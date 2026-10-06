"""
Integration tests for ``GeoAgentGraph.process()`` end-to-end (QA-6 /
Fase 6 #5).

Why this file exists
--------------------
Before this, ``tests/test_orchestrator.py`` exercised mostly trivial
dataclasses and ``inspect.signature`` assertions; nothing drove the
graph through router → agent → responder. With the graph at 35-ish %
coverage, any refactor of ``graph.py`` (Fase 6 #6) would have been
shooting in the dark.

These tests are the safety net for that refactor. They mock the LLM
(``LLMClient.chat``) and the database pool but exercise the *real*
``GeoAgentGraph._build_graph`` wiring, the *real* node functions, and
the *real* routing edges. If a node is moved, renamed, or its return
shape changes, these tests should catch it.

Scope kept intentionally tight
------------------------------
* No live LLM, no live DB. Everything fast and deterministic.
* Cada test ejercita un único camino del grafo, con el mínimo de
  mocking necesario para llegar a ese camino.
* No assertions on internal node implementations — only on the
  observable result of ``process()``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.core.config import get_settings
from geo_copilot.core.llm_client import LLMResponse
from geo_copilot.orchestrator.graph import GeoAgentGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llm_response(content: str | dict) -> LLMResponse:
    """Build an ``LLMResponse`` from either a string or a dict.

    A3 (structured outputs): el router y el planner ya NO parsean JSON del
    ``content`` — reciben una LLAMADA a función (`route` / `create_plan`).
    Un dict con ``intent`` se encapsula como tool_call `route`; uno con
    ``steps`` como tool_call `create_plan`. El resto sigue siendo content
    plano (SQL, narrativa, judges…).
    """
    if isinstance(content, dict):
        from geo_copilot.core.scripted_llm import tool_call_response
        if "intent" in content:
            return tool_call_response("route", content)
        if "steps" in content:
            return tool_call_response("create_plan", content)
        content = json.dumps(content)
    return LLMResponse(content=content, model="mock-model", usage=None, tool_calls=None)


def _build_graph_with_mock_llm(*responses: LLMResponse | str | dict) -> tuple[GeoAgentGraph, AsyncMock]:
    """Build a real ``GeoAgentGraph`` with a mocked LLM client.

    ``responses`` is the ordered list of LLM responses that ``chat`` will
    return on successive calls. Strings/dicts are wrapped automatically.
    """
    normalized: list[LLMResponse] = []
    for r in responses:
        if isinstance(r, LLMResponse):
            normalized.append(r)
        else:
            normalized.append(_llm_response(r))

    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(side_effect=normalized)
    # ``handle_follow_up`` and others call ``llm.chat``; that's covered.

    graph = GeoAgentGraph(llm_client=mock_llm, db_pool=None, semantic_layer=None)
    return graph, mock_llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGeoAgentGraphConstruction:
    """The graph builds and compiles without a DB or semantic layer."""

    def test_graph_compiles(self) -> None:
        graph, _ = _build_graph_with_mock_llm()
        assert graph.compiled is not None
        assert graph.router_agent is not None
        assert graph.gis_agent is not None
        assert graph.python_agent is not None
        assert graph.symbology_agent is not None
        assert graph.insights_agent is not None
        assert graph.planner_agent is not None
        assert graph.plan_executor is not None

    def test_initial_state_has_required_keys(self) -> None:
        """The state initialized in ``process()`` must declare every key
        downstream nodes read from. This catches accidental key removals.
        """
        # We can introspect the GraphState TypedDict to ensure the
        # initialization in ``process()`` keeps it in sync.
        from geo_copilot.orchestrator.graph import GraphState

        annotations = set(GraphState.__annotations__.keys())
        # Sample of must-have keys that nodes read from state.
        must_have = {
            "query", "session_id", "conversation_history", "messages",
            "intent", "entities", "external_geojson", "external_source_name",
            "has_external_data", "active_data_source", "geojson", "sql",
            "symbology", "layer_name", "current_agent", "error",
            "requires_hitl", "hitl_approved", "final_response",
            "execution_plan", "current_step_index", "step_results",
            "retry_count", "max_retries", "last_error", "autonomous_mode",
            "cancelled", "is_complex_query", "plan_reasoning",
        }
        missing = must_have - annotations
        assert not missing, f"GraphState missing keys: {missing}"


class TestDirectResponseFlow:
    """Simplest path: router decides ``respond_directly`` and the
    responder echoes ``direct_response``. No DB / SQL / Python involved.

    The graph's ``process()`` flattens ``final_state`` into a public
    dict where the user-facing text lands in ``message`` (not
    ``final_response``); we test against that contract.
    """

    @pytest.mark.asyncio
    async def test_direct_response_reaches_message_field(self) -> None:
        # Sprint B (2026-05-25): el RouterAgent espera ``intent`` directo
        # del LLM, no ``action``. El mapeo action→intent y la heuristica
        # ``_map_action_to_intent`` se borraron; sin ``intent`` el agente
        # falla honestamente con ValueError.
        router_decision = {
            "intent": "direct_response",
            "reasoning": "saludo simple",
            "response": "Hola, soy GEO_COPILOT.",
            "entities": [],
            "is_multi_step": False,
            "additional_operations": [],
        }
        graph, mock_llm = _build_graph_with_mock_llm(router_decision)

        result = await graph.process(query="hola", session_id="s-direct")

        assert isinstance(result, dict)
        # The direct response should surface in ``message`` (which the
        # API forwards to the user).
        assert "Hola, soy GEO_COPILOT" in (result.get("message") or "")
        assert result.get("success") is True
        # Exactly one LLM call: the router. No agent dispatched.
        assert mock_llm.chat.await_count == 1

    @pytest.mark.asyncio
    async def test_direct_response_terminates_without_db(self) -> None:
        """No DB needed: graph completes even with db_pool=None."""
        router_decision = {
            "intent": "direct_response",
            "reasoning": "explicacion de capacidades",
            "response": "Puedo consultar PostGIS y cargar capas externas.",
            "entities": [],
            "is_multi_step": False,
            "additional_operations": [],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision)

        result = await graph.process(query="qué puedes hacer", session_id="s2")

        # No SQL emitted, no raw_data, but message is non-empty.
        assert result.get("sql") is None
        assert result.get("message") is not None and len(result["message"]) > 0
        # Now that the mock provides ``intent`` correctly, success must be
        # True — the previous test passed by accident on an error message.
        assert result.get("success") is True
        assert "PostGIS" in result["message"]


class TestErrorHandling:
    """Failure modes the graph must degrade through gracefully."""

    @pytest.mark.asyncio
    async def test_router_returns_non_json_falls_back(self) -> None:
        """A non-JSON LLM response must not crash routing.

        AGT-11 hardened ``parse_json_from_llm`` to take ``default={}``;
        this test pins that behaviour at the graph level.
        """
        graph, _ = _build_graph_with_mock_llm("this is not json at all")
        result = await graph.process(query="ping", session_id="s-err")
        # The graph completed without raising. ``success`` is always
        # present in the public return shape.
        assert isinstance(result, dict)
        assert "success" in result

    @pytest.mark.asyncio
    async def test_router_exception_does_not_propagate(self) -> None:
        """If the LLM client itself raises, the graph still returns."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("upstream down"))
        graph = GeoAgentGraph(llm_client=mock_llm, db_pool=None, semantic_layer=None)

        result = await graph.process(query="x", session_id="s-boom")
        assert isinstance(result, dict)
        # No exception leaked out of process(); the router caught it and
        # produced a fallback AgentResponse (its ``except`` branch).


class TestComplexQueryRouting:
    """Multi-step queries should go to the planner, not directly to an agent."""

    @pytest.mark.asyncio
    async def test_complex_query_invokes_planner(self) -> None:
        """When the router reports ``is_multi_step=true`` the graph must
        call the planner. We mock the planner LLM call to produce an
        empty plan so the execution short-circuits without needing DB.
        """
        router_decision = {
            "intent": "query_data",
            "reasoning": "consulta multi-paso",
            "response": "",
            "entities": [],
            "is_multi_step": True,
            "additional_operations": ["buffer 500m", "color rojo"],
        }
        # The planner is asked to generate a plan; we return an empty
        # plan so plan_executor finishes immediately.
        planner_plan = {
            "reasoning": "no decomposable in mock",
            "steps": [],
        }
        graph, mock_llm = _build_graph_with_mock_llm(router_decision, planner_plan)

        result = await graph.process(
            query="busca bomberos y aplica buffer 500m en rojo",
            session_id="s-complex",
        )

        # Both router and planner were invoked.
        assert mock_llm.chat.await_count >= 2
        # We returned a dict (no crash) even with an empty plan.
        assert isinstance(result, dict)


# ============================================================================
# ORC-5: tests del bucle multi-paso nativo
# ----------------------------------------------------------------------------
# Antes el PlanExecutor corría un for-loop Python invocando los nodos
# directamente. Ahora ``step_router → agente → step_finalizer`` viven en
# el grafo compilado. Estos tests parchean los nodos de agente para
# verificar que el bucle entrega/cierra correctamente cada step y que
# las terminaciones (éxito, pending_selection, fallo) se ejecutan en el
# grafo, no fuera.
# ============================================================================


class TestMultiStepNativeLoop:
    """Bucle multi-paso nativo (step_router ↔ step_finalizer).

    A2: estos tests prueban el camino CABLEADO del planner, así que fijan
    ``react_policy='off'`` — con el default 'hybrid' las consultas complejas
    irían al bucle ReAct (cubierto en test_fase4_react_analitico).
    """

    @pytest.fixture(autouse=True)
    def _wired_planner_path(self):
        from unittest.mock import patch

        from geo_copilot.core.config import get_settings as _gs
        pinned = _gs().model_copy(update={"react_policy": "off"})
        with patch("geo_copilot.orchestrator.graph.get_settings", return_value=pinned):
            yield

    @pytest.mark.asyncio
    async def test_two_step_plan_loops_through_finalizer(self) -> None:
        """Plan de 2 pasos: ambos terminan OK, se llega al responder.

        Verifica que ``step_finalizer`` incrementa correctamente
        ``current_step_index`` y rutea de vuelta a ``step_router`` para
        el segundo paso, y luego sale al responder.
        """
        from unittest.mock import patch

        router_decision = {
            "intent": "query_data",
            "reasoning": "multi-paso",
            "response": "",
            "entities": [],
            "is_multi_step": True,
            "additional_operations": ["color rojo"],
        }
        planner_plan = {
            "reasoning": "consulta + simbología",
            "steps": [
                {
                    "step_id": "step_1",
                    "action_type": "query_database",
                    "description": "Traer construcciones",
                    "query_fragment": "trae todas las construcciones",
                },
                {
                    "step_id": "step_2",
                    "action_type": "symbology",
                    "description": "Color rojo",
                    "query_fragment": "aplica color rojo",
                },
            ],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision, planner_plan)

        # Mock de los nodos de agente — devolvemos lo justo para que
        # step_finalizer marque success en cada step. infer_visualization_type
        # se mockea porque el responder lo invoca al cerrar (consumiría
        # un LLM call extra que no estamos suministrando).
        graph.insights_agent.infer_visualization_type = AsyncMock(
            return_value={"type": "map", "feature_count": 1}
        )
        with patch(
            "geo_copilot.orchestrator.nodes.data_agent.run",
            new=AsyncMock(return_value={}),
        ), patch(
            "geo_copilot.orchestrator.nodes.gis_agent.run",
            new=AsyncMock(return_value={
                "geojson": {"type": "FeatureCollection", "features": [{"a": 1}]},
                "raw_data": [{"a": 1}],
            }),
        ), patch(
            "geo_copilot.orchestrator.nodes.symbology.run",
            new=AsyncMock(return_value={
                "symbology": {"fill": {"color": "#ff0000"}},
                "layer_name": "construcciones rojo",
            }),
        ), patch(
            "geo_copilot.orchestrator.nodes.insights.run",
            new=AsyncMock(return_value={}),
        ):
            result = await graph.process(
                query="trae construcciones y color rojo",
                session_id="s-multi-ok",
            )

        # El grafo completó sin error.
        assert isinstance(result, dict)
        # step_results refleja los 2 pasos ejecutados (ambos OK).
        step_results = result.get("step_results") or []
        assert len(step_results) == 2, f"esperaba 2 step_results, obtuve {step_results}"
        assert all(s["success"] for s in step_results), step_results

    @pytest.mark.asyncio
    async def test_pending_selection_pauses_plan_native_loop(self) -> None:
        """Un step que devuelve found_services sin geojson pausa el plan.

        El step_finalizer detecta pending_selection, construye el mensaje
        de pausa y sale al responder con ``plan_paused=True``.
        """
        from unittest.mock import patch

        router_decision = {
            "intent": "search_external",
            "reasoning": "multi-paso",
            "response": "",
            "entities": [],
            "is_multi_step": True,
            "additional_operations": ["buffer 500m"],
        }
        planner_plan = {
            "reasoning": "buscar + buffer",
            "steps": [
                {
                    "step_id": "step_1",
                    "action_type": "search_external",
                    "description": "Buscar bomberos",
                    "query_fragment": "busca bomberos",
                },
                {
                    "step_id": "step_2",
                    "action_type": "spatial_operation",
                    "description": "Buffer 500m",
                    "query_fragment": "buffer 500m",
                },
            ],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision, planner_plan)
        graph.insights_agent.infer_visualization_type = AsyncMock(
            return_value={"type": "empty"}
        )

        with patch(
            "geo_copilot.orchestrator.nodes.data_agent.run",
            new=AsyncMock(return_value={
                "found_services": [{"name": "Bomberos Bogotá"}, {"name": "Bomberos Cali"}],
                "new_search_executed": True,
            }),
        ):
            result = await graph.process(
                query="busca bomberos y aplica buffer 500m",
                session_id="s-pause",
            )

        # El plan pausó después del primer step.
        step_results = result.get("step_results") or []
        assert len(step_results) == 1, "solo se debe ejecutar 1 step antes de pausar"
        # pending_operations refleja el step pendiente.
        assert result.get("pending_operations"), "esperaba pending_operations no vacío"
        assert len(result["pending_operations"]) == 1

    @pytest.mark.asyncio
    async def test_failed_step_skips_dependents_native_loop(self) -> None:
        """F3.1: un step fallido ya NO aborta el plan. Sus dependientes se
        SALTAN (honesto, no se ejecutan sobre datos viejos) y el plan termina
        reportando el desglose. Aquí step_2 (color) depende de step_1 (query,
        default lineal); step_1 falla → step_2 se salta, ambos quedan en
        step_results y el resultado global es success=False (fallo parcial)."""
        from unittest.mock import patch

        router_decision = {
            "intent": "query_data",
            "reasoning": "multi-paso",
            "response": "",
            "entities": [],
            "is_multi_step": True,
            "additional_operations": ["color rojo"],
        }
        planner_plan = {
            "reasoning": "consulta + simbología",
            "steps": [
                {
                    "step_id": "step_1",
                    "action_type": "query_database",
                    "description": "Traer construcciones",
                    "query_fragment": "trae construcciones",
                },
                {
                    "step_id": "step_2",
                    "action_type": "symbology",
                    "description": "Color rojo",
                    "query_fragment": "aplica color rojo",
                },
            ],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision, planner_plan)
        graph.insights_agent.infer_visualization_type = AsyncMock(
            return_value={"type": "empty"}
        )

        with patch(
            "geo_copilot.orchestrator.nodes.data_agent.run",
            new=AsyncMock(return_value={}),
        ), patch(
            "geo_copilot.orchestrator.nodes.gis_agent.run",
            new=AsyncMock(return_value={"error": "SQL syntax error"}),
        ):
            result = await graph.process(
                query="trae construcciones y color rojo",
                session_id="s-fail",
            )

        # F3.1: ambos steps se procesan — step_1 falla, step_2 se SALTA por
        # depender de step_1 (no se ejecuta sobre datos inexistentes).
        step_results = result.get("step_results") or []
        assert len(step_results) == 2
        assert step_results[0]["success"] is False          # step_1 falló
        assert step_results[1].get("skipped") is True       # step_2 saltado (dep)
        # El plan reporta partial_failure (success=False global).
        assert result.get("success") is False


class TestSmartRouterSkip:
    """Smart Router (2026-05-31): skip de data/gis cuando hay capa cargada.

    Cuando el usuario tiene una capa activa (state.geojson o
    previous_geojson), el router debe enrutarlo directamente al agente
    apropiado (symbology o python) sin pasar por data_agent/gis_agent.
    """

    @pytest.mark.asyncio
    async def test_apply_symbology_skips_data_and_gis(self) -> None:
        """Intent ``apply_symbology`` con capa activa → directo a symbology."""
        from unittest.mock import patch

        router_decision = {
            "intent": "apply_symbology",
            "reasoning": "el usuario solo quiere cambiar el color",
            "response": "",
            "entities": [],
            "is_multi_step": False,
            "additional_operations": [],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision)
        graph.insights_agent.infer_visualization_type = AsyncMock(
            return_value={"type": "map", "feature_count": 2}
        )

        # Mock symbology + insights nodes; data/gis NO deben ser invocados.
        data_mock = AsyncMock(return_value={})
        gis_mock = AsyncMock(return_value={})
        sym_mock = AsyncMock(return_value={
            "symbology": {"fill": {"color": "#00ff00"}, "layer_title": "Verde"},
            "layer_name": "Verde",
            "current_agent": "symbology_agent",
            "messages": [],
        })
        with patch(
            "geo_copilot.orchestrator.nodes.data_agent.run", new=data_mock
        ), patch(
            "geo_copilot.orchestrator.nodes.gis_agent.run", new=gis_mock
        ), patch(
            "geo_copilot.orchestrator.nodes.symbology.run", new=sym_mock
        ), patch(
            "geo_copilot.orchestrator.nodes.insights.run",
            new=AsyncMock(return_value={}),
        ):
            result = await graph.process(
                query="cámbiale el color a verde",
                session_id="s-smart-sym",
                previous_geojson={
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature", "geometry": {"type": "Polygon"},
                         "properties": {"id": 1}},
                        {"type": "Feature", "geometry": {"type": "Polygon"},
                         "properties": {"id": 2}},
                    ],
                },
                active_data_source="internal",
                active_source_name="construcciones",
            )

        # symbology_agent SÍ se invocó; data_agent y gis_agent NO.
        assert sym_mock.await_count == 1, "symbology_agent debe ejecutarse"
        assert data_mock.await_count == 0, "data_agent NO debe ejecutarse (skip Smart Router)"
        assert gis_mock.await_count == 0, "gis_agent NO debe ejecutarse (skip Smart Router)"
        assert result.get("success") is True
        assert result.get("symbology", {}).get("fill", {}).get("color") == "#00ff00"

    @pytest.mark.asyncio
    async def test_spatial_operation_works_on_previous_geojson(self) -> None:
        """``spatial_operation`` debe funcionar con previous_geojson sin
        requerir ``has_external_data=True`` ni re-consultar la BD."""
        from unittest.mock import patch

        router_decision = {
            "intent": "spatial_operation",
            "reasoning": "buffer sobre la capa cargada",
            "response": "",
            "entities": [],
            "is_multi_step": False,
            "additional_operations": ["buffer 500m"],
        }
        # NOTE: el additional_operations dispararía planner. Para aislar
        # solo el routing del intent simple, pasamos en blanco la lista.
        router_decision["additional_operations"] = []
        graph, _ = _build_graph_with_mock_llm(router_decision)
        graph.insights_agent.infer_visualization_type = AsyncMock(
            return_value={"type": "map", "feature_count": 1}
        )

        data_mock = AsyncMock(return_value={})
        gis_mock = AsyncMock(return_value={})
        py_mock = AsyncMock(return_value={
            "geojson": {"type": "FeatureCollection", "features": [{"a": "buffered"}]},
            "raw_data": [{"a": "buffered"}],
            "python_code": "buffer(...)",
            "active_data_source": "internal",
        })
        # camino CABLEADO (react_policy='off'): en 'hybrid' decide el bucle ReAct, que tiene
        # las herramientas exactas del workspace además de spatial_operation (test abajo)
        from geo_copilot.core.config import get_settings as _gs
        cableado = _gs().model_copy(update={"react_policy": "off"})
        with patch("geo_copilot.orchestrator.graph.get_settings", return_value=cableado), patch(
            "geo_copilot.orchestrator.nodes.data_agent.run", new=data_mock
        ), patch(
            "geo_copilot.orchestrator.nodes.gis_agent.run", new=gis_mock
        ), patch(
            "geo_copilot.orchestrator.nodes.python_agent.run", new=py_mock
        ), patch(
            "geo_copilot.orchestrator.nodes.symbology.run",
            new=AsyncMock(return_value={"symbology": {}, "layer_name": "x"}),
        ), patch(
            "geo_copilot.orchestrator.nodes.insights.run",
            new=AsyncMock(return_value={}),
        ):
            result = await graph.process(
                query="hazle buffer de 500 metros",
                session_id="s-smart-spatial",
                previous_geojson={
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "geometry": {"type": "Point"},
                                  "properties": {"id": 1}}],
                },
                active_data_source="internal",
            )

        assert py_mock.await_count == 1, "python_agent debe ejecutar la op espacial"
        assert data_mock.await_count == 0, "data_agent NO debe re-consultar"
        assert gis_mock.await_count == 0, "gis_agent NO debe re-ejecutar SQL"
        assert result.get("success") is True

    @pytest.mark.asyncio
    async def test_spatial_operation_en_hybrid_la_decide_el_bucle_react(self) -> None:
        """V5 en Chrome (FH): «el área de cada lote» iba directo al sandbox sin que el LLM
        viera ws_add_measure. En hybrid (default) va al bucle, que ofrece ambas."""
        from unittest.mock import patch

        router_decision = {"intent": "spatial_operation", "reasoning": "área de cada lote", "response": "",
                           "entities": [], "is_multi_step": False, "additional_operations": []}
        graph, _ = _build_graph_with_mock_llm(router_decision)
        loop_mock = AsyncMock(return_value={"final_response": "ok", "success": True})
        py_mock = AsyncMock(return_value={})
        with patch("geo_copilot.orchestrator.nodes.agent_loop.run", new=loop_mock), patch(
            "geo_copilot.orchestrator.nodes.python_agent.run", new=py_mock
        ), patch("geo_copilot.orchestrator.nodes.insights.run", new=AsyncMock(return_value={})):
            await graph.process(
                query="calcula el área de cada lote", session_id="s-hybrid-spatial",
                previous_geojson={"type": "FeatureCollection", "features": [
                    {"type": "Feature", "geometry": {"type": "Point"}, "properties": {"id": 1}}]},
                active_data_source="internal",
            )
        assert loop_mock.await_count == 1 and py_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_apply_symbology_without_active_layer_honest_message(self) -> None:
        """ORQ-12: sin capa activa, ``apply_symbology`` NO genera SQL (antes se
        degradaba a data_agent→gis_agent pidiendo SQL a partir de «ponlo rojo»).
        Responde con un mensaje honesto sobre la falta de capa."""
        from unittest.mock import patch

        router_decision = {
            "intent": "apply_symbology",
            "reasoning": "el usuario pidió color sin tener datos",
            "response": "",
            "entities": [],
            "is_multi_step": False,
            "additional_operations": [],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision)
        graph.insights_agent.infer_visualization_type = AsyncMock(
            return_value={"type": "empty"}
        )

        data_mock = AsyncMock(return_value={"current_agent": "data_agent"})
        with patch(
            "geo_copilot.orchestrator.nodes.data_agent.run", new=data_mock
        ):
            result = await graph.process(
                query="cámbialo a rojo",
                session_id="s-no-layer",
                # Sin previous_geojson, sin external_geojson.
            )

        # NO se degrada a data_agent (no genera SQL para una petición de estilo).
        assert data_mock.await_count == 0
        # Responde con un mensaje honesto que menciona la falta de capa.
        _resp = (result.get("final_response") or result.get("message") or "").lower()
        assert "capa" in _resp or "carga" in _resp


class TestReturnShape:
    """The public return shape of ``process()`` is part of the API contract.

    Routes (``/query``, the WebSocket handler) read specific keys from
    this dict; renaming or dropping them is a breaking change.
    """

    @pytest.mark.asyncio
    async def test_return_shape_keys_are_stable(self) -> None:
        router_decision = {
            "intent": "direct_response",
            "reasoning": "x",
            "response": "ok",
            "entities": [],
            "is_multi_step": False,
            "additional_operations": [],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision)

        result = await graph.process(query="hola", session_id="s-shape")

        # These are consumed by the API and the WebSocket handler.
        expected_keys = {
            "success", "message", "sql", "data", "geojson",
            "symbology", "layer_name", "agent_messages",
            "visualization", "found_services",
            "external_geojson", "external_source_name", "has_external_data",
            "python_code", "pending_operations",
        }
        missing = expected_keys - set(result.keys())
        assert not missing, f"public return shape lost keys: {missing}"

    @pytest.mark.asyncio
    async def test_data_results_is_a_dict_with_results_list(self) -> None:
        """``data`` is always ``{"results": list-or-None}``."""
        router_decision = {
            "intent": "direct_response",
            "reasoning": "x",
            "response": "ok",
            "entities": [],
            "is_multi_step": False,
            "additional_operations": [],
        }
        graph, _ = _build_graph_with_mock_llm(router_decision)

        result = await graph.process(query="hola", session_id="s-shape2")

        assert isinstance(result.get("data"), dict)
        assert "results" in result["data"]
