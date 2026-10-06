"""Un INTENTO del nodo GIS: generar → validar → (HITL) → ejecutar → juzgar un 0 (F4 del plan de calidad).

Antes era `_attempt`, una función anidada de 294 líneas dentro de `nodes.gis_agent.run` (493) que
compartía estado por closure con `_correct` y `_notify_retry`. Ese estado (el SQL en curso, los ya
intentados, los errores) vive ahora en `IntentoSQL`, con un método por responsabilidad. Código movido
tal cual: sin cambio de comportamiento.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.retry import AttemptOutcome
from geo_copilot.security.hitl import (
    ApprovedContentMismatch,
    HITLActionType,
    HITLStatus,
    content_digest,
)

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

#: el mismo logger que el nodo: los registros siguen saliendo con su nombre
logger = get_logger("geo_copilot.orchestrator.nodes.gis_agent")


def _nodo():
    """`nodes.gis_agent` importa este módulo; sus helpers se resuelven al usarlos (sin ciclo)."""
    from geo_copilot.orchestrator.nodes import gis_agent

    return gis_agent


@dataclass
class IntentoSQL:
    graph: GeoAgentGraph
    state: GraphState
    settings: Any
    schema_info: str
    a2a_hints: str | None
    a2a_calls: list[dict]
    max_attempts: int
    autonomous_mode: bool
    hitl_interrupt_mode: bool
    #: el SQL en curso: lo reutiliza el intento siguiente tras una corrección
    current: str | None = None
    #: F2.3 (revisión adversarial): SQL ya intentados (normalizados). Si el corrector regenera algo
    #: ya probado, se para en vez de quemar intentos en mutaciones superficiales.
    seen_sql: set[str] = field(default_factory=set)
    errors_collected: list[str] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.state.get("session_id", "")

    # ------------------------------------------------------------------ 1. generar
    def _esquema_efectivo(self) -> str:
        """El esquema que ve el generador: tablas + validaciones A2A + otras fuentes + zona visible +
        feature seleccionada."""
        effective_schema: str = self.schema_info + (self.a2a_hints or "")
        # F5 (T5.1): qué OTRAS fuentes de datos hay conectadas (hecho del hub). Sin esto, con
        # «sedes educativas de Soacha» el generador contó construcciones y las llamó sedes; con
        # él puede decir que eso no está en ESTA BD (y el turno pasa al bucle, que las consulta).
        effective_schema += _nodo()._otras_fuentes()
        # Fase B: si el frontend reportó la zona visible, dásela al LLM
        # para que pueda filtrar espacialmente cuando el usuario diga
        # "en esta zona" / "aquí" / "lo que se ve".
        _mc = self.state.get("map_context") or {}
        _vp = (_mc.get("viewport") or {}) if isinstance(_mc, dict) else {}
        _bbox = _vp.get("bbox")
        if _bbox and len(_bbox) == 4:
            effective_schema += _nodo().bloque_zona_visible(_bbox)
            # F2.1: CRS métrico correcto para ESTA zona (no asumir UTM 18N).
            # Para área/distancia/buffer el LLM reproyecta a la zona que
            # corresponde a los datos visibles, no a una fija hardcodeada.
            from geo_copilot.core.spatial import SpatialReasoner
            effective_schema += "\n" + SpatialReasoner.format_for_prompt(_bbox)
        # #5 (audit 2026-06-13): inyectar la FEATURE SELECCIONADA del mapa
        # para que el LLM resuelva "el punto que seleccioné" / "esos
        # atributos". La infra ya transporta map_context end-to-end; el
        # router ya usa format_map_context, faltaba el GIS node. Pasamos solo
        # selected_feature (no layers/viewport) para no duplicar contexto.
        if isinstance(_mc, dict) and _mc.get("selected_feature"):
            from geo_copilot.core.formatters import format_map_context
            _sel_block = format_map_context(
                {"selected_feature": _mc.get("selected_feature")}
            )
            if _sel_block:
                effective_schema += "\n\n" + _sel_block
        return effective_schema

    async def _generar(self, idx: int) -> str | None:
        """El SQL del intento: el corregido, el ya generado antes de pausar (resume) o uno nuevo."""
        current_sql = self.current
        graph, session_id = self.graph, self.session_id
        # F4.2 idempotencia de resume: si en interrupt mode ya generamos el SQL
        # de ESTE intento (idx) antes de pausar, lo reusamos (NO regeneramos)
        # para que el usuario apruebe exactamente lo que vio. Cache POR idx →
        # también idempotente si el corrector generó un SQL en otro intento.
        if not current_sql and self.hitl_interrupt_mode:
            _by_idx = graph._pending_sql.get(session_id) or {}
            if _by_idx.get(idx):
                current_sql = _by_idx[idx]
                self.current = current_sql
        if not current_sql:
            current_sql = await graph.gis_agent.generate_sql_from_query(
                query=self.state["query"],
                schema_info=self._esquema_efectivo(),
                previous_sql=self.state.get("previous_sql"),
                previous_results=self.state.get("previous_results"),
                conversation_history=self.state.get("conversation_history", []),
            )
            self.current = current_sql
            # F4.2: persistir el SQL ANTES del interrupt → resume idempotente.
            if self.hitl_interrupt_mode and current_sql:
                graph._pending_sql.setdefault(session_id, {})[idx] = current_sql
        return current_sql

    # ------------------------------------------------------------------ 1.5 validar
    def _sin_sentencias(self, current_sql: str, idx: int) -> AttemptOutcome | None:
        """Regresión F0/E0.2 (TH.16): con una tabla que no existe, el generador responde SOLO con
        un comentario («-- No existe tabla "hospitales"…»). Es su respuesta honesta; con el
        validador antes del HITL (TH.9) se volvía «SQL rechazado: 0 statements». Sin sentencias,
        su explicación ES la respuesta (el texto lo decide el LLM; aquí solo se ve que no hay SQL)."""
        from geo_copilot.agents.gis_agent.sql_validator import SQLValidator

        if SQLValidator._strip_comments(current_sql).strip().strip(";").strip():
            return None
        explicacion = _nodo()._texto_de_comentarios(current_sql)
        logger.info(f"[GISAgent] El generador no escribió SQL y explicó por qué: {explicacion[:200]}")
        return AttemptOutcome(
            success=False,
            no_retry=True,
            error=None,
            payload={
                "current_agent": "gis_agent",
                "final_response": explicacion or "No hay en la base una tabla que responda esa pregunta.",
                "_sin_sql": True,  # lo consume run(): puede pasar el turno al bucle (F5)
                "retry_count": idx,
                "messages": [_nodo()._msg("Sin SQL: el generador explicó por qué no se puede consultar",
                                          success=False)],
            },
        )

    def _validar(self, current_sql: str, idx: int) -> AttemptOutcome | None:
        """V5 FH.9: el validador ANTES de pedir aprobación. Se pedía aprobar (3 veces) un SQL que el
        validador iba a rechazar al ejecutarlo; ahora el corrector lo arregla sin molestar al usuario,
        y solo se le pide aprobar lo que sí se ejecutaría."""
        from geo_copilot.agents.gis_agent.sql_validator import SQLValidator

        _veredicto = (getattr(self.graph.gis_agent, "sql_validator", None) or SQLValidator()).validate(current_sql)
        if _veredicto.get("is_valid", False):
            return None
        err = "SQL rechazado por el validador: " + "; ".join(_veredicto.get("errors", ["operación no permitida"]))
        self.errors_collected.append(err)
        logger.warning(f"[GISAgent] Attempt {idx + 1} rechazado antes de pedir aprobación: {err}")
        return AttemptOutcome(success=False, error=err)

    # ------------------------------------------------------------------ 2. HITL
    def _terminal(self, current_sql: str, idx: int, error: str, error_payload: str, mensaje: str,
                  marca: str) -> AttemptOutcome:
        """Un rechazo o una expiración: terminal, no se reintenta automáticamente."""
        return AttemptOutcome(
            success=False,
            no_retry=True,
            error=error,
            payload={
                "current_agent": "gis_agent",
                "error": error_payload,
                "sql": current_sql,
                "requires_hitl": True,
                "hitl_approved": False,
                "retry_count": idx,
                "messages": [_nodo()._msg(mensaje, success=False, data={"sql": current_sql, marca: True})],
            },
        )

    async def _aprobar(self, current_sql: str, idx: int) -> tuple[str, AttemptOutcome | None]:
        """(SQL a ejecutar, terminal o None). Gateway unificado: blocking (HITLManager) o interrupt
        (LangGraph) según hitl_mode; misma semántica downstream."""
        graph, settings, session_id = self.graph, self.settings, self.session_id
        logger.info("[GISAgent] HITL enabled - requesting approval for SQL")
        risks = _nodo().identify_sql_risks(current_sql)
        # R0.8 (AUD-15): huella del artefacto EXACTO que se presenta.
        approved_digest = content_digest(current_sql)
        from geo_copilot.orchestrator.hitl_interrupt import request_hitl_decision
        hitl_response = await request_hitl_decision(
            graph, settings,
            action_type=HITLActionType.SQL_EXECUTION,
            title="Ejecutar Consulta SQL",
            description=f"Consulta generada: {self.state['query'][:100]}...",
            details={"sql": current_sql, "query": self.state["query"]},
            risks=risks,
            # R0.8 (AUD-15): `preview` es un RESUMEN, no lo que se aprueba. El panel recibe
            # `details["sql"]` íntegro vía `content` (ver hitl.py). Antes este truncado a 500 era lo
            # único que el humano veía, mientras se ejecutaba el SQL completo.
            preview=current_sql[:500] if len(current_sql) > 500 else current_sql,
            session_id=session_id,
        )
        # F4.2 (revisión adversarial): un SQL rechazado/expirado NO debe
        # reusarse desde el cache si el usuario re-pregunta → lo descartamos.
        if self.hitl_interrupt_mode and hitl_response.status in (HITLStatus.REJECTED, HITLStatus.EXPIRED):
            graph._pending_sql.get(session_id, {}).pop(idx, None)
        if hitl_response.status == HITLStatus.REJECTED:
            feedback = hitl_response.feedback or "Sin razón especificada"
            logger.info(f"[GISAgent] SQL rejected by user: {feedback}")
            return current_sql, self._terminal(current_sql, idx, f"Consulta rechazada: {feedback}",
                                               f"Consulta rechazada: {feedback}",
                                               "Consulta rechazada por el usuario", "rejected")
        if hitl_response.status == HITLStatus.EXPIRED:
            return current_sql, self._terminal(current_sql, idx, "Tiempo de aprobación expirado.",
                                               "Tiempo de aprobación expirado. Por favor, intente de nuevo.",
                                               "Aprobación expirada", "expired")
        if hitl_response.status == HITLStatus.MODIFIED:
            current_sql = cast(str, hitl_response.modified_content)
            self.current = current_sql
            # F4.2: el cache de idempotencia refleja lo MODIFICADO por el usuario (no el SQL original generado).
            if self.hitl_interrupt_mode and current_sql:
                graph._pending_sql.setdefault(session_id, {})[idx] = current_sql
            logger.info("[GISAgent] SQL modified by user")
            # Al modificar, el artefacto autorizado pasa a ser el del usuario: la huella se recalcula sobre ÉL.
            approved_digest = content_digest(current_sql)
        # R0.8 (AUD-15): el artefacto que se va a ejecutar debe ser BIT A BIT el que se presentó (o el
        # que el usuario escribió). Si divergen, se aborta ruidosamente en vez de ejecutar algo que
        # nadie autorizó.
        if content_digest(current_sql) != approved_digest:
            raise ApprovedContentMismatch("El SQL a ejecutar no coincide con el aprobado por el usuario.")
        logger.info("[GISAgent] SQL approved, executing...")
        return current_sql, None

    # ------------------------------------------------------------------ 3.5 un 0
    async def _juzgar_vacio(self, current_sql: str, idx: int, results: list) -> tuple[Any, AttemptOutcome | None]:
        """F2.3: 0 filas → juez semántico decide real vs bug ANTES de declarar éxito. Si es bug y QUEDA
        margen de reintento, success=False con la hipótesis para que el SQLCorrector la use. Si es real
        (o ya no hay reintentos), se sigue al éxito anotando el veredicto (el responder reporta el 0
        con honestidad)."""
        if not (len(results) == 0 and self.autonomous_mode):
            return None, None
        from geo_copilot.orchestrator.result_judge import judge_empty_result
        empty_verdict = await judge_empty_result(
            query=self.state["query"], sql=current_sql, schema_info=self.schema_info, llm_client=self.graph.llm,
        )
        if empty_verdict.is_bug and idx < self.max_attempts - 1:
            err = f"0 filas — posible defecto: {empty_verdict.reason}"
            if empty_verdict.suggested_fix:
                err += f" | Corrección sugerida: {empty_verdict.suggested_fix}"
            self.errors_collected.append(err)
            logger.info(f"[GISAgent] ResultJudge: 0 filas → bug (conf={empty_verdict.confidence:.2f}); reintentando")
            return empty_verdict, AttemptOutcome(success=False, error=err)
        logger.info(f"[GISAgent] ResultJudge: 0 filas → {empty_verdict.verdict} "
                    f"(conf={empty_verdict.confidence:.2f}); reportando honesto")
        return empty_verdict, None

    # ------------------------------------------------------------------ 4. éxito
    async def _exito(self, current_sql: str, idx: int, results: list, geojson: Any, empty_verdict: Any) -> AttemptOutcome:
        """El payload del turno; notifica al cliente si hubo correcciones previas."""
        session_id = self.session_id
        if idx > 0 and session_id:
            from geo_copilot.platform import events
            await events.sink().retry_result(
                session_id=session_id, agent="gis_agent", success=True, attempts=idx + 1,
                message=f"Consulta corregida exitosamente. {len(results)} resultados.",
            )
            logger.info(f"[GISAgent] Succeeded after {idx} corrections")
        # F2.3: si 0 filas tras el juicio, anota el veredicto y un sufijo honesto
        # en el mensaje (el responder/insights lo usa para no decir "error").
        empty_note = ""
        if len(results) == 0 and empty_verdict is not None:
            if empty_verdict.is_bug:
                empty_note = (f" — 0 resultados; posible problema en la consulta "
                              f"({empty_verdict.reason}), no verificable con más reintentos")
            else:
                empty_note = " — 0 resultados, y parece ser la respuesta real"
        return AttemptOutcome(success=True, payload={
            "sql": current_sql, "raw_data": results, "geojson": geojson, "current_agent": "gis_agent",
            "requires_hitl": self.settings.hitl_enabled, "hitl_approved": True, "retry_count": idx,
            "last_error": None, "a2a_log": list(self.a2a_calls),
            # F2.3: veredicto del juez de resultados vacíos (None si hubo filas).
            "empty_result_verdict": (empty_verdict.as_dict() if empty_verdict is not None else None),
            "messages": [_nodo()._msg(
                f"SQL ejecutado: {len(results)} resultados"
                + (f" (corregido en intento {idx + 1})" if idx > 0 else "") + empty_note,
                success=True,
                data={"sql": current_sql, "row_count": len(results), "corrections": idx,
                      "a2a_calls": len(self.a2a_calls)},
            )],
        })

    # ------------------------------------------------------------------ el intento
    async def intento(self, idx: int) -> AttemptOutcome:
        """Genera (si hace falta), valida, pide HITL y ejecuta. ``no_retry=True`` en estados terminales
        (rejected/expired/sql-no-generable)."""
        current_sql = await self._generar(idx)
        if not current_sql:
            return AttemptOutcome(success=False, no_retry=True, error="Could not generate SQL", payload={
                "current_agent": "gis_agent", "error": "Could not generate SQL", "retry_count": idx,
                "messages": [_nodo()._msg("No se pudo generar SQL", success=False)]})
        logger.info(f"[GISAgent] SQL Generated (attempt {idx + 1}):\n{current_sql}")
        parada = self._sin_sentencias(current_sql, idx) or self._validar(current_sql, idx)
        if parada is not None:
            return parada
        if self.settings.hitl_enabled and (self.graph.hitl_manager or self.hitl_interrupt_mode):
            current_sql, terminal = await self._aprobar(current_sql, idx)
            if terminal is not None:
                return terminal
        try:
            results, geojson = await self.graph.gis_agent._execute_sql(current_sql)
        except Exception as exc:
            # Captura amplia a propósito: cualquier fallo de ejecución (asyncpg, timeout,
            # validación, GeoJSON) alimenta al SQLCorrector.
            err = _nodo()._describir_fallo(exc)
            self.errors_collected.append(err)
            logger.error(f"[GISAgent] Attempt {idx + 1} error: {err}", exc_info=True)
            return AttemptOutcome(success=False, error=err)
        empty_verdict, reintento = await self._juzgar_vacio(current_sql, idx, results)
        if reintento is not None:
            return reintento
        return await self._exito(current_sql, idx, results, geojson, empty_verdict)

    # ------------------------------------------------------------------ corregir / notificar
    async def corregir(self, error_msg: str, idx: int) -> bool:
        """El SQLCorrector propone un SQL alternativo. True si hubo una corrección útil (el
        RetryExecutor reintenta); False si no, y el executor sale con el último error."""
        from geo_copilot.platform import events
        try:
            corrector = _nodo().SQLCorrector(self.graph.llm)  # el nodo es el punto de inyección (pruebas)
            corrected_sql = await corrector.correct_sql(
                sql=self.current, error=error_msg, schema=self.schema_info, query=self.state["query"],
            )
        except Exception as exc:
            # Captura amplia a propósito: llamada LLM del corrector; si falla se corta el reintento
            # con el último error.
            logger.error(f"[GISAgent] Correction failed: {exc}", exc_info=True)
            return False

        def _norm(s: str | None) -> str:
            return " ".join((s or "").split()).lower()

        self.seen_sql.add(_norm(self.current))  # el SQL actual ya fue intentado
        if corrected_sql and _norm(corrected_sql) not in self.seen_sql:
            logger.info("[GISAgent] SQL corrected, retrying...")
            if self.session_id:
                await events.sink().retry_correction(
                    session_id=self.session_id, agent="gis_agent", correction_type="sql",
                    original=self.current, corrected=corrected_sql,
                )
            self.current = corrected_sql
            self.seen_sql.add(_norm(corrected_sql))
            return True
        # Sin corrección útil o ya intentada → no reintentar (corta loops).
        logger.warning("[GISAgent] corrección nula o ya intentada, deteniendo reintentos")
        return False

    async def notificar_reintento(self, next_attempt: int, total: int, last_error: str) -> None:
        """Solo notifica al cliente que se está reintentando."""
        if not self.session_id:
            return
        from geo_copilot.platform import events
        await events.sink().retry_started(
            session_id=self.session_id, agent="gis_agent", attempt=next_attempt, max_attempts=total,
            error=last_error, action="Corrigiendo consulta SQL...",
        )
