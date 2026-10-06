"""Fase 4 del plan de remediación — T27 (R1.4) + T28 (A2), parte unitaria.

R1.4 — el bucle ReAct conoce el motor analítico:
- ``analyze_layer`` existe en el catálogo y despacha al python_agent con
  intent='analyze'.
- El canal analítico (``data``/``visualization``/``python_code``) se propaga:
  react_tools lo mergea, agent_loop lo expone, _run_react_mode lo respeta
  (sin re-inferir visualización).
- Datos frescos invalidan análisis viejos; la simbología NO borra nada.

A2 — política ReAct 'off' | 'hybrid' | 'always':
- resolución de política (compat react_mode=True ⇒ always).
- routing hybrid: complejas → agent_loop; simples → grafo clásico; guard de
  hitl_mode='interrupt'; analyze-sin-capa → agent_loop (no degradación).
- paridad de éxito: breaker del bucle ⇒ success=False también en el grafo.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.orchestrator import react_tools
from geo_copilot.orchestrator.graph import GeoAgentGraph, _resolve_react_policy
from geo_copilot.orchestrator.tool_schemas import TOOL_NAMES, is_known_tool, tool_schema


def _analytic_payload() -> dict:
    return {
        "current_agent": "python_agent",
        "geojson": None,
        "raw_data": [],
        "python_code": "result = ...",
        "data": {"results": [{"cluster": 0, "n": 30}, {"cluster": 1, "n": 30}]},
        "visualization": {"type": "table"},
        "messages": [{"agent": "python_agent", "content": "análisis ok", "success": True}],
    }


# ---------------------------------------------------------------------------
# R1.4 — catálogo y despacho
# ---------------------------------------------------------------------------
class TestAnalyzeLayerTool:
    def test_analyze_layer_en_catalogo(self):
        assert is_known_tool("analyze_layer")
        schema = tool_schema("analyze_layer")
        assert "request" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_despacha_a_python_agent_con_intent_analyze(self):
        seen: dict = {}

        async def fake_py_run(graph, substate):
            seen.update(substate)
            return _analytic_payload()

        with patch("geo_copilot.orchestrator.nodes.python_agent.run", new=fake_py_run):
            outcome = await react_tools.dispatch_tool(
                MagicMock(), {"geojson": {"features": [1]}},
                "analyze_layer", {"request": "clusters con DBSCAN"},
            )
        assert seen["intent"] == "analyze"
        assert seen["query"] == "clusters con DBSCAN"
        assert outcome.success
        # El canal analítico viaja en el delta y la observación lo narra.
        assert outcome.delta["data"]["results"]
        assert outcome.delta["visualization"] == {"type": "table"}
        assert "análisis" in outcome.observation

    @pytest.mark.asyncio
    async def test_query_database_limpia_analisis_viejo(self):
        async def fake_gis_run(graph, substate):
            return {"geojson": {"features": [{"a": 1}]}, "raw_data": [{"a": 1}]}

        working = {
            "data": {"results": [{"viejo": True}]},
            "visualization": {"type": "chart"},
        }
        with patch("geo_copilot.orchestrator.nodes.gis_agent.run", new=fake_gis_run):
            outcome = await react_tools.dispatch_tool(
                MagicMock(), working, "query_database", {"request": "lotes"},
            )
        # Pizarra limpia: el análisis del paso anterior NO sobrevive.
        assert outcome.delta["data"] is None
        assert outcome.delta["visualization"] is None

    @pytest.mark.asyncio
    async def test_query_database_declara_fuente_interna(self):
        """Tras query_database la fuente activa ES 'internal' — sin esto, el
        estado conservaba "none" (string truthy que el ``or`` no reemplazaba)
        y la siguiente herramienta veía "sin fuente activa" con 0 features.
        Bug real destapado por el bench hybrid.
        """
        async def fake_gis_run(graph, substate):
            return {"geojson": {"features": [{"a": 1}]}, "raw_data": [{"a": 1}]}

        working = {"active_data_source": "none"}
        with patch("geo_copilot.orchestrator.nodes.gis_agent.run", new=fake_gis_run):
            outcome = await react_tools.dispatch_tool(
                MagicMock(), working, "query_database", {"request": "lotes"},
            )
        assert outcome.delta["active_data_source"] == "internal"

    def test_effective_source_trata_none_string_como_vacio(self):
        assert react_tools._effective_source({"active_data_source": "none"}) == "internal"
        assert react_tools._effective_source(
            {"active_data_source": "none", "external_geojson": {"features": [1]}}
        ) == "external"
        assert react_tools._effective_source({"active_data_source": "previous"}) == "previous"

    @pytest.mark.asyncio
    async def test_analyze_layer_usa_fuente_efectiva(self):
        seen: dict = {}

        async def fake_py_run(graph, substate):
            seen.update(substate)
            return _analytic_payload()

        working = {
            "active_data_source": "none",  # el estado viejo decía "none"...
            "geojson": {"features": [{"a": 1}]},  # ...pero HAY capa interna
        }
        with patch("geo_copilot.orchestrator.nodes.python_agent.run", new=fake_py_run):
            await react_tools.dispatch_tool(
                MagicMock(), working, "analyze_layer", {"request": "clusters"},
            )
        assert seen["active_data_source"] == "internal"

    @pytest.mark.asyncio
    async def test_symbology_no_borra_datos_ni_analisis(self):
        async def fake_sym_run(graph, substate):
            return {"symbology": {"symbology_type": "unique_values"}}

        with patch("geo_copilot.orchestrator.nodes.symbology.run", new=fake_sym_run):
            outcome = await react_tools.dispatch_tool(
                MagicMock(), {"raw_data": [{"a": 1}], "data": {"results": [1]}},
                "apply_symbology", {"request": "por uso"},
            )
        # La simbología es estilo: no toca raw_data ni el canal analítico.
        assert "raw_data" not in outcome.delta
        assert "data" not in outcome.delta
        assert outcome.delta["symbology"]["symbology_type"] == "unique_values"


# ---------------------------------------------------------------------------
# R1.4 — el bucle expone el canal analítico
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_agent_loop_expone_canal_analitico():
    from geo_copilot.core.scripted_llm import ScriptedLLM, tool_call_response
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.orchestrator.react_tools import ToolOutcome

    graph = MagicMock()
    graph.llm = ScriptedLLM([
        tool_call_response("analyze_layer", {"request": "clusters"}),
        tool_call_response("answer", {"text": "2 clusters de 30 puntos."}),
    ])
    graph.agent_metrics = None
    settings = SimpleNamespace(
        react_max_tool_calls=8, react_token_budget=0, react_max_reflections=0,
    )
    analytic_outcome = ToolOutcome(
        observation="analyze: análisis listo (table, 2 fila(s))", success=True,
        delta={
            "data": {"results": [{"cluster": 0, "n": 30}, {"cluster": 1, "n": 30}]},
            "visualization": {"type": "table"},
            "python_code": "result = ...",
        },
    )
    with patch.object(agent_loop, "get_settings", return_value=settings), \
         patch.object(agent_loop, "dispatch_tool",
                      new=AsyncMock(return_value=analytic_outcome)):
        update = await agent_loop.run(graph, {"query": "agrupa en clusters", "session_id": "s"})

    assert update["final_response"] == "2 clusters de 30 puntos."
    assert update["data"]["results"][0]["cluster"] == 0
    assert update["visualization"] == {"type": "table"}
    assert update["python_code"] == "result = ..."


@pytest.mark.asyncio
async def test_run_react_mode_respeta_visualizacion_del_bucle():
    graph = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    loop_result = {
        "final_response": "Listo.",
        "decision_trace": [{"kind": "final"}],
        "data": {"results": [{"k": 1}]},
        "visualization": {"type": "chart", "chart_type": "bar"},
    }
    graph.insights_agent = MagicMock()
    graph.insights_agent.infer_visualization_type = AsyncMock()
    with patch("geo_copilot.orchestrator.nodes.agent_loop.run",
               new=AsyncMock(return_value=loop_result)):
        result = await graph._run_react_mode({"query": "q"})

    assert result["visualization"] == {"type": "chart", "chart_type": "bar"}
    assert result["data"] == {"results": [{"k": 1}]}
    # No se re-infirió: el canal analítico del bucle GANA.
    graph.insights_agent.infer_visualization_type.assert_not_awaited()


# ---------------------------------------------------------------------------
# A2 — resolución de política
# ---------------------------------------------------------------------------
class TestResolveReactPolicy:
    def test_default_off(self):
        assert _resolve_react_policy(SimpleNamespace(react_mode=False, react_policy="off")) == "off"

    def test_react_mode_viejo_gana_como_always(self):
        s = SimpleNamespace(react_mode=True, react_policy="off")
        assert _resolve_react_policy(s) == "always"

    def test_hybrid(self):
        s = SimpleNamespace(react_mode=False, react_policy="hybrid")
        assert _resolve_react_policy(s) == "hybrid"

    def test_valor_invalido_degrada_a_off(self):
        s = SimpleNamespace(react_mode=False, react_policy="turbo")
        assert _resolve_react_policy(s) == "off"


# ---------------------------------------------------------------------------
# A2 — routing hybrid en el grafo
# ---------------------------------------------------------------------------
def _settings(policy: str = "off", hitl_mode: str = "blocking") -> SimpleNamespace:
    return SimpleNamespace(
        react_mode=False, react_policy=policy, hitl_mode=hitl_mode,
        enable_planning=True,
    )


class TestRouteFromRouterHybrid:
    def _graph(self) -> GeoAgentGraph:
        return GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)

    def test_compleja_va_a_agent_loop_en_hybrid(self):
        g = self._graph()
        with patch("geo_copilot.orchestrator.graph.get_settings",
                   return_value=_settings("hybrid")):
            assert g._route_from_router({"is_complex_query": True, "intent": "query_data"}) == "agent_loop"

    def test_compleja_va_al_planner_en_off(self):
        g = self._graph()
        with patch("geo_copilot.orchestrator.graph.get_settings",
                   return_value=_settings("off")):
            assert g._route_from_router({"is_complex_query": True, "intent": "query_data"}) == "planner"

    def test_simple_no_cambia_en_hybrid(self):
        g = self._graph()
        with patch("geo_copilot.orchestrator.graph.get_settings",
                   return_value=_settings("hybrid")):
            assert g._route_from_router({"is_complex_query": False, "intent": "query_data"}) == "data_agent"

    def test_interrupt_conserva_el_planner(self):
        g = self._graph()
        with patch("geo_copilot.orchestrator.graph.get_settings",
                   return_value=_settings("hybrid", hitl_mode="interrupt")):
            assert g._route_from_router({"is_complex_query": True, "intent": "query_data"}) == "planner"

    def test_analyze_sin_capa_va_a_agent_loop_en_hybrid(self):
        g = self._graph()
        with patch("geo_copilot.orchestrator.graph.get_settings",
                   return_value=_settings("hybrid")):
            assert g._route_from_router({"intent": "analyze"}) == "agent_loop"

    def test_analyze_sin_capa_degrada_en_off(self):
        g = self._graph()
        with patch("geo_copilot.orchestrator.graph.get_settings",
                   return_value=_settings("off")):
            assert g._route_from_router({"intent": "analyze"}) == "data_agent"

    def test_analyze_con_capa_sigue_al_python_agent(self):
        g = self._graph()
        with patch("geo_copilot.orchestrator.graph.get_settings",
                   return_value=_settings("hybrid")):
            state = {"intent": "analyze", "geojson": {"features": [{"x": 1}]}}
            assert g._route_from_router(state) == "python_agent"


# ---------------------------------------------------------------------------
# A2 — el grafo compila con el nodo agent_loop y la paridad de éxito
# ---------------------------------------------------------------------------
def test_grafo_compila_con_agent_loop():
    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    assert "agent_loop" in g.compiled.get_graph().nodes


def test_map_final_state_breaker_es_fallo():
    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    ok = g._map_final_state({
        "final_response": "Alcancé el límite…",
        "decision_trace": [{"kind": "tool_call"}, {"kind": "error"}],
    })
    assert ok["success"] is False
    good = g._map_final_state({
        "final_response": "Listo",
        "decision_trace": [{"kind": "tool_call"}, {"kind": "final"}],
    })
    assert good["success"] is True
    # La traza ReAct se expone como reasoning_trace.
    assert good["reasoning_trace"] == [{"kind": "tool_call"}, {"kind": "final"}]


@pytest.mark.asyncio
async def test_router_node_no_marca_degradacion_en_hybrid():
    from geo_copilot.orchestrator.nodes import router as router_node

    graph = MagicMock()
    graph._get_cached_schema = AsyncMock(return_value="tablas…")
    graph.router_agent.process = AsyncMock(return_value=SimpleNamespace(
        success=True, message="",
        data={"intent": "analyze", "reasoning": "análisis pedido"},
    ))
    state = {"query": "haz clustering de los lotes"}

    with patch("geo_copilot.core.config.get_settings",
               return_value=_settings("hybrid")):
        update = await router_node.run(graph, dict(state))
    assert update["analyze_degraded_no_layer"] is False

    with patch("geo_copilot.core.config.get_settings",
               return_value=_settings("off")):
        update = await router_node.run(graph, dict(state))
    assert update["analyze_degraded_no_layer"] is True
