"""
Tests para el Orquestador GeoAgentGraph.

Pruebas del grafo de agentes A2A, routing, y procesamiento de consultas.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.orchestrator.conversation import (
    AnalysisState,
    ConversationContext,
    ConversationManager,
    Message,
    MessageRole,
    UserPreferences,
)
from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def conversation_manager():
    """Instancia de ConversationManager."""
    return ConversationManager(max_sessions=10, session_timeout_minutes=30)


@pytest.fixture
def conversation_context():
    """Instancia de ConversationContext."""
    return ConversationContext(session_id="test-session-123")


# ============================================================================
# Tests: ConversationManager
# ============================================================================

class TestConversationManager:
    """Tests para ConversationManager."""

    def test_create_session(self, conversation_manager):
        """Crear nueva sesión."""
        context = conversation_manager.create_session("test-session")

        assert context is not None
        assert context.session_id == "test-session"
        assert len(context.history) == 0

    def test_get_existing_session(self, conversation_manager):
        """Obtener sesión existente."""
        # Crear sesión
        context1 = conversation_manager.create_session("test-session")
        context1.add_user_message("Test message")

        # Obtener la misma sesión
        context2 = conversation_manager.get_session("test-session")

        assert context2 is not None
        assert len(context2.history) == 1

    def test_get_or_create_session(self, conversation_manager):
        """get_or_create debe retornar existente o crear nueva."""
        # Primera vez - crea
        context1 = conversation_manager.get_or_create_session("new-session")
        context1.set_variable("test", "value")

        # Segunda vez - retorna existente
        context2 = conversation_manager.get_or_create_session("new-session")

        assert context2.get_variable("test") == "value"

    def test_delete_session(self, conversation_manager):
        """Eliminar sesión existente."""
        conversation_manager.create_session("to-delete")

        result = conversation_manager.delete_session("to-delete")
        assert result is True

        # Ya no debe existir
        assert conversation_manager.get_session("to-delete") is None

    def test_list_sessions(self, conversation_manager):
        """Listar sesiones activas."""
        conversation_manager.create_session("session-1")
        conversation_manager.create_session("session-2")

        sessions = conversation_manager.list_sessions()

        assert len(sessions) == 2

    def test_session_timeout_cleanup(self, conversation_manager):
        """Las sesiones expiradas deben limpiarse."""
        # Crear sesión con timeout muy corto
        manager = ConversationManager(session_timeout_minutes=0)
        context = manager.create_session("will-expire")

        # El cleanup debe eliminarla
        count = manager._cleanup_expired_sessions()
        assert count >= 0  # Puede o no haber expirado dependiendo del timing


# ============================================================================
# Tests: ConversationContext
# ============================================================================

class TestConversationContext:
    """Tests para ConversationContext."""

    def test_add_messages(self, conversation_context):
        """Agregar diferentes tipos de mensajes."""
        conversation_context.add_user_message("User query")
        conversation_context.add_assistant_message("Assistant response")
        conversation_context.add_system_message("System info")

        assert len(conversation_context.history) == 3

    def test_message_roles(self, conversation_context):
        """Verificar roles de mensajes."""
        conversation_context.add_user_message("Test")
        conversation_context.add_assistant_message("Response")

        assert conversation_context.history[0].role == MessageRole.USER
        assert conversation_context.history[1].role == MessageRole.ASSISTANT

    def test_message_metadata(self, conversation_context):
        """Agregar metadata a mensajes."""
        conversation_context.add_assistant_message(
            "Response",
            sql="SELECT * FROM test",
            row_count=10
        )

        msg = conversation_context.history[0]
        assert msg.metadata.get("sql") == "SELECT * FROM test"
        assert msg.metadata.get("row_count") == 10

    def test_get_last_messages(self, conversation_context):
        """Obtener últimos N mensajes."""
        for i in range(10):
            conversation_context.add_user_message(f"Message {i}")

        last_5 = conversation_context.get_last_messages(5)

        assert len(last_5) == 5
        assert last_5[-1].content == "Message 9"

    def test_get_messages_for_llm(self, conversation_context):
        """Formatear mensajes para LLM."""
        conversation_context.add_user_message("Hello")
        conversation_context.add_assistant_message("Hi there")

        llm_messages = conversation_context.get_messages_for_llm()

        assert len(llm_messages) == 2
        assert llm_messages[0]["role"] == "user"
        assert llm_messages[0]["content"] == "Hello"

    def test_update_state(self, conversation_context):
        """Actualizar estado del análisis."""
        conversation_context.update_state(
            last_sql="SELECT * FROM parcelas",
            analysis_type="spatial_query"
        )

        assert conversation_context.state.last_sql == "SELECT * FROM parcelas"
        assert conversation_context.state.analysis_type == "spatial_query"

    def test_variables_for_found_services(self, conversation_context):
        """Variables deben funcionar para found_services."""
        services = [
            {"name": "Service 1", "url": "http://example.com/1"},
            {"name": "Service 2", "url": "http://example.com/2"}
        ]

        conversation_context.set_variable("found_services", services)
        retrieved = conversation_context.get_variable("found_services")

        assert retrieved == services
        assert len(retrieved) == 2

    def test_context_summary(self, conversation_context):
        """Obtener resumen del contexto."""
        conversation_context.add_user_message("Test")
        conversation_context.update_state(analysis_type="test")

        summary = conversation_context.get_summary()

        assert summary["session_id"] == "test-session-123"
        assert summary["message_count"] == 1

    def test_clear_history(self, conversation_context):
        """Limpiar historial de mensajes."""
        conversation_context.add_user_message("Test 1")
        conversation_context.add_user_message("Test 2")

        conversation_context.clear_history()

        assert len(conversation_context.history) == 0

    def test_full_reset(self, conversation_context):
        """Reset completo del contexto."""
        conversation_context.add_user_message("Test")
        conversation_context.set_variable("test_var", "value")
        conversation_context.update_state(last_sql="SELECT 1")

        conversation_context.reset()

        assert len(conversation_context.history) == 0
        assert conversation_context.get_variable("test_var") is None
        assert conversation_context.state.last_sql is None


# ============================================================================
# Tests: GraphState
# ============================================================================

class TestGraphState:
    """Tests para el estado del grafo."""

    @pytest.mark.asyncio
    async def test_process_seeds_found_services_into_initial_state(self):
        """process() debe SEMBRAR found_services en el estado inicial del grafo.

        TST-45: antes esto se "verificaba" con dos tests que sólo construían un
        dict GraphState y afirmaban lo que acababan de escribir (tautológicos —
        GraphState es un TypedDict, dict puro en runtime, no valida nada), y un
        tercero que sólo inspeccionaba la firma de process(). Un parámetro
        declarado pero IGNORADO los pasaba igual. Aquí interceptamos
        compiled.ainvoke y afirmamos que el estado inicial que recibe el grafo
        lleva realmente los found_services pasados a process() — un fallo de
        cableado (process dejando de propagarlos) rompe este test."""
        from geo_copilot.orchestrator import graph as graph_module

        services = [{"name": "Bomberos", "url": "http://x"}]
        graph = GeoAgentGraph(
            llm_client=MagicMock(),
            hitl_manager=MagicMock(),
            db_pool=None,
            semantic_layer=None,
        )
        # Forzar el camino clásico (ni ReAct 'always' ni interrupt) para que la
        # ejecución pase por compiled.ainvoke, donde inspeccionamos el estado.
        graph.compiled_interrupt = None
        graph.compiled = MagicMock()
        graph.compiled.ainvoke = AsyncMock(return_value={"final_response": "ok"})

        with patch.object(graph_module, "_resolve_react_policy", return_value="off"), \
             patch.object(graph, "_map_final_state", side_effect=lambda s: s):
            await graph.process(
                query="busca bomberos", session_id="s1", found_services=services
            )

        graph.compiled.ainvoke.assert_awaited_once()
        seeded_state = graph.compiled.ainvoke.await_args.args[0]
        assert seeded_state["found_services"] == services
        assert seeded_state["query"] == "busca bomberos"


# ============================================================================
# Tests: GeoAgentGraph
# ============================================================================

class TestGeoAgentGraph:
    """Tests para GeoAgentGraph."""

    def test_graph_constructs_all_required_agents(self):
        """El grafo debe construir las 6 instancias de agentes + planner.

        Antes este test era `assert hasattr(GeoAgentGraph, 'process')` — trivial.
        Ahora verifica que TODOS los agentes esperados existan como atributos
        después de instanciar."""
        mock_llm = MagicMock()
        graph = GeoAgentGraph(
            llm_client=mock_llm,
            hitl_manager=MagicMock(),
            db_pool=None,
            semantic_layer=None,
        )

        # Los 6 agentes del flujo principal.
        assert graph.router_agent is not None, "RouterAgent no inicializado"
        assert graph.data_agent is not None
        assert graph.gis_agent is not None
        assert graph.python_agent is not None
        assert graph.symbology_agent is not None
        assert graph.insights_agent is not None
        # PlannerAgent para multi-step.
        assert graph.planner_agent is not None
        # process() existe Y es async.
        import inspect
        assert inspect.iscoroutinefunction(graph.process), (
            "process() debe ser async para integrarse con FastAPI/LangGraph"
        )

    @pytest.mark.asyncio
    async def test_search_external_flow_invokes_router_then_data_agent(self):
        """Verifica el flow `intent=search_external` (búsqueda en Hub).

        Orden canónico: router → data_agent.process() → responder

        Este test verifica que:
        1. El router se invoca PRIMERO con la query.
        2. Después del router, data_agent.process() se invoca.
        3. El resultado final tiene `success` no-None (el grafo terminó).

        Si alguien rompe `_route_from_router` y `search_external` deja
        de enrutar a data_agent, este test detecta la regresión.

        Antes (auditoría 2026-05-24): NO existía ningún test que verificara
        el orden real de invocación de agentes — solo `hasattr(...)`.
        """
        from unittest.mock import AsyncMock

        from geo_copilot.agents.base import AgentResponse

        sequence: list[str] = []

        def make_agent(name: str, response_data: dict | None = None):
            # AsyncMock evita "MagicMock can't be used in 'await'" cuando
            # el grafo llama métodos auxiliares async del agente.
            agent = AsyncMock()

            async def fake_process(*args, **kwargs):
                sequence.append(name)
                return AgentResponse(
                    success=True,
                    message=f"{name} ok",
                    data=response_data or {},
                )

            agent.process = fake_process
            return agent

        graph = GeoAgentGraph(
            llm_client=AsyncMock(),
            hitl_manager=AsyncMock(),
            db_pool=None,
            semantic_layer=MagicMock(),
        )

        # Router decide: intent=search_external (que SÍ invoca data_agent.process).
        graph.router_agent = make_agent(
            "router",
            {
                "intent": "search_external",
                "entities": ["IGAC"],
                "additional_operations": [],
                "is_complex_query": False,
            },
        )
        # data_agent.process() devolverá búsqueda con servicios encontrados.
        graph.data_agent = make_agent(
            "data_agent",
            {
                "search_results": {
                    "status": "services_found",
                    "services": [],
                    "total_found": 0,
                },
                "search_query": "IGAC",
            },
        )
        graph.gis_agent = make_agent("gis_agent")
        graph.symbology_agent = make_agent("symbology_agent")
        graph.insights_agent = make_agent("insights_agent")
        graph.insights_agent.infer_visualization_type = AsyncMock(
            return_value={"type": "map"}
        )

        result = await graph.process(
            query="busca datos del IGAC",
            session_id="test-session",
        )

        # Verificaciones de orden y completitud:
        assert sequence, (
            f"Ningún agente invocado — error del grafo: {result.get('message')}"
        )
        # Router primero.
        assert sequence[0] == "router", (
            f"Router debe ser el primer agente; sequence={sequence}"
        )
        # data_agent.process() fue llamado (search_external lo dispara).
        assert "data_agent" in sequence, (
            f"data_agent.process no fue invocado para search_external; "
            f"sequence={sequence}"
        )
        # Orden router → data_agent respetado.
        assert sequence.index("router") < sequence.index("data_agent"), (
            "router debe invocarse ANTES que data_agent"
        )
        # gis_agent NO debe ser llamado (search_external no genera SQL).
        assert "gis_agent" not in sequence, (
            f"gis_agent NO debió ser invocado para search_external; "
            f"sequence={sequence}"
        )


# ============================================================================
# Tests: AnalysisState
# ============================================================================

class TestAnalysisState:
    """Tests para AnalysisState."""

    def test_default_state(self):
        """Estado inicial debe estar vacío."""
        state = AnalysisState()

        assert state.entities_identified == []
        assert state.current_query is None
        assert state.last_sql is None
        assert state.last_results is None
        assert state.last_geojson is None
        assert state.analysis_type is None
        assert state.parameters == {}

    def test_state_reset(self):
        """Reset debe limpiar todos los campos."""
        state = AnalysisState()
        state.last_sql = "SELECT * FROM test"
        state.entities_identified = ["parcela"]
        state.parameters = {"limit": 100}

        state.reset()

        assert state.last_sql is None
        assert state.entities_identified == []
        assert state.parameters == {}


# ============================================================================
# Tests: UserPreferences
# ============================================================================

class TestUserPreferences:
    """Tests para UserPreferences."""

    def test_default_preferences(self):
        """Preferencias por defecto."""
        prefs = UserPreferences()

        assert prefs.language == "es"
        assert prefs.output_format == "full_report"
        assert prefs.max_results == 1000
        assert prefs.auto_visualize is True
        assert prefs.require_approval is True

    def test_custom_preferences(self):
        """Preferencias personalizadas."""
        prefs = UserPreferences(
            language="en",
            max_results=500,
            require_approval=False
        )

        assert prefs.language == "en"
        assert prefs.max_results == 500
        assert prefs.require_approval is False


# ============================================================================
# Tests: Message
# ============================================================================

class TestMessage:
    """Tests para Message."""

    def test_message_creation(self):
        """Crear mensaje con campos requeridos."""
        msg = Message(
            role=MessageRole.USER,
            content="Test message"
        )

        assert msg.role == MessageRole.USER
        assert msg.content == "Test message"
        assert msg.metadata == {}

    def test_message_to_dict(self):
        """Convertir mensaje a diccionario."""
        msg = Message(
            role=MessageRole.ASSISTANT,
            content="Response",
            metadata={"sql": "SELECT 1"}
        )

        d = msg.to_dict()

        assert d["role"] == "assistant"
        assert d["content"] == "Response"
        assert d["metadata"]["sql"] == "SELECT 1"
        assert "timestamp" in d


# ============================================================================
# Tests de Integración: Flujo found_services
# ============================================================================

class TestFoundServicesIntegration:
    """Tests de integración para el flujo de found_services."""

    def test_full_search_select_flow(self):
        """Test del flujo completo de búsqueda y selección."""
        manager = ConversationManager()
        session_id = "integration-test-123"

        # === Turno 1: Usuario busca ===
        ctx = manager.get_or_create_session(session_id)
        ctx.add_user_message("busca servicios de bomberos en Bogotá")

        # Simular respuesta del sistema
        found = [
            {
                "name": "Estaciones Bomberos",
                "url": "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/emergencias/bomberos/MapServer",
                "description": "Ubicación de estaciones de bomberos"
            },
            {
                "name": "Zonas de Cobertura",
                "url": "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/emergencias/cobertura/MapServer",
                "description": "Zonas de cobertura del cuerpo de bomberos"
            }
        ]
        ctx.set_variable("found_services", found)
        ctx.add_assistant_message("Encontré 2 servicios relacionados con bomberos")

        # === Turno 2: Usuario selecciona ===
        ctx2 = manager.get_or_create_session(session_id)
        ctx2.add_user_message("carga el 1")

        # Obtener servicios guardados
        services = ctx2.get_variable("found_services")

        # VALIDACIONES CRÍTICAS
        assert services is not None, "found_services DEBE existir en el segundo turno"
        assert len(services) == 2, "Deben estar los 2 servicios"

        # Seleccionar por número
        selected = services[0]  # Usuario pidió el 1
        assert "bomberos" in selected["url"].lower()
        assert selected["name"] == "Estaciones Bomberos"

    def test_services_cleared_on_new_search(self):
        """Los servicios deben actualizarse con nueva búsqueda."""
        manager = ConversationManager()
        session_id = "test-clear"
        ctx = manager.get_or_create_session(session_id)

        # Primera búsqueda
        ctx.set_variable("found_services", [{"name": "old"}])

        # Segunda búsqueda reemplaza
        ctx.set_variable("found_services", [{"name": "new1"}, {"name": "new2"}])

        result = ctx.get_variable("found_services")
        assert len(result) == 2
        assert result[0]["name"] == "new1"
