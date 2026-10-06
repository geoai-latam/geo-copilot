"""Un INTENTO del nodo Python: generar/ejecutar en el sandbox → éxito, rechazo o error a corregir.

F4 del plan de calidad: antes era `_attempt` (153 líneas), anidada en `nodes.python_agent.run` (330)
con `_correct` y `_notify_retry`, compartiendo el código en curso por closure (`code_holder`). Ese
estado vive en `IntentoPython`, con un método por responsabilidad. Código movido tal cual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.retry import AttemptOutcome

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

#: el mismo logger que el nodo: los registros siguen saliendo con su nombre
logger = get_logger("geo_copilot.orchestrator.nodes.python_agent")


def _nodo():
    """`nodes.python_agent` importa este módulo; sus helpers (y `CodeCorrector`, que las pruebas
    sustituyen ahí) se resuelven al usarlos."""
    from geo_copilot.orchestrator.nodes import python_agent

    return python_agent


def _canal_analitico(payload: dict, data: dict, mensaje: str) -> None:
    """Salidas ANALÍTICAS (chart/table/stats) → canal de visualización que query.py convierte en
    `visualizations[]` para el frontend (reusa Chart.tsx / la tabla; sin código nuevo de render). Una
    pregunta analítica puede no tener geometría: el mapa queda vacío y el resultado se ve como gráfico
    o tabla."""
    chart, table, stats = data.get("chart"), data.get("table"), data.get("stats")
    if isinstance(chart, dict) and chart.get("data"):
        payload["visualization"] = {
            "type": "chart", "chart_type": chart.get("chart_type", "bar"),
            "x_axis": chart.get("x"), "y_axis": chart.get("y"),
        }
        payload["data"] = {"results": chart["data"]}
    elif table is not None:
        # R4.3 (completo): presencia, no truthiness — una tabla legítimamente VACÍA sigue siendo un
        # resultado analítico (se muestra con 0 filas); con `elif table:` el canal analítico se perdía y
        # el finalizer marcaba "no produjo output útil".
        payload["visualization"] = {"type": "table"}
        payload["data"] = {"results": table}
    elif isinstance(stats, dict) and stats:
        # stats dict → tabla métrica/valor para mostrarlo legible.
        payload["visualization"] = {"type": "table"}
        payload["data"] = {"results": [{"métrica": k, "valor": v} for k, v in stats.items()]}
    if chart or table or stats:
        # Mensaje honesto del agente analítico (no "N features").
        payload["messages"][0]["content"] = mensaje
    if extras := _analiticos_extra(chart, table, stats):
        payload["analiticos_extra"] = extras  # el bucle ReAct los acumula (bucle_react)
        # grafo clásico: la respuesta sale de `analiticos` (el último es el que viaja en `data`)
        payload["analiticos"] = [*extras, {"data": payload["data"], "visualization": payload["visualization"]}]


def _analiticos_extra(chart: Any, table: Any, stats: Any) -> list[dict]:
    """Las salidas analíticas que NO van en `data` (solo cabe una). V5 (auditoría F4): con gráfico +
    tabla, `data` llevaba los puntos del gráfico y la tabla completa («número de lotes Y área total»)
    se perdía; el agente no la veía y el usuario tampoco."""
    extras: list[dict] = []
    con_grafico = isinstance(chart, dict) and bool(chart.get("data"))
    if con_grafico and table is not None:
        extras.append({"data": {"results": table}, "visualization": {"type": "table"}})
    if (con_grafico or table is not None) and isinstance(stats, dict) and stats:
        extras.append({"data": {"results": [{"métrica": k, "valor": v} for k, v in stats.items()]},
                       "visualization": {"type": "table"}})
    return extras


@dataclass
class IntentoPython:
    graph: GeoAgentGraph
    state: GraphState
    working_geojson: dict
    source_name: str
    active_source: str
    secondary_geojson: dict | None
    secondary_name: str | None
    columns: list[str]
    feature_count: int
    #: el último código que ejecutó (o intentó), la corrección para el siguiente intento y el
    #: traceback del último error (lo que lee el corrector)
    original: str | None = None
    corrected: str | None = None
    traceback: str | None = None
    errors_collected: list[str] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.state.get("session_id", "")

    def _contexto(self) -> dict[str, Any]:
        state, working = self.state, self.working_geojson
        context = {
            "external_geojson": working if self.active_source == "external" else None,
            "geojson": working if self.active_source == "internal" else state.get("geojson"),
            "last_geojson": working,
            "session_id": state.get("session_id"),
            "active_data_source": self.active_source,
            "active_source_name": state.get("active_source_name") or self.source_name,
            "external_source_name": state.get("external_source_name"),
            "secondary_geojson": self.secondary_geojson,
            "secondary_source_name": self.secondary_name,
        }
        if self.corrected:
            context["_use_corrected_code"] = self.corrected
            # consumida — el siguiente intento generaría una corrección nueva si vuelve a fallar.
            self.corrected = None
        return context

    async def intento(self, idx: int) -> AttemptOutcome:
        response = await self.graph.python_agent.process(query=self.state["query"], context=self._contexto())
        if response.success and response.data:
            return await self._exito(idx, response)
        # LIVE-02: un RECHAZO del usuario NO es un error de código a auto-corregir. Antes se devolvía como
        # fallo genérico → el RetryExecutor invocaba al corrector, que REGENERABA el código y volvía a pedir
        # aprobación ("cada rechazo produce una versión nueva"; solo Cancelar paraba el bucle). Un rechazo
        # es TERMINAL, igual que el guardrail del SQL en gis_agent: no_retry corta aquí con honestidad.
        error_msg = response.message or "Error desconocido"
        if response.data and response.data.get("rejected"):
            logger.info("[PythonAgent] código rechazado por el usuario — terminal (sin regenerar)")
            _rej_code = response.data.get("code")
            return AttemptOutcome(success=False, no_retry=True, error=error_msg, payload={
                "current_agent": "python_agent", "error": error_msg, "python_code": _rej_code,
                "requires_hitl": True, "hitl_approved": False, "retry_count": idx,
                "messages": [_nodo()._msg("Operación rechazada por el usuario", success=False,
                                          data={"code": _rej_code, "rejected": True})],
            })
        # Fallo: capturar para el corrector.
        self.errors_collected.append(error_msg)
        if response.data:
            self.original = response.data.get("code")
            self.traceback = response.data.get("traceback") or error_msg
        else:
            self.original = None
            self.traceback = error_msg
        logger.error(f"[PythonAgent] Attempt {idx + 1} error: {error_msg}")
        return AttemptOutcome(success=False, error=error_msg)

    async def _exito(self, idx: int, response: Any) -> AttemptOutcome:
        result_geojson = response.data.get("geojson")
        result_count = response.data.get("result_count", 0)
        if idx > 0 and self.session_id:
            from geo_copilot.platform import events
            await events.sink().retry_result(
                session_id=self.session_id, agent="python_agent", success=True, attempts=idx + 1,
                message=f"Código corregido exitosamente. {result_count} features.",
            )
            logger.info(f"[PythonAgent] Succeeded after {idx} corrections")
        active_source = self.active_source
        # Smart Router: si la fuente fue ``previous`` (capa heredada del turno anterior), la marcamos
        # como ``internal`` en el state propagado — el state solo conoce {internal, external, none}. El
        # responder/cliente reciben el geojson modificado como capa interna nueva.
        propagated_source = "internal" if active_source in ("internal", "previous") else "external"
        payload: dict = {
            "current_agent": "python_agent",
            "geojson": result_geojson,
            "raw_data": result_geojson.get("features", []) if result_geojson else [],
            "python_code": response.data.get("code"),
            "python_output": response.data,
            "retry_count": idx,
            "last_error": None,
            "active_data_source": propagated_source,
            "active_source_name": self.source_name,
            # V5 en Chrome: sin esto la capa nueva se llamaba como la pregunta
            # («calcula el área de cada lote en metros cuadrados»).
            "layer_name": _nodo()._nombre_resultado(self.source_name, self.state.get("operacion_espacial")
                                                    or (response.data or {}).get("operation")),
            "messages": [_nodo()._msg(
                f"Operación completada: {result_count} features resultantes"
                + (f" (corregido en intento {idx + 1})" if idx > 0 else ""),
                success=True,
                data={"operation": response.data.get("operation"), "result_count": result_count,
                      "original_count": response.data.get("original_count"), "corrections": idx,
                      "data_source": active_source},
            )],
        }
        if active_source == "external":
            payload["external_geojson"] = result_geojson
            payload["has_external_data"] = True
        else:
            payload["has_external_data"] = False
            # Si veníamos de ``previous`` también limpiamos external_geojson para evitar contaminación cruzada.
            if active_source == "previous":
                payload["external_geojson"] = None
        _canal_analitico(payload, response.data, response.message)
        return AttemptOutcome(success=True, payload=payload)

    async def corregir(self, _error_msg: str, _idx: int) -> bool:
        original_code = self.original
        if not original_code:
            logger.warning("[PythonAgent] No code to correct, stopping retries")
            return False
        try:
            corrector = _nodo().CodeCorrector(self.graph.llm)
            new_code = await corrector.correct_code(
                code=original_code, error=self.traceback or "", columns=self.columns,
                feature_count=self.feature_count,
                # R1.3: el corrector conoce la intención original — sin esto corregía "hacia cualquier
                # cosa que no diera error".
                query=self.state.get("query", ""),
            )
        except Exception as exc:
            # Captura amplia a propósito: llamada LLM del corrector; si falla se corta el reintento
            # con el último error.
            logger.error(f"[PythonAgent] Correction failed: {exc}", exc_info=True)
            return False
        if not new_code or new_code == original_code:
            logger.warning("[PythonAgent] Could not correct code, stopping retries")
            return False
        logger.info("[PythonAgent] Code corrected, retrying...")
        if self.session_id:
            from geo_copilot.platform import events
            await events.sink().retry_correction(
                session_id=self.session_id, agent="python_agent", correction_type="code",
                original=original_code, corrected=new_code,
            )
        self.corrected = new_code
        return True

    async def notificar_reintento(self, next_attempt: int, total: int, last_error: str) -> None:
        if not self.session_id:
            return
        from geo_copilot.platform import events
        await events.sink().retry_started(
            session_id=self.session_id, agent="python_agent", attempt=next_attempt, max_attempts=total,
            error=last_error, action="Corrigiendo código Python...",
        )
