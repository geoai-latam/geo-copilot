"""El ESTADO del grafo (`GraphState`) y los mensajes entre agentes.

Salió de `graph.py` (F4 del plan de calidad), tal cual.
"""

from operator import add
from typing import Annotated, Any, TypedDict, cast

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.orchestrator.graph")


def _build_reasoning_trace(state: dict) -> list[dict]:
    """F1.2: traza de las decisiones del agente, para visibilidad.

    Expone qué decidió el router y qué agentes participaron, SIN volcar
    payloads internos (schema, resultados crudos, datos de A2A). Solo:
    paso/agente + resumen corto + ok.

    S0.4: recortar a 160 caracteres saneaba el VOLUMEN, no el contenido: el
    mensaje de un paso fallido podía llevar el error crudo de PostgreSQL. Ahora
    el resumen pasa solo si es una frase segura (`is_user_safe`); si no, un paso
    fallido muestra su categoría de error y uno exitoso no muestra resumen.
    """
    from geo_copilot.core.error_sanitizer import describe_error, is_user_safe

    # F4: misma forma que la traza del bucle ReAct (`TraceEntry` del contrato):
    # antes eran dos formas y el frontend esperaba una tercera.
    trace: list[dict] = []
    intent = state.get("intent")
    if intent:
        trace.append({"step": 0, "kind": "decision", "agent": "router", "detail": str(intent), "success": True})
    for m in state.get("messages") or []:
        agent = m.get("agent")
        if not agent or agent == "router":
            continue
        ok = bool(m.get("success", True))
        summary = " ".join(str(m.get("content") or "").split())[:160]
        if summary and not is_user_safe(summary):
            summary = "" if ok else (describe_error(summary, context=f"trace.{agent}") or "")
        trace.append({"step": len(trace), "kind": "agent", "agent": agent, "detail": summary, "success": ok})
    a2a = state.get("a2a_log") or []
    if a2a:
        trace.append({"step": len(trace), "kind": "a2a", "agent": "a2a",
                      "detail": f"{len(a2a)} llamada(s) cross-agent", "success": True})
    return trace


class AgentMessage(TypedDict):
    """Mensaje entre agentes (formato interno, no LangChain)."""
    agent: str
    content: str
    data: dict | None
    success: bool


class GraphState(TypedDict):
    """
    Estado compartido entre todos los agentes del grafo.

    Usa Annotated con reducers para campos que se acumulan:
    - messages: usa operator.add para concatenar listas
    """
    # Consulta original (inmutable durante ejecución)
    query: str
    session_id: str

    # Fase A: contexto del mapa/UI que el frontend adjunta (capas activas,
    # feature seleccionada, viewport, visualización). Permite al router y a
    # los agentes razonar sobre "esta capa"/"esta zona". None si no se envía.
    map_context: dict | None

    # F2.2: región de sesión (override del default configurado). None = default
    # del despliegue; key sin catálogo → búsqueda global/neutral (nunca colapsa
    # a Colombia en silencio). La consume el data_agent (discovery) y el router.
    session_region: str | None

    # B4: copia preservada de la query del turno. En multi-paso,
    # ``step_router`` PISA ``query`` con el fragmento de cada step; sin
    # esta copia, ``responder``/``insights`` (que leen ``query``) generaban
    # narrativa/visualización contra el último fragmento, no la consulta
    # real del usuario. ``step_finalizer`` restaura ``query`` desde aquí.
    original_query: str | None

    # Historial de conversación (para contexto de follow-up)
    conversation_history: list[dict]  # [{"role": "user/assistant", "content": "..."}]

    # Resultados previos (para preguntas de seguimiento)
    previous_sql: str | None
    previous_results: list[dict] | None
    # GeoJSON de la capa mostrada en el turno anterior. Permite que los
    # follow-up razonen sobre las features/geometría reales (no solo las
    # filas tabulares). NO se re-emite como capa nueva (evita duplicados).
    previous_geojson: dict | None

    # Historial de mensajes entre agentes (se acumula con reducer)
    messages: Annotated[list[AgentMessage], add]

    # Resultados de cada agente
    intent: str | None
    entities: list[str]
    # Degradación honesta: intent='analyze' llegó sin capa activa y se degradó a
    # data_agent. El responder lo usa para avisar en vez de mostrar datos en
    # silencio. Debe declararse aquí o LangGraph descarta la clave en el merge.
    analyze_degraded_no_layer: bool | None

    # Datos para búsqueda/carga externa
    external_url: str | None
    # Servicios encontrados en búsqueda anterior (para selección por número)
    found_services: list[dict] | None
    # True solo cuando ESTE turno ejecutó una búsqueda externa nueva.
    # query.py lo usa para decidir si emite las cards al frontend; sin esto
    # las cards se duplican en cada follow-up porque found_services se
    # propaga del state previo al output del grafo.
    new_search_executed: bool

    # NUEVO: Datos externos cargados (para operaciones espaciales en memoria)
    external_geojson: dict | None          # GeoJSON de fuente externa
    external_source_url: str | None        # URL del servicio
    external_source_name: str | None       # Nombre del servicio
    has_external_data: bool                # Flag para routing a python_agent
    # ORQ-11 (auditoría): un paso query_database AÍSLA los datos externos
    # (external_geojson=None) para operar sobre la BD; aquí se guarda el valor
    # para que el step_finalizer lo RESTAURE tras el paso (antes se anulaba y se
    # perdía para el resto del plan, aunque el comentario decía lo contrario).
    _saved_external_geojson: dict | None
    _saved_has_external_data: bool
    # Descriptor de capa imagery (MapServer tiles / ImageServer raster).
    # Cuando está poblado, el frontend monta un ArcGisMapServerImageryProvider
    # como capa raster en vez de procesar GeoJSON. Mutuamente excluyente con `geojson`.
    external_imagery: dict | None
    imagery_previas: list | None  # FH.10: los rasters anteriores del mismo turno (comparar fechas)

    # FRT-04: capas del mapa por id (geojson + nombre) para operar sobre una
    # capa NOMBRADA por el usuario, no sólo la activa. Sembrado desde map_context
    # (sólo en el estado del TURNO, no en la sesión persistente — evita bloat).
    map_layers: dict[str, dict] | None     # {layer_id: {"data": geojson, "name": str}}
    # FRT-04: id de la capa OBJETIVO que el LLM (router / tool ReAct) eligió por
    # nombre. None → se usa la capa activa (comportamiento previo). Validado
    # contra map_layers antes de escribirse.
    target_layer_id: str | None
    # V5 FH.4: lo que el seguimiento juzgó que hay que calcular (llega al bucle ReAct).
    interpretacion_previa: str | None
    # S2.5: LayerRef (dump) del dataset que produjo una capacidad ws_* del
    # workspace; query.py lo entrega tal cual en vez de re-materializar.
    result_layer_ref: dict | None

    # Fuente de datos activa (para evitar confusión entre interno/externo)
    active_data_source: str                # "internal", "external", "none"
    active_source_name: str | None         # Nombre descriptivo de la fuente activa

    # NUEVO: Resultados de PythonAgent
    python_code: str | None                # Código generado
    python_output: dict | None             # Resultado de ejecución

    # Datos generados por GISAgent
    sql: str | None
    raw_data: list[dict] | None
    geojson: dict | None
    # F2.3: veredicto del juez de resultados vacíos (0-por-bug vs 0-por-realidad).
    # DEBE ser un canal declarado: LangGraph descarta claves no declaradas en el
    # merge de estado, y sin esto el veredicto nunca llega a insights/responder.
    empty_result_verdict: dict | None

    # Canal analítico (intent 'analyze'). El nodo python_agent emite el
    # resultado del sandbox (tabla / estadística / gráfico) por ESTOS DOS
    # campos. DEBEN estar declarados o LangGraph los descarta en el merge —el
    # mismo mecanismo documentado arriba para empty_result_verdict— y el
    # gráfico/tabla nunca llegarían al cliente (bug: el responder re-infería la
    # visualización desde raw_data=[] y la real se perdía).
    #   data:          {"results": [...]}  filas para la tabla o los puntos del gráfico.
    #   visualization: {"type": "chart"|"table", "chart_type"?, "x_axis"?, "y_axis"?, "columns"?}
    # Para el resto de intents (query_data / geo) el responder infiere la
    # visualización; para 'analyze' se respeta la que el nodo ya decidió.
    data: dict | None
    visualization: dict | None
    # V5 F4: TODOS los resultados analíticos del turno, en orden ({data, visualization}).
    # `data`/`visualization` guardan solo el último: en un turno ReAct con gráfico y
    # luego tabla, el gráfico se perdía aunque el agente lo hubiera generado.
    analiticos: list | None
    # FH.1: órdenes del agente al mapa compartido (zoom, visibilidad, orden…), en orden.
    map_commands: list | None
    # A2 (hybrid): traza de decisiones del bucle ReAct cuando la consulta
    # compleja se ejecuta con agent_loop como nodo del grafo. Sin declararla,
    # LangGraph la descartaría en el merge (misma trampa que data/visualization).
    decision_trace: list | None

    # Simbología generada por SymbologyAgent
    symbology: dict | None
    layer_name: str | None

    # Control de flujo
    current_agent: str
    error: str | None
    requires_hitl: bool
    hitl_approved: bool

    # Respuesta final compilada por Responder
    final_response: str | None
    final_data: dict | None

    # =========================================================================
    # AUTONOMÍA: Control de auto-corrección y planificación multi-paso
    # =========================================================================

    # Control de autonomía (multi-step planning)
    execution_plan: list[dict] | None      # Plan de pasos a ejecutar
    current_step_index: int                # Índice del paso actual
    step_results: list[dict] | None        # Resultados de cada paso

    # Control de reintentos (self-correction)
    retry_count: int                       # Intentos del paso actual
    max_retries: int                       # Máximo de reintentos (default: 2)
    last_error: str | None                 # Error del último intento
    error_context: dict | None             # Contexto para corrección

    # Configuración de autonomía
    autonomous_mode: bool                  # Si está habilitado el modo autónomo

    # Flag de cancelación (consultado por PlanExecutor; los resultados
    # parciales se notifican vía WebSocket, no se guardan en el estado).
    cancelled: bool

    # A2A telemetría (2026-05-31): cada llamada cross-agent queda
    # registrada aquí. El responder la expone vía ``final_data`` para
    # debugging y para que el cliente pueda mostrar al usuario qué
    # decisiones automáticas tomó el sistema (ej. "corregí 'constsrucciones'
    # → 'construcciones' antes de generar SQL").
    a2a_log: Annotated[list[dict], add]

    # Multi-Step Planning (Fase 2)
    is_complex_query: bool                 # Flag del router indicando query compleja
    plan_reasoning: str | None             # Explicación del plan generado
    pending_operations: list[dict] | None  # Operaciones pendientes de plan pausado
    plan_paused: bool                      # Flag si el plan está pausado esperando input
    # Fallo parcial / total del plan multi-paso. Lo setea el responder
    # cuando ``step_results`` contiene pasos con success=False. Permite
    # que ``GeoAgentGraph.process`` reporte success=False aunque el grafo
    # haya terminado sin levantar ``state.error``.
    plan_partial_failure: bool


def estado_inicial(
    query: str, *, session_id: str, conversation_history: list[dict] | None,
    previous_sql: str | None, previous_results: list[dict] | None, previous_geojson: dict | None,
    found_services: list[dict] | None, external_geojson: dict | None, external_source_name: str | None,
    has_external_data: bool, active_data_source: str, active_source_name: str | None,
    map_context: dict | None, map_layers: dict[str, dict] | None, session_region: str | None,
    max_retries: int, autonomous_mode: bool,
) -> GraphState:
    """El estado con el que empieza un turno: lo que llega del cliente y de la sesión, y todo lo
    demás vacío (cada turno empieza sin resultados)."""
    estado: dict[str, Any] = {
        "query": query,
        "original_query": query,  # B4: preservada para multi-paso
        "map_context": map_context,  # Fase A: estado del mapa/UI
        "map_layers": map_layers,  # FRT-04: capas por id (para target por nombre)
        "target_layer_id": None,   # FRT-04: lo resuelve el router / tool ReAct
        "session_region": session_region,  # F2.2: override de región
        "session_id": session_id,
        "conversation_history": conversation_history or [],
        "previous_sql": previous_sql,
        "previous_results": previous_results,
        "previous_geojson": previous_geojson,
        "found_services": found_services,
        # Datos externos para PythonAgent
        "external_geojson": external_geojson,
        "external_source_name": external_source_name,
        "has_external_data": has_external_data or (external_geojson is not None),
        # Fuente de datos activa (para evitar confusión entre interno/externo)
        "active_data_source": active_data_source,
        "active_source_name": active_source_name or external_source_name,
        "max_retries": max_retries,
        "autonomous_mode": autonomous_mode,
        **_turno_vacio(),
    }
    return cast(GraphState, estado)


def _turno_vacio() -> dict[str, Any]:
    """Lo que cada turno empieza sin tener (resultados, plan, errores…): un dict nuevo cada vez."""
    return {
        "new_search_executed": False,
        "messages": [],
        "intent": None,
        "entities": [],
        "external_url": None,
        "external_source_url": None,
        "external_imagery": None,
        "analiticos": None,  # cada turno empieza sin resultados analíticos
        "map_commands": None,
        # Resultados de PythonAgent
        "python_code": None,
        "python_output": None,
        # GISAgent
        "sql": None,
        "raw_data": None,
        "empty_result_verdict": None,  # F2.3
        "geojson": None,
        "symbology": None,
        "layer_name": None,
        "current_agent": "",
        "error": None,
        "requires_hitl": False,
        "hitl_approved": False,
        "final_response": None,
        "final_data": None,
        # Autonomía: multi-step planning
        "execution_plan": None,
        "current_step_index": 0,
        "step_results": None,
        # A2 (hybrid): traza del bucle ReAct si la consulta va a agent_loop.
        "decision_trace": None,
        # Autonomía: self-correction
        "retry_count": 0,
        "last_error": None,
        "error_context": None,
        # Control de cancelación
        "cancelled": False,
        # Multi-Step Planning (Fase 2)
        "is_complex_query": False,
        "plan_reasoning": None,
        "pending_operations": None,
        "plan_paused": False,
        "plan_partial_failure": False,
        # A2A: el reducer ``add`` acumula entries entre nodos.
        "a2a_log": [],
    }
