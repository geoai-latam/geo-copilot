"""Tests unitarios de nodos del orquestador.

Cobertura focalizada en el contrato de estado de ``step_router``,
``step_finalizer`` y ``planner`` — en particular la regresión B4: la
``query`` original del turno se sobrescribía con el fragmento del último
step y nunca se restauraba, así que ``responder``/``insights`` generaban
narrativa/visualización contra la consulta equivocada.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.orchestrator.nodes import planner as planner_node
from geo_copilot.orchestrator.nodes import step_finalizer, step_router


def _plan():
    return [
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
    ]


class TestStepRouterOverwritesQuery:
    @pytest.mark.asyncio
    async def test_query_is_overwritten_with_fragment(self):
        """Documenta el comportamiento: step_router pisa query con el fragmento."""
        graph = MagicMock()
        state = {
            "execution_plan": _plan(),
            "current_step_index": 0,
            "query": "trae construcciones y coloréalas de rojo",
            "session_id": "",
        }

        updates = await step_router.run(graph, state)

        assert updates["query"] == "trae todas las construcciones"
        assert updates["intent"] == "query_data"


class TestStepFinalizerRestoresQuery:
    """Regresión B4: la query original debe restaurarse al cerrar cada step."""

    @pytest.mark.asyncio
    async def test_restores_original_query_on_plan_completion(self):
        graph = MagicMock()
        original = "trae construcciones y coloréalas de rojo"
        state = {
            "execution_plan": _plan(),
            "current_step_index": 1,  # último step
            "original_query": original,
            "query": "aplica color rojo",  # fragmento del step actual
            "geojson": {"type": "FeatureCollection", "features": [{"a": 1}]},
            "session_id": "",
            "step_results": [{"step_id": "step_1", "success": True}],
        }

        updates = await step_finalizer.run(graph, state)

        assert updates["query"] == original

    @pytest.mark.asyncio
    async def test_restores_original_query_on_failed_step(self):
        graph = MagicMock()
        original = "consulta original del usuario"
        state = {
            "execution_plan": _plan(),
            "current_step_index": 0,
            "original_query": original,
            "query": "trae todas las construcciones",
            "error": "falló la SQL",
            "session_id": "",
            "step_results": [],
        }

        updates = await step_finalizer.run(graph, state)

        assert updates["query"] == original

    @pytest.mark.asyncio
    async def test_no_original_query_keeps_current(self):
        """Sin original_query (plan sin planner) no rompe: deja la query actual."""
        graph = MagicMock()
        state = {
            "execution_plan": _plan(),
            "current_step_index": 1,
            "query": "aplica color rojo",
            "geojson": {"type": "FeatureCollection", "features": [{"a": 1}]},
            "session_id": "",
            "step_results": [{"step_id": "step_1", "success": True}],
        }

        updates = await step_finalizer.run(graph, state)

        assert updates["query"] == "aplica color rojo"


class TestPlannerCapturesOriginalQuery:
    @pytest.mark.asyncio
    async def test_planner_sets_original_query(self):
        graph = MagicMock()
        graph._get_cached_schema = AsyncMock(return_value="schema")
        graph.planner_agent = MagicMock()
        graph.planner_agent.process = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={"steps": _plan(), "reasoning": "plan"},
            )
        )

        state = {"query": "consulta original", "session_id": ""}
        updates = await planner_node.run(graph, state)

        assert updates["original_query"] == "consulta original"
        assert updates["execution_plan"]


class TestRouterNodeIntentGuard:
    """Regresión C3a-2: intent ausente no debe lanzar KeyError en el nodo."""

    @pytest.mark.asyncio
    async def test_missing_intent_falls_back_not_raises(self):
        from geo_copilot.agents.base import AgentResponse
        from geo_copilot.orchestrator.nodes import router as router_node

        graph = MagicMock()
        graph._get_cached_schema = AsyncMock(return_value="schema")
        graph.conversation_manager = None
        graph.router_agent = MagicMock()
        # success=True pero data SIN 'intent'.
        graph.router_agent.process = AsyncMock(
            return_value=AgentResponse(
                success=True, message="ok", data={"reasoning": "x"}
            )
        )

        state = {"query": "hola", "session_id": "", "active_data_source": "none"}
        # No debe lanzar; cae al fallback con intent direct_response.
        updates = await router_node.run(graph, state)

        assert updates["intent"] == "direct_response"

    @pytest.mark.asyncio
    async def test_valid_intent_passes_through(self):
        from geo_copilot.agents.base import AgentResponse
        from geo_copilot.orchestrator.nodes import router as router_node

        graph = MagicMock()
        graph._get_cached_schema = AsyncMock(return_value="schema")
        graph.conversation_manager = None
        graph.router_agent = MagicMock()
        graph.router_agent.process = AsyncMock(
            return_value=AgentResponse(
                success=True,
                message="ok",
                data={"intent": "query_data", "reasoning": "r"},
            )
        )

        state = {"query": "trae parcelas", "session_id": "", "active_data_source": "none"}
        updates = await router_node.run(graph, state)

        assert updates["intent"] == "query_data"


class TestAnalyzeHonestDegradation:
    """§5: 'analyze' sin capa activa marca la degradación honesta que el
    responder usa para avisar en vez de mostrar datos en silencio.

    A2: la degradación aplica al camino CABLEADO — con react_policy='hybrid'
    (default actual) este caso va al bucle ReAct y NO es degradación (cubierto
    en test_fase4_react_analitico). Aquí se fija 'off' explícito.
    """

    async def _run_analyze(self, state_extra: dict):
        from unittest.mock import patch

        from geo_copilot.agents.base import AgentResponse
        from geo_copilot.core.config import get_settings as _gs
        from geo_copilot.orchestrator.nodes import router as router_node

        graph = MagicMock()
        graph._get_cached_schema = AsyncMock(return_value="schema")
        graph.conversation_manager = None
        graph.router_agent = MagicMock()
        graph.router_agent.process = AsyncMock(
            return_value=AgentResponse(
                success=True, message="ok", data={"intent": "analyze", "reasoning": "r"}
            )
        )
        state = {"query": "agrupa en clusters", "session_id": "", "active_data_source": "none"}
        state.update(state_extra)
        pinned = _gs().model_copy(update={"react_policy": "off"})
        with patch("geo_copilot.core.config.get_settings", return_value=pinned):
            return await router_node.run(graph, state)

    @pytest.mark.asyncio
    async def test_analyze_sin_capa_marca_degradacion(self):
        updates = await self._run_analyze({})
        assert updates["intent"] == "analyze"
        assert updates["analyze_degraded_no_layer"] is True

    @pytest.mark.asyncio
    async def test_analyze_con_geojson_activo_no_marca(self):
        updates = await self._run_analyze(
            {"geojson": {"type": "FeatureCollection", "features": [{"x": 1}]}}
        )
        assert updates["analyze_degraded_no_layer"] is False

    @pytest.mark.asyncio
    async def test_analyze_con_previous_geojson_no_marca(self):
        updates = await self._run_analyze(
            {"previous_geojson": {"type": "FeatureCollection", "features": [{"x": 1}]}}
        )
        assert updates["analyze_degraded_no_layer"] is False

    @pytest.mark.asyncio
    async def test_analyze_con_datos_externos_no_marca(self):
        updates = await self._run_analyze({"has_external_data": True})
        assert updates["analyze_degraded_no_layer"] is False


class TestResponderHonestDegradationNote:
    """El responder antepone el aviso honesto cuando el router marcó la
    degradación de 'analyze' sin capa."""

    async def _run_responder(self, state_extra: dict):
        from geo_copilot.orchestrator.nodes import responder as responder_node

        graph = MagicMock()
        graph.insights_agent = MagicMock()
        graph.insights_agent.infer_visualization_type = AsyncMock(return_value=None)
        state = {
            "query": "agrupa en clusters",
            "final_response": "Traje 200 registros.",
            "raw_data": [{"a": 1}],
            # visualization presente → el responder no llama al insights_agent.
            "visualization": {"type": "table"},
        }
        state.update(state_extra)
        return await responder_node.run(graph, state)

    @pytest.mark.asyncio
    async def test_antepone_aviso_cuando_degradado(self):
        updates = await self._run_responder({"analyze_degraded_no_layer": True})
        assert "No había una capa activa" in updates["final_response"]
        assert "Traje 200 registros." in updates["final_response"]

    @pytest.mark.asyncio
    async def test_sin_flag_no_antepone(self):
        updates = await self._run_responder({})
        assert updates["final_response"] == "Traje 200 registros."

    @pytest.mark.asyncio
    async def test_no_antepone_si_hay_error(self):
        updates = await self._run_responder(
            {"analyze_degraded_no_layer": True, "error": "boom"}
        )
        assert "No había una capa activa" not in (updates["final_response"] or "")


class TestSelectSecondaryData:
    """Cross-source: la 2ª capa cargada (distinta de la activa) se expone como gdf2."""

    def _fc(self, n=1):
        return {"type": "FeatureCollection", "features": [{"x": i} for i in range(n)]}

    def test_activa_interna_secundaria_es_externa(self):
        from geo_copilot.orchestrator.nodes.python_agent import _select_secondary_data
        internal = self._fc()
        external = self._fc(2)
        state = {"geojson": internal, "external_geojson": external, "external_source_name": "REST X"}
        gj, name = _select_secondary_data(state, "internal", internal)
        assert gj is external
        assert name == "REST X"

    def test_activa_externa_secundaria_es_interna(self):
        from geo_copilot.orchestrator.nodes.python_agent import _select_secondary_data
        internal = self._fc()
        external = self._fc(2)
        state = {"geojson": internal, "external_geojson": external}
        gj, _ = _select_secondary_data(state, "external", external)
        assert gj is internal

    def test_una_sola_capa_no_hay_secundaria(self):
        from geo_copilot.orchestrator.nodes.python_agent import _select_secondary_data
        internal = self._fc()
        state = {"geojson": internal}
        gj, name = _select_secondary_data(state, "internal", internal)
        assert gj is None and name is None

    def test_no_devuelve_la_misma_capa_primaria(self):
        from geo_copilot.orchestrator.nodes.python_agent import _select_secondary_data
        prev = self._fc()
        # previous == primary → no debe devolverse como secundaria
        state = {"previous_geojson": prev}
        gj, _ = _select_secondary_data(state, "previous", prev)
        assert gj is None


class TestPlannerWSFailureDoesNotDropPlan:
    """Regresión C3a-3: un fallo de WS no debe descartar un plan válido."""

    @pytest.mark.asyncio
    async def test_ws_failure_keeps_plan(self, monkeypatch):
        graph = MagicMock()
        graph._get_cached_schema = AsyncMock(return_value="schema")
        graph.planner_agent = MagicMock()
        graph.planner_agent.process = AsyncMock(
            return_value=MagicMock(success=True, data={"steps": _plan(), "reasoning": "r"})
        )

        # El sink de eventos revienta (WS caído). S1.2: el nodo emite por
        # platform.events; parchear api.websocket ya no probaría nada.
        from geo_copilot.platform import events

        class _SinkRoto(events.NullSink):
            async def plan_created(self, *a, **k):
                raise RuntimeError("WS caído")

        monkeypatch.setattr(events, "_sink", _SinkRoto())

        state = {"query": "q", "session_id": "s-ws"}
        updates = await planner_node.run(graph, state)

        # El plan sobrevive pese al fallo de WS.
        assert updates["execution_plan"]
        assert updates.get("error") is None


class TestStepRouterSelectServiceMultiStep:
    """ORQ-04: un paso select_service dentro de un plan multi-paso resuelve el
    número→URL contra found_services y despacha como load_external (antes: sin
    external_url, data_agent 'validaba la BD' en silencio y no cargaba nada)."""

    def _plan_select(self, fragment: str):
        # select_service como PRIMER paso: found_services viene de un turno
        # previo (p. ej. "carga el 2 y píntalo de rojo" tras haber buscado).
        return [
            {
                "step_id": "step_1",
                "action_type": "select_service",
                "description": "Cargar el servicio elegido",
                "query_fragment": fragment,
            },
            {
                "step_id": "step_2",
                "action_type": "symbology",
                "description": "Color rojo",
                "query_fragment": "aplica color rojo",
            },
        ]

    def _services(self):
        return [
            {"name": "Bomberos A", "url": "https://a.example.com/FeatureServer/0"},
            {"name": "Bomberos B", "url": "https://b.example.com/FeatureServer/0"},
        ]

    @pytest.mark.asyncio
    async def test_numero_resuelve_a_load_external(self):
        graph = MagicMock()
        state = {
            "execution_plan": self._plan_select("carga el 2"),
            "current_step_index": 0,
            "found_services": self._services(),
            "session_id": "",
        }
        updates = await step_router.run(graph, state)
        assert updates["intent"] == "load_external"
        assert updates["external_url"] == "https://b.example.com/FeatureServer/0"

    @pytest.mark.asyncio
    async def test_el_numero_lo_decide_el_planner_no_el_texto(self):
        """Auditoría pre-producción: «carga el de 2024» se leía como el servicio 2024 (regex).
        Con `service_number` (campo estructurado del planner) el texto no se interpreta."""
        plan = self._plan_select("carga el servicio de 2024")
        plan[0]["service_number"] = 1
        updates = await step_router.run(MagicMock(), {
            "execution_plan": plan, "current_step_index": 0,
            "found_services": self._services(), "session_id": "",
        })
        assert updates["intent"] == "load_external"
        assert updates["external_url"] == "https://a.example.com/FeatureServer/0"

    @pytest.mark.asyncio
    async def test_ordinal_espanol_resuelve(self):
        graph = MagicMock()
        state = {
            "execution_plan": self._plan_select("carga el primero"),
            "current_step_index": 0,
            "found_services": self._services(),
            "session_id": "",
        }
        updates = await step_router.run(graph, state)
        assert updates["intent"] == "load_external"
        assert updates["external_url"] == "https://a.example.com/FeatureServer/0"

    @pytest.mark.asyncio
    async def test_sin_servicios_falla_honesto(self):
        graph = MagicMock()
        state = {
            "execution_plan": self._plan_select("carga el 1"),
            "current_step_index": 0,
            "found_services": [],  # nunca hubo búsqueda previa cargada
            "session_id": "",
        }
        updates = await step_router.run(graph, state)
        # NO se despacha a data_agent como 'ok': fallo honesto → step_finalizer.
        assert updates["intent"] != "load_external"
        assert "external_url" not in updates
        assert updates.get("error")
        assert "servicios" in updates["error"].lower()

    @pytest.mark.asyncio
    async def test_fuera_de_rango_falla_honesto(self):
        graph = MagicMock()
        state = {
            "execution_plan": self._plan_select("carga el 5"),
            "current_step_index": 0,
            "found_services": self._services(),  # solo 2
            "session_id": "",
        }
        updates = await step_router.run(graph, state)
        assert updates["intent"] != "load_external"
        assert "excede" in (updates.get("error") or "").lower()
