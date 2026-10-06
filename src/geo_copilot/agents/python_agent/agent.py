"""
PythonAgent - Agente para operaciones espaciales sobre datos en memoria.

Genera código Python y lo ejecuta en un sandbox seguro para realizar
operaciones espaciales sobre datos externos (buffer, intersect, etc.)
"""


from typing import Any, cast

from geo_copilot.agents.base import AgentResponse, BaseAgent
from geo_copilot.agents.gis_agent.sandbox import PythonSandbox
from geo_copilot.core.config import settings
from geo_copilot.core.llm_client import LLMClient
from geo_copilot.core.logging import get_logger
from geo_copilot.security.hitl import (
    ApprovedContentMismatch,
    HITLActionType,
    HITLManager,
    HITLStatus,
    content_digest,
)

logger = get_logger(__name__)

# F4: la validación, la generación, el juez y la ejecución viven en sus módulos (mixins).
from geo_copilot.agents.python_agent.ejecucion import (  # noqa: F401
    EjecucionMixin,
    _bloque_datasets,
    _datasets_de_la_sesion,
    _exportar_usados,
)
from geo_copilot.agents.python_agent.generacion import GeneracionMixin
from geo_copilot.agents.python_agent.juez import JuezMixin
from geo_copilot.agents.python_agent.validacion import ValidacionMixin


def _no_aprobado(hitl_response: Any, code: str) -> AgentResponse:
    """La respuesta honesta cuando el código NO se aprobó (rechazado, expirado u otro estado)."""
    if hitl_response.status == HITLStatus.REJECTED:
        motivo = hitl_response.feedback or "Sin razón especificada"
        mensaje = f"Operación rechazada: {motivo}"
    elif hitl_response.status == HITLStatus.EXPIRED:
        mensaje = (
            "Tiempo de aprobación expirado. El código no se ejecutó; "
            "por favor, intente de nuevo."
        )
    else:
        # PENDING, o cualquier estado que se añada después.
        mensaje = (
            "La operación no fue aprobada "
            f"(estado: {hitl_response.status.value})."
        )
    return AgentResponse(
        success=False,
        message=mensaje,
        data={
            "code": code,
            "approved": False,
            "hitl_status": hitl_response.status.value,
        },
    )



def _mensaje_de_exito(warning: str | None, feature_count: int, table: Any, stats: Any, chart: Any,
                      result_geojson: dict | None, is_analysis: bool) -> str:
    """Un mensaje honesto según lo que produjo el código."""
    if warning == "result_empty":
        msg = (
            "Operación completada pero no quedaron features en el "
            "resultado (posible filtro muy estricto o intersección vacía)."
        )
    elif warning and warning.startswith("repaired_"):
        repaired = warning.split("_")[1]
        msg = (
            f"Operación completada: {feature_count} features. "
            f"Se repararon {repaired} geometrías inválidas con make_valid()."
        )
    elif is_analysis:
        partes = []
        if chart is not None:
            partes.append("gráfico")
        if table is not None:
            partes.append(f"tabla ({len(table)} filas)")
        if stats is not None:
            partes.append("estadísticas")
        if result_geojson:
            partes.append(f"{feature_count} features")
        msg = "Análisis completado: " + ", ".join(partes) + "."
    else:
        msg = f"Operación completada: {feature_count} features resultantes"
    return msg


class PythonAgent(ValidacionMixin, GeneracionMixin, JuezMixin, EjecucionMixin, BaseAgent):
    """
    Agente para operaciones espaciales sobre datos en memoria.

    Genera código Python basado en lenguaje natural y lo ejecuta
    en un sandbox seguro para procesar datos externos (GeoJSON).

    Capacidades:
    - Buffer, Union, Intersection, Difference
    - Filtrado por atributos
    - Cálculos de área, distancia, centroide
    - Agregaciones espaciales
    - Transformaciones de coordenadas
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        hitl_manager: HITLManager | None = None,
        sandbox_timeout: int | None = None,
    ):
        super().__init__(
            name="PythonAgent",
            description="Agente para operaciones espaciales sobre datos en memoria"
        )

        self.llm_client = llm_client or LLMClient.from_settings(settings)
        self.hitl_manager = hitl_manager or HITLManager(timeout=settings.hitl_timeout)
        # Usar timeout de settings si no se especifica
        timeout = sandbox_timeout if sandbox_timeout is not None else settings.sandbox_timeout
        self.sandbox = PythonSandbox(
            timeout=timeout,
            hitl_manager=self.hitl_manager
        )

        self._register_tools()

    def _register_tools(self) -> None:
        """Registrar herramientas del agente."""
        self.register_tool(
            "generate_code",
            self.generate_code,
            "Generar código Python para una operación espacial"
        )
        self.register_tool(
            "execute_code",
            self.execute_code,
            "Ejecutar código Python en el sandbox"
        )
        self.register_tool(
            "analyze_geojson",
            self.analyze_geojson,
            "Analizar estructura de un GeoJSON"
        )

    # ==========================================================================
    # A2A — capacidades públicas para que otros agentes consulten al PythonAgent.
    # ==========================================================================

    def get_capabilities(self) -> dict:
        """Retornar capacidades del agente."""
        return {
            "operations": [
                "buffer",
                "centroid",
                "area",
                "union",
                "intersect",
                "clip",
                "dissolve",
                "convex_hull",
                "bounding_box",
                "distance",
                "simplify",
                "filter",
            ],
            "input_formats": ["geojson"],
            "output_formats": ["geojson"],
            "max_features": 10000,
        }

    async def process(
        self,
        query: str,
        context: dict | None = None
    ) -> AgentResponse:
        """
        Procesar una solicitud de operación espacial.

        Args:
            query: Consulta en lenguaje natural
            context: Contexto con external_geojson, etc.

        Returns:
            AgentResponse con el GeoJSON resultante
        """
        context = context or {}
        session_id = context.get("session_id")

        logger.info(f"[PythonAgent] Processing: {query[:100]}...")

        # S2.4: datasets del workspace de la sesión, disponibles como
        # `datasets["nombre"]` además de gdf/gdf2 (análisis sobre N capas).
        workspace = await _datasets_de_la_sesion(session_id)

        geojson, rechazo = self._capa_de_trabajo(context, workspace)
        if rechazo is not None:
            return rechazo
        assert geojson is not None

        # Log para debugging
        feature_count = len(geojson.get("features", []))

        # Segunda capa cargada (cross-source): el node la pasa cuando hay OTRA
        # capa en el mapa además de la activa (p.ej. una de la BD y otra REST).
        # Llega al sandbox como ``gdf2`` para cruces espaciales.
        secondary_geojson = context.get("secondary_geojson")
        secondary_name = context.get("secondary_source_name")

        # Validar que tenga features
        features = geojson.get("features", [])
        if not features and not workspace:
            return AgentResponse(
                success=False,
                message="El GeoJSON no contiene features para procesar.",
                data=None
            )

        # 2. Analizar el GeoJSON (y la segunda capa si existe, para el prompt gdf2)
        geojson_info = self._analyze_geojson_structure(geojson)
        secondary_info = (
            self._analyze_geojson_structure(secondary_geojson)
            if secondary_geojson and secondary_geojson.get("features")
            else None
        )
        if secondary_info:
            logger.info(
                "[PythonAgent] Segunda capa disponible como gdf2: %s (%d features)",
                secondary_name or "capa 2", secondary_info.get("count", 0),
            )

        code = await self._codigo(query, context, geojson_info, secondary_info, geojson,
                                  secondary_geojson, workspace)

        if not code:
            return AgentResponse(
                success=False,
                message="No se pudo generar código para la operación solicitada.",
                data=None
            )

        code, no_aprobado = await self._aprobar_codigo(code, query, features, session_id)
        if no_aprobado is not None:
            return no_aprobado

        return await self._resultado(query, context, code, geojson, secondary_geojson, workspace,
                                     features, geojson_info, session_id)

    @staticmethod
    def _capa_de_trabajo(context: dict, workspace: dict) -> tuple[dict | None, AgentResponse | None]:
        """El GeoJSON sobre el que opera el código (o el rechazo honesto si no hay ninguno)."""
        # 1. Obtener GeoJSON según la fuente de datos activa
        active_source = context.get("active_data_source", "none")
        geojson = None
        source_description = ""

        if active_source == "external":
            geojson = context.get("external_geojson")
            source_description = context.get("external_source_name", "datos externos")
            logger.info(f"[PythonAgent] Using EXTERNAL data: {source_description}")
        elif active_source in ("internal", "previous"):
            # R4.2: el Smart Router emite active_source='previous' cuando la
            # capa es la HEREDADA del turno anterior (el node la pone en
            # context['geojson'/'last_geojson']). Antes esta rama solo aceptaba
            # 'internal' y 'previous' caía al rechazo "sin fuente activa" — la
            # ruta estaba muerta end-to-end.
            geojson = context.get("geojson") or context.get("last_geojson")
            source_description = context.get(
                "active_source_name",
                "capa cargada previamente" if active_source == "previous"
                else "base de datos interna",
            )
            logger.info(
                f"[PythonAgent] Using {active_source.upper()} data: {source_description}"
            )
        elif not workspace:
            # Sin active_source NO adivinamos. Antes había fallback que tomaba
            # el primer GeoJSON que encontrara — usaba datos viejos sin avisar.
            return None, AgentResponse(
                success=False,
                message=(
                    "No hay una fuente de datos activa. Antes de operaciones "
                    "espaciales (buffer, intersección, etc.) carga primero datos "
                    "con 'busca [tema]' o consulta la base de datos."
                ),
                data=None,
            )

        if not geojson and not workspace:
            return None, AgentResponse(
                success=False,
                message=f"La fuente activa ({active_source}) no tiene GeoJSON cargado.",
                data=None,
            )
        # S2.4: sin capa activa en memoria (p.ej. una capa grande que se quedó en
        # el workspace) el código trabaja solo con `datasets`; gdf es None.
        return geojson or {"type": "FeatureCollection", "features": []}, None

    async def _codigo(self, query: str, context: dict, geojson_info: dict, secondary_info: dict | None,
                      geojson: dict, secondary_geojson: dict | None, workspace: dict) -> str | None:
        """El código a ejecutar: el corregido si viene de la auto-corrección, si no el del LLM."""
        # 3. Generar código Python usando LLM (o usar código corregido si viene de auto-corrección)
        corrected_code = context.get("_use_corrected_code")
        code: str | None
        if corrected_code:
            code = corrected_code
            logger.info("[PythonAgent] Using corrected code from auto-correction")
        else:
            code = await self._generate_code(
                query=query,
                geojson_info=geojson_info,
                secondary_info=secondary_info,
                # A4: los GeoJSON crudos para el perfil de datos del prompt.
                geojson=geojson,
                secondary_geojson=secondary_geojson,
                workspace=workspace,
            )
        if code:
            logger.info(f"[PythonAgent] {'Corrected' if corrected_code else 'Generated'} code:\n{code}")
        return code

    async def _aprobar_codigo(self, code: str, query: str, features: list,
                              session_id: str | None) -> tuple[str, AgentResponse | None]:
        """La puerta HITL del código: (lo que se ejecuta —el aprobado o el que escribió el usuario—,
        None) o (code, la respuesta de NO aprobado). Una ALLOWLIST: el próximo estado falla cerrado."""
        if settings.hitl_enabled and self.hitl_manager:
            # R0.8 (AUD-15): huella del artefacto EXACTO que se presenta. La
            # ruta SQL ya lo hacía (orchestrator/nodes/gis_agent.py); esta se
            # había quedado fuera, y es la que ejecuta código arbitrario.
            approved_digest = content_digest(code)
            hitl_response = await self.hitl_manager.request_approval(
                action_type=HITLActionType.CODE_EXECUTION,
                title="Ejecutar código Python",
                description=f"Operación espacial: {query[:50]}...",
                details={
                    "code": code,
                    "input_features": len(features),
                    "query": query,
                },
                risks=[
                    f"Procesar {len(features)} features",
                    "Operación espacial puede tomar tiempo",
                ],
                preview=code,
                session_id=session_id
            )

            # El gate es una ALLOWLIST, no una lista de rechazos. Antes esto
            # comprobaba REJECTED y MODIFIED, y todo lo demás caía al paso 6:
            # un EXPIRED —que `request_approval` devuelve al vencer
            # `hitl_timeout`, 300 s por defecto— ejecutaba el código del LLM
            # sin que nadie lo hubiera aprobado. Bastaba con no contestar.
            # Escrito así, el próximo estado nuevo también falla CERRADO.
            if hitl_response.status == HITLStatus.MODIFIED:
                code = cast(str, hitl_response.modified_content)
                logger.info("[PythonAgent] Code modified by user")
                # Al modificar, el artefacto autorizado pasa a ser el del
                # usuario: la huella se recalcula sobre ÉL.
                approved_digest = content_digest(code)
            elif hitl_response.status != HITLStatus.APPROVED:
                logger.info(f"[PythonAgent] Code NOT approved: {hitl_response.status.value}")
                return code, _no_aprobado(hitl_response, code)

            # R0.8 (AUD-15): lo que se va a ejecutar debe ser BIT A BIT lo que
            # se presentó (o lo que el usuario escribió). Si divergen, abortamos
            # ruidosamente en vez de ejecutar algo que nadie autorizó.
            if content_digest(code) != approved_digest:
                raise ApprovedContentMismatch(
                    "El código a ejecutar no coincide con el aprobado por el usuario."
                )
            logger.info("[PythonAgent] Code approved, executing...")
        return code, None

    async def _exito(self, query: str, context: dict, code: str, result: dict, features: list,
                     geojson_info: dict) -> AgentResponse:
        """La respuesta de una ejecución que funcionó, con lo que produjo, verificada por el juez."""
        result_geojson = result.get("result_geojson")
        table = result.get("table")
        stats = result.get("stats")
        chart = result.get("chart")
        stdout = result.get("stdout")
        feature_count = len(result_geojson.get("features", [])) if result_geojson else 0
        warning = result.get("warning")
        # R4.3: presencia (is not None), no truthiness — table=[] es un
        # análisis legítimo con 0 filas, no un fallo.
        is_analysis = any(x is not None for x in (table, stats, chart))

        msg = _mensaje_de_exito(warning, feature_count, table, stats, chart, result_geojson,
                                is_analysis)

        response = AgentResponse(
            success=True,
            message=msg,
            data={
                "geojson": result_geojson,
                "table": table,
                "stats": stats,
                "chart": chart,
                "stdout": stdout,
                "code": code,
                "operation": "analysis" if is_analysis else "spatial_operation",
                "execution_time": result.get("execution_time"),
                "original_count": len(features),
                "result_count": feature_count,
                "warning": warning,
            }
        )
        return await self._verificada(response, query, context, code, geojson_info, result_geojson,
                                      table, stats, chart, feature_count)

    async def _verificada(self, response: AgentResponse, query: str, context: dict, code: str,
                          geojson_info: dict, result_geojson: dict | None, table: Any, stats: Any,
                          chart: Any, feature_count: int) -> AgentResponse:
        """La respuesta, o UN reintento dirigido si el juez ligero dice que no responde lo pedido."""
        # A4(b): auto-verificación LIGERA del resultado — "¿esta salida
        # responde la solicitud?" (1 llamada corta). Si NO responde (y
        # aún no reintentamos por esto), UNA corrección dirigida por la
        # razón del juez y re-ejecución. Best-effort: si el juez falla,
        # el resultado exitoso se entrega tal cual.
        if not context.get("_a4_verified_retry"):
            verdict = await self._judge_output_responds(
                query=query, result_geojson=result_geojson,
                table=table, stats=stats, chart=chart,
                feature_count=feature_count,
            )
            if verdict is not None and not verdict.get("responds", True):
                reason = verdict.get("reason", "el resultado no responde la solicitud")
                logger.info(
                    f"[PythonAgent] A4 self-check: resultado NO responsivo "
                    f"({reason}) — 1 reintento dirigido"
                )
                retry = await self._retry_with_judge_reason(
                    query=query, context=context, code=code,
                    reason=reason, geojson_info=geojson_info,
                )
                if retry is not None and retry.success:
                    return retry
        return response

    async def _resultado(self, query: str, context: dict, code: str, geojson: dict,
                         secondary_geojson: dict | None, workspace: dict, features: list,
                         geojson_info: dict, session_id: str | None) -> AgentResponse:
        """Ejecutar en el sandbox y responder con lo que produjo (y el juez ligero de la salida)."""
        # 6. Ejecutar en sandbox
        try:
            manifiesto = await _exportar_usados(session_id, code, workspace)
            result = await self._execute_in_sandbox(
                code, geojson, secondary_geojson, datasets=manifiesto,
            )

            if result.get("success"):
                return await self._exito(query, context, code, result, features, geojson_info)
            else:
                return AgentResponse(
                    success=False,
                    message=f"Error en ejecución: {result.get('error', 'Error desconocido')}",
                    data={
                        "code": code,
                        "error": result.get("error"),
                        "traceback": result.get("traceback"),
                    }
                )

        except Exception as e:  # captura amplia a propósito: frontera del agente: sandbox + LLM juez/reintento; el fallo se devuelve como AgentResponse
            logger.error(f"[PythonAgent] Execution error: {e}", exc_info=True)
            return AgentResponse(
                success=False,
                message=f"Error ejecutando código: {str(e)}",
                data={"code": code, "error": str(e)}
            )

    # =========================================================================
    # Herramientas expuestas
    # =========================================================================


# ---------------------------------------------------------------------------
# S2.4 — datasets del workspace en el sandbox
# ---------------------------------------------------------------------------
