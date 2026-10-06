"""
Router node — extraído de ``graph.py`` en Fase 6 #6.

Primer nodo del grafo. Pregunta al ``RouterAgent`` qué hacer con la
consulta (responder directamente, ir a BD, búsqueda externa, plan
multi-paso, ...) y normaliza la respuesta a una actualización de
estado.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from geo_copilot.core.colors import log_agent
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


def _sandbox_available() -> bool:
    """F1.1: ¿corre el sandbox de Python en esta plataforma? (POSIX)."""
    try:
        from geo_copilot.agents.gis_agent.sandbox import SANDBOX_AVAILABLE
        return bool(SANDBOX_AVAILABLE)
    except Exception:  # noqa: BLE001
        return True  # ante la duda, no restringir


def _servicios_conectados() -> str:
    """F3: resumen del hub MCP para el router ("" = ninguno enchufado)."""
    from geo_copilot.platform.mcp.hub import hub_actual

    hub = hub_actual()
    return hub.resumen_prompt() if hub is not None else ""


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:  # noqa: PLR0915
    """Delegar al RouterAgent y normalizar su respuesta."""
    log_agent("RouterAgent", "Analizando intención...", f"Query: {state['query'][:80]}...")
    logger.info(f"[Router] Processing: {state['query'][:50]}...")

    schema_info = await graph._get_cached_schema()
    external_geojson = state.get("external_geojson") or {}

    # Smart Router (2026-05-31): si el active_data_source dice "internal"
    # pero ``state.geojson`` está vacío (turno fresh), miramos el
    # ``previous_geojson`` heredado de la sesión — es la capa que el
    # usuario sigue viendo en el mapa.
    active_source = state.get("active_data_source", "none")
    active_geojson = None
    if active_source == "internal":
        active_geojson = state.get("geojson") or state.get("previous_geojson")
    elif active_source == "external":
        active_geojson = external_geojson or None
    elif active_source == "none" and state.get("previous_geojson"):
        # Sin source explícita pero hay capa previa → tratarla como interna
        # heredada. El prompt sabrá que es "CAPA HEREDADA".
        active_geojson = state.get("previous_geojson")
        active_source = "previous"

    active_features = (active_geojson or {}).get("features") if active_geojson else []
    active_feature_count = len(active_features) if active_features else 0

    active_geometry_type: str | None = None
    active_field_names: list[str] | None = None
    if active_features:
        first = active_features[0] or {}
        geom = first.get("geometry") or {}
        active_geometry_type = geom.get("type")
        props = first.get("properties") or {}
        active_field_names = sorted(props.keys()) if props else None

    context = {
        "conversation_history": state.get("conversation_history", []),
        "previous_sql": state.get("previous_sql"),
        "previous_results": state.get("previous_results"),
        "found_services": state.get("found_services", []),
        "schema_info": schema_info,
        "has_external_data": state.get("has_external_data", False),
        "external_source_name": state.get("external_source_name"),
        "external_feature_count": len(external_geojson.get("features", [])),
        "active_data_source": active_source,
        "active_source_name": state.get("active_source_name"),
        # Smart Router context — el LLM ve la forma de la capa activa
        # y sabe que NO debe pedir SQL nuevo para ajustarla.
        "active_feature_count": active_feature_count,
        "active_geometry_type": active_geometry_type,
        "active_field_names": active_field_names,
        # Fase A: estado real del mapa que reporta el frontend (capas
        # cargadas, feature seleccionada, viewport).
        "map_context": state.get("map_context"),
        # F2.2: región de sesión → el router rinde las "fuentes externas"
        # según la región (neutral si es global/desconocida), sin fijar Colombia.
        "session_region": state.get("session_region"),
        # F1.1: autoconocimiento de plataforma — si el sandbox no corre aquí
        # (Windows), el router lo sabe ANTES de decidir y propone PostGIS.
        "sandbox_available": _sandbox_available(),
        # F3: qué servicios MCP hay enchufados (y en qué estado) ANTES de
        # elegir el intent (mismo principio F1.1 que sandbox_available).
        "connected_services": _servicios_conectados(),
    }

    response = await graph.router_agent.process(query=state["query"], context=context)

    # C3a-2: exigir ``intent`` en el guard. Antes se hacía
    # ``data["intent"]`` con subíndice duro dentro de la rama de éxito; si
    # el RouterAgent devolvía success=True sin ``intent``, el KeyError
    # escapaba el nodo (no se capturaba aquí). Ahora la ausencia de intent
    # cae al fallback de error como cualquier otra respuesta inválida.
    if response.success and response.data and response.data.get("intent"):
        data = response.data
        intent = data["intent"]
        reasoning = data.get("reasoning", "")
        direct_response = data.get("direct_response")
        external_url = data.get("external_url")
        entities = data.get("entities", [])
        is_complex_query = data.get("is_complex_query", False)
        additional_operations = data.get("additional_operations", [])

        # Autonomía: si el LLM marcó la query como simple pero detectó
        # >=2 operaciones, forzamos multi-step para que el planner las
        # encadene correctamente.
        if additional_operations and not is_complex_query and len(additional_operations) >= 2:
            is_complex_query = True
            logger.info("[Router] Overriding to complex due to multiple operations")

        # R5.1: ELIMINADO el override incondicional feature-seleccionada →
        # query_data (#2/P1-D). Pisaba juicios legítimos del LLM: cualquier
        # follow_up ("¿qué campos tenían?") con una feature clicada colgada
        # disparaba SQL innecesario, sin verificar que la pregunta se refiriera
        # a esa feature. La regla vive ahora SOLO en el prompt (prompts.py,
        # sección FEATURE SELECCIONADA) donde el LLM la aplica CON contexto —
        # y hay tests con LLM real cubriendo ambos lados (pregunta sobre la
        # feature → query_data; follow_up genérico → follow_up).

        logger.info(
            f"[Router] Decision: {intent}, Complex: {is_complex_query}, "
            f"AdditionalOps: {additional_operations}, "
            f"Reason: {reasoning[:100] if reasoning else 'N/A'}"
        )

        # Degradación honesta de 'analyze' sin capa: el edge _route_from_router
        # lo degrada a data_agent (traer datos) en vez de analizar. Como el edge
        # condicional de LangGraph NO persiste mutaciones de estado, marcamos el
        # flag AQUÍ (el router NODE sí persiste su update) para que el responder
        # avise en vez de mostrar datos en silencio. Misma lógica de capa activa
        # que el edge.
        # H23 (V5 F2): una capa del workspace demasiado grande para hidratarse ES una capa.
        from geo_copilot.orchestrator.layer_resolution import hay_capa_vectorial

        has_active_layer = hay_capa_vectorial(state)
        analyze_degraded = intent == "analyze" and not has_active_layer
        # A2 (hybrid): si la política manda este caso al bucle ReAct, NO es una
        # degradación — el bucle trae los datos Y ejecuta el análisis en el
        # mismo turno. El aviso del responder solo aplica al camino cableado.
        if analyze_degraded:
            from geo_copilot.core.config import get_settings
            from geo_copilot.orchestrator.graph import _resolve_react_policy
            _settings = get_settings()
            if (
                _resolve_react_policy(_settings) == "hybrid"
                and getattr(_settings, "hitl_mode", "blocking") != "interrupt"
            ):
                analyze_degraded = False
        if analyze_degraded:
            logger.info("[Router] analyze sin capa activa → degradación honesta marcada")

        # ORQ-12 (auditoría): spatial_operation / apply_symbology sin capa activa.
        # Antes spatial_operation caía a "Consulta procesada." (responder sin
        # final_response) y apply_symbology se degradaba a generación de SQL
        # (pedía SQL a partir de "ponlo rojo"). Marcamos un mensaje HONESTO aquí
        # (el router NODE persiste su update) — en paralelo al trato de analyze.
        op_no_layer_msg: str | None = None
        if intent in ("spatial_operation", "apply_symbology") and not has_active_layer:
            op_no_layer_msg = (
                "No hay una capa cargada sobre la que operar. Primero trae o "
                "carga datos (p.ej. «trae 100 lotes») y luego pídeme la operación."
            )
            logger.info(f"[Router] {intent} sin capa activa → mensaje honesto")

        # FRT-04: capa objetivo VALIDADA — sólo si el id existe entre las capas
        # cargadas (map_layers); si el LLM alucina un id, se ignora (cae a la
        # activa). Espeja el guardrail de _resolve_service_selection.
        _target = data.get("target_layer_id")
        from geo_copilot.orchestrator.layer_resolution import id_de_capa

        resolved_target = id_de_capa(_target, state)
        if _target and resolved_target is None:
            logger.warning(
                f"[Router] target_layer_id {_target!r} no existe en map_layers — ignorado"
            )

        return {
            "intent": intent,
            "target_layer_id": resolved_target,
            "entities": entities,
            "current_agent": "router",
            "analyze_degraded_no_layer": analyze_degraded,
            # A5: en `clarify` la "respuesta" ES la pregunta aclaratoria — el
            # responder la entrega y el turno termina limpio (sin plan fallido).
            # ORQ-12: idem para spatial_operation/apply_symbology sin capa.
            "final_response": (
                direct_response if intent in ("direct_response", "clarify")
                else op_no_layer_msg
            ),
            "external_url": external_url,
            "is_complex_query": is_complex_query,
            "messages": [{
                "agent": "router",
                "content": f"Intent: {intent} - {reasoning[:200] if reasoning else 'Processing'}",
                "data": data,
                "success": True,
            }],
        }

    # Fallback en caso de error — devolvemos un direct_response con el
    # mensaje del agente en lugar de forzar query_data.
    logger.error(f"Router error: {response.message}")
    error_data = response.data or {}
    error_response = error_data.get("direct_response")
    return {
        "intent": error_data.get("intent", "direct_response"),
        "entities": [],
        "error": response.message,
        "current_agent": "router",
        "is_complex_query": False,
        "final_response": error_response or f"Error: {response.message}",
        "messages": [{
            "agent": "router",
            "content": f"Error: {response.message}",
            "data": response.data,
            "success": False,
        }],
    }
