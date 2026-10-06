"""Fase 1 del plan de remediación agéntica (T1–T6).

Cubre:
- R2.1: plan no parseable ⇒ 1 re-pregunta al LLM y luego fallo honesto
  (nunca "Plan generado con 0 pasos" exitoso).
- R2.5: action_type desconocido ⇒ paso fallido honesto (no adivina query_data).
- R1.1: el vocabulario del planner incluye `analyze` y está en sync con el
  dispatcher del step_router.
- R1.2: el juicio de éxito del step ve el canal analítico (`visualization`)
  y la limpieza de artefactos evita falsos éxitos por datos rancios.
- R1.3: el gate del CodeCorrector es por AST (acepta salidas analíticas,
  rechaza el comentario-trampa) y recibe la query original.
- R4.3: un resultado analítico vacío legítimo (table=[]) NO es fallo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.orchestrator.planner import (
    VALID_ACTION_TYPES,
    PlannerAgent,
    PlanStep,
)


def _llm_returning(*items):
    """Mock de LLMClient cuyo .chat devuelve estas respuestas en orden.

    A3 (structured outputs): un ``str`` simula una respuesta en PROSA (sin
    tool_call — fallo de FORMA); un ``dict`` simula la llamada correcta a
    ``create_plan`` con esos argumentos.
    """
    from geo_copilot.core.scripted_llm import final_response, tool_call_response

    llm = MagicMock()
    responses = [
        tool_call_response("create_plan", it) if isinstance(it, dict)
        else final_response(it)
        for it in items
    ]
    llm.chat = AsyncMock(side_effect=responses)
    return llm


PLAN_OK = {
    "reasoning": "r",
    "steps": [{
        "step_id": "step_1", "action_type": "query_database",
        "description": "traer lotes", "query_fragment": "trae lotes",
    }],
}


# ---------------------------------------------------------------------------
# R2.1 — plan no parseable: re-pregunta y fallo honesto
# ---------------------------------------------------------------------------
class TestPlanNoParseable:
    @pytest.mark.asyncio
    async def test_prosa_dos_veces_falla_honesto(self):
        agent = PlannerAgent(llm_client=_llm_returning("no soy json", "sigo sin ser json"))
        resp = await agent.process("haz algo compuesto", {})
        assert resp.success is False
        assert "0 pasos" not in resp.message  # el disfraz viejo
        assert agent.llm_client.chat.await_count == 2  # exactamente 1 re-pregunta

    @pytest.mark.asyncio
    async def test_retry_recupera_con_json_valido(self):
        agent = PlannerAgent(llm_client=_llm_returning("no soy json", PLAN_OK))
        resp = await agent.process("haz algo compuesto", {})
        assert resp.success is True
        assert len(resp.data["steps"]) == 1
        assert resp.data["steps"][0]["action_type"] == "query_database"

    @pytest.mark.asyncio
    async def test_plan_sin_pasos_tambien_reintenta(self):
        agent = PlannerAgent(
            llm_client=_llm_returning({"reasoning": "r", "steps": []}, PLAN_OK)
        )
        resp = await agent.process("compuesto", {})
        assert resp.success is True
        assert agent.llm_client.chat.await_count == 2

    @pytest.mark.asyncio
    async def test_action_type_invalido_reintenta_y_falla(self):
        malo = {
            "reasoning": "r",
            "steps": [{"step_id": "s1", "action_type": "hacer_magia",
                       "description": "x", "query_fragment": "x"}],
        }
        agent = PlannerAgent(llm_client=_llm_returning(malo, malo))
        resp = await agent.process("compuesto", {})
        assert resp.success is False
        # el mensaje de re-pregunta incluyó el problema
        second_call = agent.llm_client.chat.await_args_list[1].args[0]
        assert any("hacer_magia" in (m.content or "") for m in second_call)


# ---------------------------------------------------------------------------
# R1.1 — vocabulario con analyze + sincronía con el dispatcher
# ---------------------------------------------------------------------------
class TestVocabularioAnalyze:
    def test_analyze_en_vocabulario(self):
        assert "analyze" in VALID_ACTION_TYPES

    def test_vocabulario_en_sync_con_dispatcher(self):
        from geo_copilot.orchestrator.nodes.step_router import _ACTION_TO_INTENT
        # Todo action_type válido debe tener dispatch (nada cae al vacío).
        assert VALID_ACTION_TYPES <= set(_ACTION_TO_INTENT.keys())

    def test_prompt_ofrece_analyze(self):
        agent = PlannerAgent(llm_client=MagicMock())
        prompt = agent._build_planner_prompt({})
        assert '"analyze"' in prompt

    def test_planstep_sin_default_adivinador(self):
        # R2.5: el default ya no es 'general' (que caía al adivinador).
        assert PlanStep(step_id="s", description="d").action_type is None


# ---------------------------------------------------------------------------
# R2.5 — step_router: action_type desconocido ⇒ paso fallido honesto
# ---------------------------------------------------------------------------
class TestStepRouterActionInvalido:
    @pytest.mark.asyncio
    async def test_action_desconocido_marca_error(self):
        from geo_copilot.orchestrator.nodes import step_router

        graph = MagicMock()
        state = {
            "execution_plan": [
                {"step_id": "s1", "action_type": "volar", "description": "volar",
                 "query_fragment": "vuela"},
            ],
            "current_step_index": 0,
            "session_id": "",
        }
        updates = await step_router.run(graph, state)
        assert updates["intent"] == "invalid_action_type"
        assert "volar" in updates["error"]
        # NO preparó una consulta a la BD (el adivinador viejo).
        assert updates.get("active_data_source") != "internal"

    @pytest.mark.asyncio
    async def test_action_ausente_tambien_falla(self):
        from geo_copilot.orchestrator.nodes import step_router

        graph = MagicMock()
        state = {
            "execution_plan": [PlanStep(step_id="s1", description="algo")],
            "current_step_index": 0,
            "session_id": "",
        }
        updates = await step_router.run(graph, state)
        assert updates["intent"] == "invalid_action_type"

    def test_route_manda_a_finalizer(self):
        from geo_copilot.orchestrator.nodes.step_router import route_from_step_router

        state = {
            "execution_plan": [{"step_id": "s1", "action_type": "volar"}],
            "current_step_index": 0,
            "intent": "invalid_action_type",
        }
        assert route_from_step_router(state) == "step_finalizer"


# ---------------------------------------------------------------------------
# R1.2 — el finalizer ve el canal analítico; limpieza anti-rancio
# ---------------------------------------------------------------------------
class TestFinalizerCanalAnalitico:
    @pytest.mark.asyncio
    async def test_paso_analitico_con_solo_visualization_es_exito(self):
        from geo_copilot.orchestrator.nodes import step_finalizer

        graph = MagicMock()
        state = {
            "execution_plan": [
                {"step_id": "s1", "action_type": "analyze", "description": "cluster",
                 "query_fragment": "agrupa"},
            ],
            "current_step_index": 0,
            "step_results": [],
            "session_id": "",
            "intent": "analyze",
            "visualization": {"type": "table", "data": [{"c": 0, "n": 200}]},
            # SIN geojson/raw_data/symbology/final_response — antes esto era FALLO.
        }
        updates = await step_finalizer.run(graph, state)
        entry = updates["step_results"][0]
        assert entry["success"] is True, entry

    @pytest.mark.asyncio
    async def test_paso_imagery_con_visualization_es_exito(self):
        # M6: al no fijar el nodo `final_response` en multi-paso, el paso de
        # imagery se juzga por su visualization (tabla NDVI/cambio) — no FALLO.
        from geo_copilot.orchestrator.nodes import step_finalizer

        graph = MagicMock()
        state = {
            "execution_plan": [
                {"step_id": "s1", "action_type": "imagery", "description": "ndvi",
                 "query_fragment": "ndvi"},
            ],
            "current_step_index": 0,
            "step_results": [],
            "session_id": "",
            "intent": "imagery",
            "visualization": {"type": "table"},
            # SIN final_response (multi-paso no lo fija).
        }
        updates = await step_finalizer.run(graph, state)
        assert updates["step_results"][0]["success"] is True

    @pytest.mark.asyncio
    async def test_visualization_rancia_no_cuenta_para_query(self):
        from geo_copilot.orchestrator.nodes import step_finalizer

        graph = MagicMock()
        state = {
            "execution_plan": [
                {"step_id": "s1", "action_type": "query_database",
                 "description": "traer", "query_fragment": "trae"},
            ],
            "current_step_index": 0,
            "step_results": [],
            "session_id": "",
            "intent": "query_data",
            # visualization heredada de otro turno: NO debe salvar a un paso
            # de query que no produjo nada.
            "visualization": {"type": "table", "data": [{"x": 1}]},
        }
        updates = await step_finalizer.run(graph, state)
        entry = updates["step_results"][0]
        assert entry["success"] is False

    @pytest.mark.asyncio
    async def test_step_router_limpia_visualization_en_pasos_sandbox(self):
        from geo_copilot.orchestrator.nodes import step_router

        graph = MagicMock()
        state = {
            "execution_plan": [
                {"step_id": "s1", "action_type": "spatial_operation",
                 "description": "buffer", "query_fragment": "buffer 100m"},
            ],
            "current_step_index": 0,
            "session_id": "",
            "visualization": {"type": "table"},
        }
        updates = await step_router.run(graph, state)
        assert updates["visualization"] is None
        # el geojson de entrada NO se limpia (es el insumo del paso)
        assert "geojson" not in updates

    @pytest.mark.asyncio
    async def test_step_router_limpia_visualization_en_generadores(self):
        from geo_copilot.orchestrator.nodes import step_router

        graph = MagicMock()
        state = {
            "execution_plan": [
                {"step_id": "s1", "action_type": "query_database",
                 "description": "traer", "query_fragment": "trae"},
            ],
            "current_step_index": 0,
            "session_id": "",
        }
        updates = await step_router.run(graph, state)
        assert updates["visualization"] is None
        assert updates["geojson"] is None


# ---------------------------------------------------------------------------
# R1.3 — CodeCorrector: gate AST + query en el prompt
# ---------------------------------------------------------------------------
class TestCodeCorrectorContrato:
    def test_gate_acepta_salida_analitica(self):
        from geo_copilot.agents.python_agent.code_corrector import CodeCorrector
        assert CodeCorrector._assigns_output("table = df.to_dict('records')")
        assert CodeCorrector._assigns_output("stats = {'m': 1}\nchart = None")
        assert CodeCorrector._assigns_output("result = gdf.copy()")

    def test_gate_rechaza_comentario_trampa(self):
        from geo_copilot.agents.python_agent.code_corrector import CodeCorrector
        # El gate viejo por substring aceptaba esto ("result" en un comentario).
        assert not CodeCorrector._assigns_output("# calcula result\nx = 1")
        assert not CodeCorrector._assigns_output("results = [1]")  # nombre distinto
        assert not CodeCorrector._assigns_output("x = 1")

    @pytest.mark.asyncio
    async def test_correccion_analitica_aceptada_y_query_en_prompt(self):
        from geo_copilot.agents.python_agent.code_corrector import CodeCorrector

        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=MagicMock(content="table = gdf[['a']].to_dict('records')")
        )
        corrector = CodeCorrector(llm)
        out = await corrector.correct_code(
            code="table = gdf['no_existe'].to_dict()",
            error="KeyError: 'no_existe'",
            columns=["a"],
            feature_count=10,
            query="dame la tabla de a",
        )
        assert out is not None and "table" in out
        prompt = llm.chat.await_args.args[0][0].content
        assert "dame la tabla de a" in prompt  # la intención viaja al corrector


# ---------------------------------------------------------------------------
# R4.3 — analítico vacío legítimo ≠ fallo
# ---------------------------------------------------------------------------
class TestAnaliticoVacioLegitimo:
    @pytest.mark.asyncio
    async def test_tabla_vacia_es_exito(self, monkeypatch):
        from geo_copilot.agents.python_agent.agent import PythonAgent

        agent = PythonAgent(llm_client=MagicMock())
        # Simular el resultado del sandbox: el código DEFINIÓ table=[] (vacía
        # legítima) sin geometría.
        sandbox = MagicMock()
        sandbox.execute = AsyncMock(return_value={
            "success": True,
            "output": "",
            "results": {"analysis_output": {"table": []}},
        })
        agent.sandbox = sandbox
        result = await agent._execute_in_sandbox("table = []", {"features": []})
        assert result["success"] is True, result
        assert result["table"] == []
