"""
Orquestador basado en LangGraph con arquitectura Agent-to-Agent (A2A).

Define un grafo de estados donde cada nodo es un agente especializado
y las transiciones se basan en el tipo de tarea y resultados previos.

Todos los agentes ahora se usan correctamente:
- RouterAgent: self.router_agent.process()
- DataAgent: self.data_agent.process()
- GISAgent: generate_sql_from_query(), _execute_sql()
- SymbologyAgent: self.symbology_agent.process()
- InsightsAgent: self.insights_agent.process(), infer_visualization_type(), handle_follow_up()
"""


from langgraph.graph import END, StateGraph


def _resolve_react_policy(settings) -> str:
    """A2: política ReAct efectiva — 'off' | 'hybrid' | 'always'.

    ``react_mode=True`` (flag booleano de F4.5) equivale a 'always' y tiene
    precedencia, para no romper despliegues que ya lo usan. Un valor
    desconocido degrada a 'off' (comportamiento clásico, nunca sorpresa).
    """
    if getattr(settings, "react_mode", False):
        return "always"
    policy = str(getattr(settings, "react_policy", "off") or "off").strip().lower()
    return policy if policy in ("off", "hybrid", "always") else "off"

from geo_copilot.agents import DataAgent, GISAgent, PythonAgent, RouterAgent, SymbologyAgent
from geo_copilot.agents.insights_agent import InsightsAgent

# El progreso sale por `geo_copilot.platform.events` (S1.2): el núcleo no importa la API.
from geo_copilot.core.config import get_settings
from geo_copilot.core.llm_client import LLMClient
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.agent_hub import AgentHub
from geo_copilot.orchestrator.planner import PlanExecutor, PlannerAgent
from geo_copilot.security.hitl import HITLManager

logger = get_logger(__name__)

# F4.7: el estado y los nodos, rutas y resultado de GeoAgentGraph viven en sus módulos (mixins);
# se reexporta lo que el resto del núcleo y las pruebas importan desde `graph`.
from geo_copilot.orchestrator.grafo_estado import (  # noqa: F401
    AgentMessage,
    GraphState,
    _build_reasoning_trace,
    estado_inicial,
)
from geo_copilot.orchestrator.grafo_nodos import NodosMixin
from geo_copilot.orchestrator.grafo_resultado import ResultadoMixin
from geo_copilot.orchestrator.grafo_rutas import RutasMixin

# =============================================================================
# Estado del Grafo (LangGraph standard)
# =============================================================================


# =============================================================================
# Nodos del Grafo (Agentes)
# =============================================================================

class GeoAgentGraph(NodosMixin, RutasMixin, ResultadoMixin):
    """
    Grafo de agentes para procesamiento geoespacial.

    Arquitectura A2A:
    - Router: Analiza intent y decide qué agentes invocar
    - DataAgent: Valida disponibilidad de datos
    - GISAgent: Genera y ejecuta SQL espacial
    - SymbologyAgent: Genera simbología basada en datos
    - InsightsAgent: Genera narrativas y visualizaciones
    """

    def __init__(
        self,
        llm_client: LLMClient,
        hitl_manager: HITLManager | None = None,
        db_pool=None,
        semantic_layer=None,
    ):
        self.llm = llm_client
        self.hitl_manager = hitl_manager or HITLManager()
        self.db_pool = db_pool
        self.semantic_layer = semantic_layer

        # A2A hub (2026-05-31) — los agentes se registran aquí para que
        # otros agentes puedan invocar capacidades públicas mid-execution.
        # Ver ``orchestrator/agent_hub.py`` y los métodos públicos
        # ``DataAgent.lookup_entity``, etc.
        self.agent_hub = AgentHub()

        # F3.2: memoria de resolución de entidades por sesión (control de
        # confianza). Reutiliza resoluciones exactas, re-verifica las fuzzy.
        # F49: persiste a disco si agent_state_dir está configurado (durable).
        _settings = get_settings()
        _state_dir = getattr(_settings, "agent_state_dir", None)
        from geo_copilot.orchestrator.entity_memory import EntityMemory
        self.entity_memory = EntityMemory(
            persist_path=f"{_state_dir}/entity_memory.json" if _state_dir else None)

        # F6: métricas de largo plazo (cost-aware). El bucle ReAct registra
        # tokens/éxito/herramientas por turno y realimenta un hint de costo.
        from geo_copilot.core.agent_metrics import AgentMetrics
        self.agent_metrics = AgentMetrics(
            persist_path=f"{_state_dir}/agent_metrics.json" if _state_dir else None)

        # Inicializar agentes - cada uno con su responsabilidad
        self.router_agent = RouterAgent(llm_client=llm_client)
        self.data_agent = DataAgent(
            semantic_layer=semantic_layer,
            hitl_manager=self.hitl_manager,
            llm_client=llm_client
        )
        self.gis_agent = GISAgent(
            semantic_layer=semantic_layer,
            llm_client=llm_client,
            hitl_manager=self.hitl_manager,
            db_pool=db_pool,
            agent_hub=self.agent_hub,  # A2A: GIS puede consultar a Data.
        )
        self.python_agent = PythonAgent(
            llm_client=llm_client,
            hitl_manager=self.hitl_manager,
        )
        self.symbology_agent = SymbologyAgent(
            llm_client=llm_client,
            agent_hub=self.agent_hub,  # A2A: consulta InsightsAgent para viz fit.
        )
        self.insights_agent = InsightsAgent(
            llm_client=llm_client,
            agent_hub=self.agent_hub,  # A2A: consulta SymbologyAgent para palette.
        )

        # Registrar agentes en el hub con su nombre canónico.
        self.agent_hub.register("router", self.router_agent)
        self.agent_hub.register("data_agent", self.data_agent)
        self.agent_hub.register("gis_agent", self.gis_agent)
        self.agent_hub.register("python_agent", self.python_agent)
        self.agent_hub.register("symbology_agent", self.symbology_agent)
        self.agent_hub.register("insights_agent", self.insights_agent)

        # Multi-Step Planning (Fase 2)
        self.planner_agent = PlannerAgent(llm_client=llm_client)
        self.plan_executor = None  # Se inicializa después de construir el grafo

        # Cache de schema para evitar llamadas repetidas
        self._schema_cache: str | None = None
        self._schema_cache_time: float | None = None
        self._schema_cache_ttl = get_settings().schema_cache_ttl  # Configurable

        # Construir grafo
        self.graph = self._build_graph()
        self.compiled = self.graph.compile()

        # F4.2 cutover: en hitl_mode='interrupt' compilamos un segundo grafo CON
        # checkpointer para poder pausar (interrupt) y reanudar (Command(resume)).
        # El grafo de producción (self.compiled, sin checkpointer) queda intacto.
        # ``_pending_sql`` da IDEMPOTENCIA al resume: guarda el SQL generado antes
        # del interrupt para que la re-ejecución del nodo NO lo regenere (y se
        # apruebe exactamente lo que el usuario vio).
        self.compiled_interrupt = None
        self._pending_sql: dict[str, dict] = {}
        if getattr(get_settings(), "hitl_mode", "blocking") == "interrupt":
            from geo_copilot.orchestrator.hitl_interrupt import build_checkpointer
            self.compiled_interrupt = self.graph.compile(
                checkpointer=build_checkpointer(get_settings()))

        # Inicializar executor con referencia al grafo
        self.plan_executor = PlanExecutor(graph=self)

    async def _get_cached_schema(self) -> str:
        """
        Obtener schema con cache para evitar llamadas repetidas.

        El schema rara vez cambia durante una sesión, así que cachearlo
        mejora significativamente el rendimiento.
        """
        import time
        now = time.time()

        # Verificar si el cache es válido
        if (
            self._schema_cache is not None and
            self._schema_cache_time is not None and
            (now - self._schema_cache_time) < self._schema_cache_ttl
        ):
            logger.debug("[Schema] Using cached schema")
            return self._schema_cache

        # Refrescar cache
        logger.debug("[Schema] Refreshing schema cache")
        self._schema_cache = await self.gis_agent.get_db_schema_summary()
        self._schema_cache_time = now
        return self._schema_cache

    def _build_graph(self) -> StateGraph:
        """Construir el grafo de estados.

        ORC-5 (2026-05-30): el bucle multi-paso ahora vive dentro del
        grafo compilado. Antes ``plan_executor`` era un nodo Python con
        for-loop interno que dispatcheba a los agentes via ``_node(...)``
        saltándose LangGraph. Ahora ``step_router`` (entry del loop) y
        ``step_finalizer`` (cierre del loop) son nodos nativos del grafo
        y el routing condicional decide si volver al loop o salir.
        """
        graph = StateGraph(GraphState)

        # Agregar nodos (agentes)
        graph.add_node("router", self._router_node)
        graph.add_node("planner", self._planner_node)
        # A2 (hybrid): el bucle ReAct como nodo del grafo — el router puede
        # mandar las consultas COMPLEJAS aquí en vez de al planner cableado.
        graph.add_node("agent_loop", self._agent_loop_node)
        # ORC-5: nodos del bucle multi-paso nativo.
        graph.add_node("step_router", self._step_router_node)
        graph.add_node("step_finalizer", self._step_finalizer_node)
        graph.add_node("data_agent", self._data_agent_node)
        graph.add_node("gis_agent", self._gis_agent_node)
        graph.add_node("python_agent", self._python_agent_node)
        graph.add_node("symbology_agent", self._symbology_agent_node)
        graph.add_node("insights_agent", self._insights_agent_node)
        graph.add_node("responder", self._responder_node)

        # Punto de entrada
        graph.set_entry_point("router")

        # Transiciones condicionales desde router. Smart Router (2026-05-31):
        # ``symbology_agent`` añadido como destino directo para el intent
        # ``apply_symbology`` (skip de data/gis cuando hay capa activa).
        graph.add_conditional_edges(
            "router",
            self._route_from_router,
            {
                "planner": "planner",
                "agent_loop": "agent_loop",  # A2: complejas → ReAct (hybrid)
                "data_agent": "data_agent",
                "gis_agent": "gis_agent",
                "python_agent": "python_agent",
                "symbology_agent": "symbology_agent",
                "insights_agent": "insights_agent",
                "responder": "responder",
            }
        )

        # Planner → StepRouter (entry del bucle multi-paso).
        graph.add_edge("planner", "step_router")

        # A2: el bucle ReAct converge al responder como todos los caminos —
        # el responder respeta su final_response y su canal analítico (R1.4),
        # y arma final_data de forma consistente con el resto del grafo.
        graph.add_edge("agent_loop", "responder")

        # StepRouter → agente del step actual (decidido por action_type
        # ya traducido a ``intent`` por step_router.run).
        graph.add_conditional_edges(
            "step_router",
            self._route_from_step_router,
            {
                "data_agent": "data_agent",
                "python_agent": "python_agent",
                "symbology_agent": "symbology_agent",
                "step_finalizer": "step_finalizer",  # terminal / inválido
            }
        )

        # StepFinalizer → step_router (loop) o responder (salida).
        graph.add_conditional_edges(
            "step_finalizer",
            self._route_from_step_finalizer,
            {
                "step_router": "step_router",
                "responder": "responder",
            }
        )

        # DataAgent → GISAgent (datos internos) | SymbologyAgent (datos
        # externos ya con geojson) | StepFinalizer (multi-step) | Responder.
        graph.add_conditional_edges(
            "data_agent",
            self._route_from_data_agent,
            {
                "gis_agent": "gis_agent",
                "symbology_agent": "symbology_agent",
                "step_finalizer": "step_finalizer",
                "responder": "responder",
            }
        )

        # GISAgent → SymbologyAgent | StepFinalizer (multi-step) | Responder.
        graph.add_conditional_edges(
            "gis_agent",
            self._route_from_gis_agent,
            {
                "symbology_agent": "symbology_agent",
                "step_finalizer": "step_finalizer",
                "responder": "responder",
            }
        )

        # PythonAgent → SymbologyAgent | StepFinalizer (multi-step) | Responder.
        graph.add_conditional_edges(
            "python_agent",
            self._route_from_python_agent,
            {
                "symbology_agent": "symbology_agent",
                "step_finalizer": "step_finalizer",
                "responder": "responder",
            }
        )

        # SymbologyAgent → InsightsAgent (siempre — insights decide después
        # si volver al loop multi-step o ir al responder).
        graph.add_edge("symbology_agent", "insights_agent")

        # InsightsAgent → StepFinalizer (multi-step) | Responder.
        graph.add_conditional_edges(
            "insights_agent",
            self._route_from_insights_agent,
            {
                "step_finalizer": "step_finalizer",
                "responder": "responder",
            }
        )

        # Responder → END
        graph.add_edge("responder", END)

        return graph

    # =========================================================================
    # Nodos
    # =========================================================================

    # NOTA: _infer_visualization_type fue movido a InsightsAgent.infer_visualization_type()

    # =========================================================================
    # Routing Functions
    # =========================================================================

    # =========================================================================
    # Helper Methods
    # =========================================================================

    # NOTA: Las funciones _get_db_schema_summary, _get_db_schema, _generate_sql,
    # _execute_sql, _extract_geojson y _handle_follow_up fueron movidas a los
    # agentes correspondientes (GISAgent e InsightsAgent).

    # =========================================================================
    # Public API
    # =========================================================================

    async def process(
        self,
        query: str,
        session_id: str = "",
        conversation_history: list[dict] | None = None,
        previous_sql: str | None = None,
        previous_results: list[dict] | None = None,
        previous_geojson: dict | None = None,
        found_services: list[dict] | None = None,
        external_geojson: dict | None = None,
        external_source_name: str | None = None,
        has_external_data: bool = False,
        active_data_source: str = "none",
        active_source_name: str | None = None,
        map_context: dict | None = None,
        map_layers: dict[str, dict] | None = None,
        session_region: str | None = None,
    ) -> dict:
        """
        Procesar una consulta a través del grafo de agentes.

        Args:
            query: Consulta en lenguaje natural
            session_id: ID de sesión
            conversation_history: Historial de mensajes previos
            previous_sql: SQL de la última consulta ejecutada
            previous_results: Resultados de la última consulta
            found_services: Servicios encontrados en búsquedas anteriores
            external_geojson: GeoJSON de datos externos cargados
            external_source_name: Nombre del servicio externo
            has_external_data: Flag indicando si hay datos externos
            active_data_source: Fuente de datos activa ("internal", "external", "none")
            active_source_name: Nombre descriptivo de la fuente activa

        Returns:
            Diccionario con resultados
        """
        # Get autonomy settings
        settings = get_settings()
        autonomous_mode_enabled = getattr(settings, 'autonomous_mode', True)
        max_retries_config = getattr(settings, 'max_retries', 2)

        initial_state = estado_inicial(
            query, session_id=session_id, conversation_history=conversation_history,
            previous_sql=previous_sql, previous_results=previous_results, previous_geojson=previous_geojson,
            found_services=found_services, external_geojson=external_geojson,
            external_source_name=external_source_name, has_external_data=has_external_data,
            active_data_source=active_data_source, active_source_name=active_source_name,
            map_context=map_context, map_layers=map_layers, session_region=session_region,
            max_retries=max_retries_config, autonomous_mode=autonomous_mode_enabled,
        )

        # F4.5/A2: política ReAct. 'always' (o el flag viejo react_mode=True)
        # ejecuta TODO el turno con el bucle agent_loop, fuera del grafo.
        # 'hybrid' se decide DENTRO del grafo (el router manda las complejas a
        # agent_loop); 'off' es el camino clásico.
        if _resolve_react_policy(settings) == "always":
            return await self._run_react_mode(initial_state)

        # F4.2 cutover: HITL por interrupt (pausa/resume vía checkpointer) en vez
        # del bloqueante. Solo cuando el grafo con checkpointer está compilado
        # (hitl_mode='interrupt'). ReAct NO entra aquí (corre fuera del grafo).
        if self.compiled_interrupt is not None:
            return await self._run_interrupt_mode(initial_state, session_id)

        return await self._ejecutar_grafo(initial_state, settings)

    # =====================================================================
    # F4.2 cutover: HITL por LangGraph interrupt (pausa/resume durable)
    # =====================================================================
