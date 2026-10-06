"""Fase 4 — T30 (A4: perfil de datos + auto-verificación) y T31 (A5: clarify).

A4:
- build_data_profile: dtypes/nulos/min-max/top reales, determinista.
- El perfil se INYECTA al prompt de generación de código (gdf y gdf2).
- Un resultado no-responsivo dispara EXACTAMENTE 1 reintento dirigido;
  el veredicto responsivo no reintenta; un fallo del juez nunca tumba
  un resultado exitoso.

A5:
- Router: intent clarify válido con pregunta → final_response del turno;
  clarify SIN pregunta → fallo honesto.
- Planner: ask_user en el vocabulario y en sync con el dispatcher.
- step_router: paso ask_user pausa el plan con la pregunta (plan_paused +
  pending_operations) y NO ejecuta agentes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.agents.python_agent.data_profile import build_data_profile, profile_columns


def _features():
    feats = []
    for i in range(10):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-74, 4]},
            "properties": {
                "uso": ("residencial", "comercial")[i % 2] if i < 9 else None,
                "valor": i * 10,
                "nombre": f"p{i}",
            },
        })
    return feats


# ---------------------------------------------------------------------------
# A4(a) — perfil de datos
# ---------------------------------------------------------------------------
class TestDataProfile:
    def test_perfil_por_columna(self):
        cols = profile_columns(_features())
        assert cols["valor"]["dtype"] == "int"
        assert cols["valor"]["min"] == 0
        assert cols["valor"]["max"] == 90
        assert cols["uso"]["nulls"] == 1
        assert "residencial" in cols["uso"]["top"]

    def test_bloque_de_texto_contiene_hechos(self):
        block = build_data_profile(
            {"type": "FeatureCollection", "features": _features()}, name="gdf"
        )
        assert "PERFIL DE DATOS REAL de `gdf`" in block
        assert "min=0" in block and "max=90" in block
        assert "residencial" in block
        assert "NO inventes columnas" in block

    def test_determinista(self):
        gj = {"type": "FeatureCollection", "features": _features()}
        assert build_data_profile(gj) == build_data_profile(gj)

    def test_sin_features_vacio(self):
        assert build_data_profile({"features": []}) == ""
        assert build_data_profile(None) == ""

    @pytest.mark.asyncio
    async def test_perfil_llega_al_prompt_de_generacion(self):
        from geo_copilot.agents.python_agent.agent import PythonAgent

        agent = PythonAgent(llm_client=MagicMock())
        agent.llm_client.chat = AsyncMock(
            return_value=MagicMock(content="result = gdf.copy()")
        )
        gj = {"type": "FeatureCollection", "features": _features()}
        info = agent._analyze_geojson_structure(gj)
        await agent._generate_code(query="haz un buffer", geojson_info=info, geojson=gj)

        sent = agent.llm_client.chat.await_args.args[0]
        user_msg = next(m for m in sent if m.role == "user")
        assert "PERFIL DE DATOS REAL de `gdf`" in user_msg.content
        assert "min=0" in user_msg.content


# ---------------------------------------------------------------------------
# A4(b) — auto-verificación con 1 reintento
# ---------------------------------------------------------------------------
def _agent_with_sandbox(results: list[dict]):
    """PythonAgent con generación/HITL/sandbox mockeados."""
    from geo_copilot.agents.python_agent.agent import PythonAgent

    agent = PythonAgent(llm_client=MagicMock())
    agent.hitl_manager = None
    agent._generate_code = AsyncMock(return_value="result = gdf.copy()")
    agent._execute_in_sandbox = AsyncMock(side_effect=results)
    return agent


_CTX = {
    "active_data_source": "internal",
    "geojson": {"type": "FeatureCollection", "features": _features()},
}


class TestAutoVerificacion:
    @pytest.mark.asyncio
    async def test_no_responsivo_reintenta_exactamente_una_vez(self):
        agent = _agent_with_sandbox([
            {"success": True, "result_geojson": {"features": []}, "table": None,
             "stats": None, "chart": None},
            {"success": True, "result_geojson": None,
             "table": [{"corr": 0.9}], "stats": None, "chart": None},
        ])
        agent._judge_output_responds = AsyncMock(side_effect=[
            {"responds": False, "reason": "pidió correlación y no hay tabla"},
        ])
        corrected = "table = gdf[['valor','pisos']].corr()"
        with patch(
            "geo_copilot.agents.python_agent.code_corrector.CodeCorrector.correct_code",
            new=AsyncMock(return_value=corrected),
        ):
            resp = await agent.process("correlación entre valor y pisos", dict(_CTX))

        assert resp.success is True
        assert resp.data["table"] == [{"corr": 0.9}]  # el resultado del reintento
        assert agent._execute_in_sandbox.await_count == 2
        # El juez corrió UNA sola vez (el reintento no se re-juzga).
        assert agent._judge_output_responds.await_count == 1

    @pytest.mark.asyncio
    async def test_responsivo_no_reintenta(self):
        agent = _agent_with_sandbox([
            {"success": True, "result_geojson": None,
             "table": [{"cluster": 0}], "stats": None, "chart": None},
        ])
        agent._judge_output_responds = AsyncMock(
            return_value={"responds": True, "reason": "tabla con conteos"}
        )
        resp = await agent.process("clusters", dict(_CTX))
        assert resp.success is True
        assert agent._execute_in_sandbox.await_count == 1

    @pytest.mark.asyncio
    async def test_juez_roto_no_tumba_el_resultado(self):
        agent = _agent_with_sandbox([
            {"success": True, "result_geojson": None,
             "table": [{"a": 1}], "stats": None, "chart": None},
        ])
        # El método real captura sus excepciones → devuelve None; simulamos eso.
        agent._judge_output_responds = AsyncMock(return_value=None)
        resp = await agent.process("análisis", dict(_CTX))
        assert resp.success is True
        assert resp.data["table"] == [{"a": 1}]

    @pytest.mark.asyncio
    async def test_correccion_fallida_entrega_el_original(self):
        agent = _agent_with_sandbox([
            {"success": True, "result_geojson": {"features": []}, "table": None,
             "stats": None, "chart": None},
        ])
        agent._judge_output_responds = AsyncMock(
            return_value={"responds": False, "reason": "falta la tabla"}
        )
        with patch(
            "geo_copilot.agents.python_agent.code_corrector.CodeCorrector.correct_code",
            new=AsyncMock(return_value=None),  # no se pudo corregir
        ):
            resp = await agent.process("análisis", dict(_CTX))
        # El original exitoso se entrega (no un fallo).
        assert resp.success is True
        assert agent._execute_in_sandbox.await_count == 1


# ---------------------------------------------------------------------------
# A5 — clarify (router) y ask_user (planner/step_router)
# ---------------------------------------------------------------------------
class TestClarifyRouter:
    @pytest.mark.asyncio
    async def test_clarify_valido_propaga_la_pregunta(self):
        from geo_copilot.agents.router_agent.agent import RouterAgent
        from geo_copilot.core.scripted_llm import tool_call_response

        router = RouterAgent(llm_client=MagicMock())
        router.llm_client.chat = AsyncMock(return_value=tool_call_response("route", {
            "intent": "clarify",
            "reasoning": "hay 3 capas y no dice cuáles cruzar",
            "response": "¿Cuáles dos capas quieres cruzar: lotes, vías o barrios?",
        }))
        resp = await router.process("crúzalas", context={})
        assert resp.success is True
        assert resp.data["intent"] == "clarify"
        assert "cuáles" in resp.data["direct_response"].lower()

    @pytest.mark.asyncio
    async def test_clarify_sin_pregunta_falla_honesto(self):
        from geo_copilot.agents.router_agent.agent import RouterAgent
        from geo_copilot.core.scripted_llm import tool_call_response

        router = RouterAgent(llm_client=MagicMock())
        router.llm_client.chat = AsyncMock(return_value=tool_call_response("route", {
            "intent": "clarify", "reasoning": "ambigua", "response": "",
        }))
        resp = await router.process("crúzalas", context={})
        assert resp.success is False

    def test_nodo_router_propaga_final_response_en_clarify(self):
        # nodes/router.py: en clarify la pregunta viaja como final_response.
        import inspect

        from geo_copilot.orchestrator.nodes import router as m
        src = inspect.getsource(m)
        assert '("direct_response", "clarify")' in src

    def test_edge_manda_clarify_al_responder(self):
        from types import SimpleNamespace

        from geo_copilot.orchestrator.graph import GeoAgentGraph
        g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
        with patch(
            "geo_copilot.orchestrator.graph.get_settings",
            return_value=SimpleNamespace(
                react_mode=False, react_policy="off",
                hitl_mode="blocking", enable_planning=True,
            ),
        ):
            route = g._route_from_router({
                "intent": "clarify",
                "final_response": "¿Cuál capa?",
            })
        assert route == "responder"


class TestAskUserPlanner:
    def test_ask_user_en_vocabulario_y_en_sync(self):
        from geo_copilot.orchestrator.nodes.step_router import _ACTION_TO_INTENT
        from geo_copilot.orchestrator.planner import VALID_ACTION_TYPES

        assert "ask_user" in VALID_ACTION_TYPES
        assert set(VALID_ACTION_TYPES) <= set(_ACTION_TO_INTENT)

    @pytest.mark.asyncio
    async def test_paso_ask_user_pausa_el_plan_con_la_pregunta(self):
        from geo_copilot.orchestrator.nodes import step_router

        state = {
            "execution_plan": [
                {"step_id": "s1", "action_type": "ask_user",
                 "description": "preguntar capa",
                 "query_fragment": "¿Cuál de las dos capas quieres usar como base?"},
                {"step_id": "s2", "action_type": "spatial_operation",
                 "description": "cruce", "query_fragment": "haz el cruce"},
            ],
            "current_step_index": 0,
            "session_id": "",
        }
        updates = await step_router.run(MagicMock(), state)
        assert updates["plan_paused"] is True
        assert updates["final_response"].startswith("¿Cuál de las dos capas")
        assert updates["intent"] == "clarify"
        # Los pasos restantes quedan registrados para un turno futuro.
        assert updates["pending_operations"][0]["step_id"] == "s2"

    def test_edge_de_step_router_manda_clarify_al_finalizer(self):
        from geo_copilot.orchestrator.nodes.step_router import route_from_step_router

        state = {
            "execution_plan": [{"step_id": "s1", "action_type": "ask_user"}],
            "current_step_index": 0,
            "intent": "clarify",
        }
        assert route_from_step_router(state) == "step_finalizer"
