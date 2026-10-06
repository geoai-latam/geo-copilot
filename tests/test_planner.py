"""
Tests para Multi-Step Planning (Fase 2).

Valida:
1. PlanStep y ExecutionPlan - estructuras de datos
2. PlannerAgent - generación de planes
3. PlanExecutor - ejecución de pasos
4. Integración del flujo completo
"""

from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.orchestrator.planner import (
    ExecutionPlan,
    PlanExecutor,
    PlannerAgent,
    PlanStep,
    StepResult,
    StepStatus,
)


class TestPlanStep:
    """Tests para la estructura PlanStep."""

    def test_create_plan_step(self):
        """Debe crear un PlanStep con todos los campos."""
        step = PlanStep(
            step_id="step_1",
            description="Buscar bomberos",
            query_fragment="busca bomberos"
        )
        assert step.step_id == "step_1"
        assert step.description == "Buscar bomberos"
        assert step.query_fragment == "busca bomberos"

    def test_plan_step_default_query_fragment(self):
        """query_fragment debe tener default vacío."""
        step = PlanStep(step_id="step_1", description="Test")
        assert step.query_fragment == ""


class TestStepResult:
    """Tests para la estructura StepResult."""

    def test_successful_result(self):
        """Debe crear un resultado exitoso."""
        result = StepResult(
            step_id="step_1",
            success=True,
            output={"geojson": {"features": []}}
        )
        assert result.success is True
        assert result.error is None
        assert result.retry_count == 0

    def test_failed_result(self):
        """Debe crear un resultado fallido."""
        result = StepResult(
            step_id="step_1",
            success=False,
            error="Connection error"
        )
        assert result.success is False
        assert result.error == "Connection error"

    def test_skipped_result(self):
        """Debe crear un resultado saltado."""
        result = StepResult(
            step_id="step_1",
            success=False,
            skipped=True,
            skip_message="Condición no cumplida"
        )
        assert result.skipped is True
        assert result.skip_message == "Condición no cumplida"


class TestExecutionPlan:
    """Tests para la estructura ExecutionPlan."""

    def test_create_multi_step_plan(self):
        """Debe crear un plan multi-paso."""
        steps = [
            PlanStep("step_1", "Buscar", "busca datos"),
            PlanStep("step_2", "Procesar", "aplica buffer"),
        ]
        plan = ExecutionPlan(
            is_multi_step=True,
            reasoning="Buscar y procesar datos",
            steps=steps,
            original_query="busca datos y aplica buffer"
        )
        assert plan.is_multi_step is True
        assert len(plan.steps) == 2
        assert plan.reasoning == "Buscar y procesar datos"

    def test_single_step_plan(self):
        """Plan de un solo paso no debe ser multi-step."""
        steps = [PlanStep("step_1", "Buscar", "busca datos")]
        plan = ExecutionPlan(
            is_multi_step=False,
            reasoning="Solo búsqueda",
            steps=steps
        )
        assert plan.is_multi_step is False


class TestPlannerAgent:
    """Tests para PlannerAgent."""

    @pytest.fixture
    def mock_llm_client(self):
        """Mock del cliente LLM."""
        mock = AsyncMock()
        mock.chat = AsyncMock()
        return mock

    @pytest.fixture
    def planner(self, mock_llm_client):
        """Crear PlannerAgent con LLM mock."""
        return PlannerAgent(llm_client=mock_llm_client)

    def test_get_capabilities(self, planner):
        """Debe retornar capacidades correctas."""
        caps = planner.get_capabilities()
        assert "analyze_complexity" in caps["actions"]
        assert "generate_plan" in caps["actions"]
        assert "search_external" in caps["supported_operations"]

    @pytest.mark.asyncio
    async def test_is_complex_query_true(self, planner, mock_llm_client):
        """Debe detectar query compleja."""
        mock_llm_client.chat.return_value = MagicMock(
            content='{"is_multi_step": true}'
        )
        result = await planner.is_complex_query("busca bomberos y hazle buffer")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_complex_query_false(self, planner, mock_llm_client):
        """Debe detectar query simple."""
        mock_llm_client.chat.return_value = MagicMock(
            content='{"is_multi_step": false}'
        )
        result = await planner.is_complex_query("busca bomberos")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_complex_query_error_returns_false(self, planner, mock_llm_client):
        """Error en detección debe retornar False."""
        mock_llm_client.chat.side_effect = Exception("LLM Error")
        result = await planner.is_complex_query("cualquier query")
        assert result is False

    @pytest.mark.asyncio
    async def test_generate_plan(self, planner, mock_llm_client):
        """Debe generar plan con pasos."""
        # R2.5: los pasos deben traer action_type del vocabulario — sin él, el
        # planner re-pregunta y luego falla honesto (ver test_fase1_*).
        # A3: el plan llega como tool_call `create_plan` (structured outputs).
        from geo_copilot.core.scripted_llm import tool_call_response
        mock_llm_client.chat.return_value = tool_call_response("create_plan", {
            "reasoning": "Buscar y aplicar buffer",
            "steps": [
                {"step_id": "step_1", "action_type": "search_external",
                 "description": "Buscar", "query_fragment": "busca bomberos"},
                {"step_id": "step_2", "action_type": "spatial_operation",
                 "description": "Buffer", "query_fragment": "aplica buffer 500m"},
            ],
        })
        plan = await planner.generate_plan("busca bomberos y buffer", {})
        assert len(plan.steps) == 2
        assert plan.steps[0].query_fragment == "busca bomberos"
        assert plan.steps[1].query_fragment == "aplica buffer 500m"

    @pytest.mark.asyncio
    async def test_process_returns_response(self, planner, mock_llm_client):
        """process() debe retornar AgentResponse con plan."""
        from geo_copilot.core.scripted_llm import tool_call_response
        mock_llm_client.chat.return_value = tool_call_response("create_plan", {
            "reasoning": "Plan simple",
            "steps": [{"step_id": "step_1", "action_type": "query_database",
                       "description": "Test", "query_fragment": "test"}],
        })
        response = await planner.process("test query")
        assert response.success is True
        assert "steps" in response.data


class TestPlanExecutor:
    """Tests para PlanExecutor."""

    @pytest.fixture
    def mock_graph(self):
        """Mock del grafo de agentes."""
        mock = MagicMock()
        mock._router_node = AsyncMock(return_value={"intent": "search_external"})
        mock._data_agent_node = AsyncMock(return_value={
            "found_services": [{"name": "bomberos"}],
            "final_response": "Servicios encontrados"
        })
        return mock

    @pytest.fixture
    def executor(self, mock_graph):
        """Crear PlanExecutor con graph mock."""
        return PlanExecutor(graph=mock_graph)

    def test_normalize_plan_from_list(self, executor):
        """Debe normalizar lista de dicts a PlanSteps."""
        plan_data = [
            {"step_id": "step_1", "description": "Test", "query_fragment": "test"},
        ]
        steps = executor._normalize_plan(plan_data)
        assert len(steps) == 1
        assert isinstance(steps[0], PlanStep)

    def test_normalize_plan_from_execution_plan(self, executor):
        """Debe normalizar ExecutionPlan a lista de steps."""
        plan = ExecutionPlan(
            is_multi_step=True,
            reasoning="Test",
            steps=[PlanStep("step_1", "Test", "test")]
        )
        steps = executor._normalize_plan(plan)
        assert len(steps) == 1

    # NOTE ORC-5 (2026-05-30): borrados ``test_execute_step_success``,
    # ``test_execute_step_pending_selection``, ``test_merge_step_output`` y
    # ``test_merge_step_output_failed`` porque ejercitaban métodos privados
    # del PlanExecutor (``_execute_step``, ``_merge_step_output``,
    # ``_execute_through_graph``) que fueron eliminados al migrar el bucle
    # de ejecución dentro del grafo compilado de LangGraph
    # (``orchestrator/nodes/step_router.py`` + ``step_finalizer.py``).
    #
    # La cobertura equivalente vive ahora en
    # ``tests/test_orchestrator_integration.py``, que ejercita el grafo
    # completo (router → planner → step_router → agente → step_finalizer
    # → responder) con LLMs mockeados, y captura el comportamiento real
    # del flujo multi-paso.


class TestPlannerIntegration:
    """Tests de integración del planner."""

    @pytest.mark.asyncio
    async def test_full_plan_generation_and_normalization(self):
        """Test completo de generación y normalización de plan."""
        from geo_copilot.core.scripted_llm import tool_call_response
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = tool_call_response("create_plan", {
            "reasoning": "Buscar bomberos, aplicar buffer, aplicar color",
            "steps": [
                {"step_id": "step_1", "action_type": "search_external",
                 "description": "Buscar estaciones", "query_fragment": "busca bomberos"},
                {"step_id": "step_2", "action_type": "spatial_operation",
                 "description": "Buffer 500m", "query_fragment": "aplica buffer 500 metros"},
                {"step_id": "step_3", "action_type": "symbology",
                 "description": "Color rojo", "query_fragment": "color rojo"},
            ],
        })

        planner = PlannerAgent(llm_client=mock_llm)
        plan = await planner.generate_plan(
            "busca bomberos y hazle buffer 500m de color rojo",
            {}
        )

        assert plan.is_multi_step is True
        assert len(plan.steps) == 3
        assert "bomberos" in plan.steps[0].query_fragment
        assert "buffer" in plan.steps[1].query_fragment
        assert "rojo" in plan.steps[2].query_fragment
