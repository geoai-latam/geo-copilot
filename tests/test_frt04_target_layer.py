"""FRT-04: selección de capa OBJETIVO por nombre (unit deterministas).

Verifican que, con `target_layer_id` (ya validado) + `map_layers` en el estado,
los puntos de resolución de capa (symbology, python) operan sobre
LA CAPA OBJETIVO, no la activa — y que sin target caen a la cascada de siempre
(no-regresión). Más la validación del id (router node + react_tools).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.orchestrator.nodes._layer_context import resolve_active_layer
from geo_copilot.orchestrator.nodes.python_agent import _select_working_data
from geo_copilot.orchestrator.react_tools import _resolve_target


def _fc(n: int, tag: str):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {"id": i, "capa": tag}}
            for i in range(n)
        ],
    }


PREDIOS = _fc(3, "predios")
RIOS = _fc(5, "rios")
MAP_LAYERS = {
    "layer-predios": {"data": PREDIOS, "name": "Predios"},
    "layer-rios": {"data": RIOS, "name": "Ríos"},
}


# ── symbology: resolve_active_layer ────────────────────────────────────────
class TestResolveActiveLayerTarget:
    def test_target_gana_sobre_la_activa(self):
        # La activa (external_geojson) es Ríos, pero el target es Predios.
        state = {
            "target_layer_id": "layer-predios",
            "map_layers": MAP_LAYERS,
            "external_geojson": RIOS,  # la "activa" inyectada
            "external_source_name": "Ríos",
        }
        gj, name, kind = resolve_active_layer(state)
        assert gj is PREDIOS  # operó sobre PREDIOS, no la activa
        assert name == "Predios"

    def test_sin_target_cae_a_la_activa(self):
        state = {"map_layers": MAP_LAYERS, "external_geojson": RIOS,
                 "external_source_name": "Ríos"}
        gj, name, _ = resolve_active_layer(state)
        assert gj is RIOS  # sin target → la activa de siempre (no-regresión)

    def test_target_inexistente_ignorado(self):
        # target ya validado en el router; aquí, por defensa, un id que no está
        # en map_layers no debe usarse (cae a la activa).
        state = {"target_layer_id": "layer-fantasma", "map_layers": MAP_LAYERS,
                 "external_geojson": RIOS}
        gj, _, _ = resolve_active_layer(state)
        assert gj is RIOS


# ── python (spatial/analyze): _select_working_data ─────────────────────────
class TestSelectWorkingDataTarget:
    def test_target_gana(self):
        state = {
            "target_layer_id": "layer-predios",
            "map_layers": MAP_LAYERS,
            "active_data_source": "external",
            "external_geojson": RIOS,
        }
        gj, name, _ = _select_working_data(state)
        assert gj is PREDIOS
        assert name == "Predios"

    def test_sin_target_no_regresa(self):
        state = {"active_data_source": "external", "external_geojson": RIOS,
                 "external_source_name": "Ríos", "map_layers": MAP_LAYERS}
        gj, _, _ = _select_working_data(state)
        assert gj is RIOS


# ── validación del id (react_tools._resolve_target) ────────────────────────
class TestResolveTargetValidation:
    def test_id_valido(self):
        assert _resolve_target({"target_layer_id": "layer-predios"},
                               {"map_layers": MAP_LAYERS}) == "layer-predios"

    def test_id_alucinado_es_none(self):
        assert _resolve_target({"target_layer_id": "layer-inventada"},
                               {"map_layers": MAP_LAYERS}) is None

    def test_sin_id_es_none(self):
        assert _resolve_target({}, {"map_layers": MAP_LAYERS}) is None

    def test_sin_map_layers_es_none(self):
        assert _resolve_target({"target_layer_id": "layer-predios"}, {}) is None

    # ── fallback al target del nodo router (bug de clobbering en el bucle) ──
    def test_fallback_al_target_del_estado(self):
        # El LLM del bucle NO repitió el arg, pero el nodo router YA resolvió la
        # capa nombrada al estado. Sin este fallback, el override None de la tool
        # pisaba esa resolución y la simbología caía a la activa (bug en vivo).
        assert _resolve_target(
            {}, {"map_layers": MAP_LAYERS, "target_layer_id": "layer-predios"}
        ) == "layer-predios"

    def test_arg_explicito_gana_sobre_el_estado(self):
        # Si el LLM SÍ dirige la tool a una capa (per-tool, multi-paso), su arg
        # tiene prioridad sobre el target de query-level del router.
        assert _resolve_target(
            {"target_layer_id": "layer-rios"},
            {"map_layers": MAP_LAYERS, "target_layer_id": "layer-predios"},
        ) == "layer-rios"

    def test_arg_alucinado_no_pisa_el_target_valido_del_estado(self):
        # Un id inventado en el arg NO debe borrar la resolución válida del
        # router: se descarta el arg y se cae al target del estado.
        assert _resolve_target(
            {"target_layer_id": "layer-inventada"},
            {"map_layers": MAP_LAYERS, "target_layer_id": "layer-predios"},
        ) == "layer-predios"


# ── router node: escribe target_layer_id VALIDADO al estado ────────────────
class TestRouterNodeValidatesTarget:
    async def _run(self, router_target: str | None, map_layers: dict):
        from geo_copilot.agents.base import AgentResponse
        from geo_copilot.orchestrator.nodes import router as router_node

        graph = MagicMock()
        graph._get_cached_schema = AsyncMock(return_value="schema")
        graph.conversation_manager = None
        graph.router_agent = MagicMock()
        graph.router_agent.process = AsyncMock(return_value=AgentResponse(
            success=True, message="ok",
            data={"intent": "apply_symbology", "reasoning": "r",
                  "target_layer_id": router_target},
        ))
        state = {"query": "colorea los predios", "session_id": "",
                 "active_data_source": "external", "external_geojson": RIOS,
                 "has_external_data": True, "map_layers": map_layers}
        return await router_node.run(graph, state)

    @pytest.mark.asyncio
    async def test_id_valido_se_escribe(self):
        updates = await self._run("layer-predios", MAP_LAYERS)
        assert updates["target_layer_id"] == "layer-predios"

    @pytest.mark.asyncio
    async def test_id_alucinado_se_ignora(self):
        updates = await self._run("layer-inexistente", MAP_LAYERS)
        assert updates["target_layer_id"] is None


# ── agent_loop: el prompt ReAct EXPONE las capas + la regla de target ──────
# Sin esto el LLM del bucle no conoce los ids y nunca puede dirigir una tool a
# la capa NOMBRADA (bug destapado en validación en vivo: "colorea los lotes"
# coloreaba la activa porque la query entró por hybrid → agent_loop).
class TestReactPromptExposesLayers:
    def _state_2capas(self, query: str) -> dict:
        return {
            "query": query,
            "map_context": {"layers": [
                {"id": "layer-predios", "name": "Predios", "is_active": False,
                 "geometry_type": "Polygon", "feature_count": 3, "fields": ["uso"]},
                {"id": "layer-rios", "name": "Ríos", "is_active": True,
                 "geometry_type": "LineString", "feature_count": 5, "fields": ["nombre"]},
            ]},
            "map_layers": MAP_LAYERS,
        }

    async def _run_capturing_prompt(self, state: dict, *, cost_hint: str = ""):
        from unittest.mock import patch

        from geo_copilot.core.scripted_llm import ScriptedLLM, tool_call_response
        from geo_copilot.orchestrator.nodes import agent_loop

        llm = ScriptedLLM([tool_call_response("answer", {"text": "listo"})])
        graph = MagicMock()
        graph.llm = llm
        graph.agent_metrics = (
            MagicMock(cost_hint=MagicMock(return_value=cost_hint)) if cost_hint else None
        )
        with patch.object(agent_loop, "get_settings",
                          return_value=MagicMock(react_max_reflections=0,
                                                 react_max_tool_calls=8,
                                                 react_token_budget=0)):
            await agent_loop.run(graph, state)
        return llm.calls[0]["messages"][0].content  # system prompt del 1er turno

    @pytest.mark.asyncio
    async def test_prompt_lista_las_capas_con_id(self):
        sys_msg = await self._run_capturing_prompt(
            self._state_2capas("colorea los predios de rojo"))
        assert "[layer-predios]" in sys_msg
        assert "[layer-rios]" in sys_msg
        assert "Predios" in sys_msg

    @pytest.mark.asyncio
    async def test_prompt_incluye_la_regla_de_target(self):
        sys_msg = await self._run_capturing_prompt(
            self._state_2capas("colorea los predios de rojo"))
        assert "CAPA OBJETIVO" in sys_msg
        assert "target_layer_id" in sys_msg

    @pytest.mark.asyncio
    async def test_hint_de_costo_no_borra_las_capas(self):
        """F0 / S0.1b: con historial suficiente aparece el hint de costo, y antes
        el prompt se reconstruía desde REACT_SYSTEM_PROMPT: el bloque de capas y
        la regla de capa objetivo desaparecían justo en sesiones largas."""
        sys_msg = await self._run_capturing_prompt(
            self._state_2capas("colorea los predios de rojo"),
            cost_hint="HISTÓRICO DE SESIÓN: 3 turnos",
        )
        assert "HISTÓRICO DE SESIÓN" in sys_msg
        assert "[layer-predios]" in sys_msg
        assert "CAPA OBJETIVO" in sys_msg

    @pytest.mark.asyncio
    async def test_sin_map_context_no_agrega_bloque(self):
        # No-regresión: sin capas, el prompt no gana el bloque (ni la regla).
        sys_msg = await self._run_capturing_prompt({"query": "hola"})
        assert "CAPA OBJETIVO" not in sys_msg
        assert "CAPAS EN EL MAPA" not in sys_msg


# ── contrato de respuesta: AMBOS mapeadores propagan target_layer_id ───────
# El E2E nivel 3 destapó que _map_final_state y _run_react_mode NO incluían
# target_layer_id → query.py caía a la capa ACTIVA → el cliente re-estilaba la
# activa, no la nombrada (el backend sí operaba sobre la correcta; el hueco era
# el mapeo a la API). Estos tests fijan el contrato.
class TestResponseCarriesTarget:
    def test_map_final_state_incluye_target(self):
        from geo_copilot.orchestrator.graph import GeoAgentGraph

        graph = MagicMock()
        out = GeoAgentGraph._map_final_state(graph, {
            "intent": "apply_symbology",
            "target_layer_id": "layer-predios",
            "final_response": "coloreado",
            "decision_trace": [{"kind": "final"}],
        })
        assert out["target_layer_id"] == "layer-predios"

    @pytest.mark.asyncio
    async def test_run_react_mode_incluye_target(self):
        from unittest.mock import patch

        from geo_copilot.orchestrator.graph import GeoAgentGraph

        graph = MagicMock()
        graph.insights_agent.infer_visualization_type = AsyncMock(return_value=None)
        with patch("geo_copilot.orchestrator.nodes.agent_loop.run",
                   new=AsyncMock(return_value={
                       "final_response": "coloreado",
                       "target_layer_id": "layer-predios",
                       "decision_trace": [{"kind": "final"}],
                   })):
            out = await GeoAgentGraph._run_react_mode(
                graph, {"query": "colorea los predios de rojo"})
        assert out["target_layer_id"] == "layer-predios"


# ── F0 / S0.1a: el schema `route` DECLARA target_layer_id ──────────────────
# Los tests de arriba simulan `router_agent.process` entero, así que nunca
# pasaban por el schema. Con `additionalProperties: False` y sin la propiedad,
# un proveedor con structured outputs estrictos NO puede devolver el campo:
# FRT-04 quedaba muerto en el camino cableado aunque el prompt lo pidiera.
class TestRouteSchemaDeclaresTarget:
    async def _route(self, args: dict):
        from geo_copilot.agents.router_agent.agent import RouterAgent
        from geo_copilot.core.scripted_llm import tool_call_response

        router = RouterAgent(llm_client=MagicMock())
        router.llm_client.chat = AsyncMock(
            return_value=tool_call_response("route", args)
        )
        resp = await router.process(
            query="colorea los predios de rojo",
            context={"found_services": [], "map_context": None,
                     "schema_info": "", "conversation_history": []},
        )
        tool = router.llm_client.chat.call_args.kwargs["tools"][0]
        return resp, tool["function"]["parameters"]

    @pytest.mark.asyncio
    async def test_schema_incluye_target_layer_id(self):
        _, params = await self._route({"intent": "apply_symbology", "reasoning": "r"})
        assert "target_layer_id" in params["properties"]
        assert params["properties"]["target_layer_id"]["type"] == ["string", "null"]

    @pytest.mark.asyncio
    async def test_target_llega_a_la_respuesta(self):
        resp, _ = await self._route({
            "intent": "apply_symbology", "reasoning": "r",
            "target_layer_id": "layer-predios",
        })
        assert resp.data["target_layer_id"] == "layer-predios"

    @pytest.mark.asyncio
    async def test_todo_lo_que_se_lee_esta_declarado(self):
        """Cada campo que `process` lee del resultado de `route` debe estar en
        el schema: con `additionalProperties: False`, lo no declarado no llega."""
        import inspect
        import re

        from geo_copilot.agents.router_agent import agent as router_mod

        _, params = await self._route({"intent": "direct_response", "reasoning": "r",
                                       "response": "hola"})
        leidos = set(re.findall(r'result\.get\("(\w+)"', inspect.getsource(router_mod)))
        faltan = leidos - set(params["properties"])
        assert not faltan, f"campos leídos pero no declarados en `route`: {faltan}"


# ── V5 F4 (T4.9): una capa traída en el turno no queda eclipsada por el objetivo previo ──
class TestCapaDelTurnoGanaAlObjetivoPrevio:
    """«trae los lotes de la manzana 004503001, grafica su área y coloréalos»: el
    router resolvió como objetivo la capa de lotes que YA estaba en el mapa (otra
    manzana). Tras traer los nuevos, el análisis y la simbología siguieron cayendo a
    ese objetivo: gráfico y colores de la manzana vieja."""

    async def _traer(self, monkeypatch, working):
        from geo_copilot.orchestrator import capabilities_core
        from geo_copilot.orchestrator.nodes import gis_agent as gis_node

        nuevos = _fc(15, "manzana-nueva")
        monkeypatch.setattr(gis_node, "run", AsyncMock(return_value={"geojson": nuevos, "sql": "SELECT 1"}))
        out = await capabilities_core._query_database(None, working, {"request": "trae los lotes"})
        working.update(out.delta)
        return nuevos

    async def test_tras_traer_datos_el_objetivo_previo_ya_no_es_el_defecto(self, monkeypatch):
        from geo_copilot.orchestrator.layer_resolution import resolver_capa

        working = {"map_layers": MAP_LAYERS, "target_layer_id": "layer-predios"}
        working["active_source_name"] = "Lotes Manzana 004503009"  # el nombre del turno anterior
        nuevos = await self._traer(monkeypatch, working)
        assert _resolve_target({}, working) is None
        capa = resolver_capa(working)
        assert capa.geojson is nuevos
        # y se llama por lo que se pidió, no por la capa de antes
        assert capa.name == "trae los lotes"

    async def test_el_llm_aun_puede_dirigir_la_accion_a_otra_capa(self, monkeypatch):
        working = {"map_layers": MAP_LAYERS, "target_layer_id": "layer-predios"}
        await self._traer(monkeypatch, working)
        assert _resolve_target({"target_layer_id": "layer-rios"}, working) == "layer-rios"

    async def test_sin_datos_nuevos_el_objetivo_se_conserva(self, monkeypatch):
        from geo_copilot.orchestrator import capabilities_core
        from geo_copilot.orchestrator.nodes import gis_agent as gis_node

        working = {"map_layers": MAP_LAYERS, "target_layer_id": "layer-predios"}
        monkeypatch.setattr(gis_node, "run", AsyncMock(return_value={"error": "sin resultados"}))
        out = await capabilities_core._query_database(None, working, {"request": "trae nada"})
        working.update(out.delta)
        assert _resolve_target({}, working) == "layer-predios"


async def test_la_observacion_de_simbologia_cuenta_la_capa_que_coloreo():
    """V5 F4: «simbología aplicada a 30 elementos» (los del turno) cuando coloreó la
    capa objetivo, de 3: el LLM narró sobre una capa que no era."""
    from geo_copilot.orchestrator.react_tools import _run_node

    async def sym(_g, _s):
        return {"symbology": {"symbology_type": "single_symbol"}}

    working = {"geojson": _fc(30, "turno"), "map_layers": MAP_LAYERS}
    out = await _run_node(None, sym, working, {"intent": "apply_symbology", "target_layer_id": "layer-predios"})
    assert "a 3 elementos" in out.observation


async def test_una_transformacion_deja_en_foco_la_capa_nueva_no_la_de_entrada():
    """V3 F5 (E2.6): tras «buffer de 500 m a esos lotes», `activa` seguía siendo la capa de
    entrada: ws_measure midió los lotes (0,77 ha) y el LLM pidió al usuario que eligiera el buffer.
    La simbología, en cambio, sigue apuntando a la capa que coloreó (FRT-04)."""
    from geo_copilot.orchestrator.layer_resolution import resolver_capa
    from geo_copilot.orchestrator.react_tools import _run_node

    buffer = _fc(1, "buffer")

    async def op(_g, _s):
        return {"geojson": buffer}

    working = {"geojson": _fc(30, "turno"), "map_layers": MAP_LAYERS}
    out = await _run_node(None, op, working, {"intent": "spatial_operation", "target_layer_id": "layer-predios"},
                          deriva_capa=True)
    assert out.delta["target_layer_id"] is None
    foco = resolver_capa({**working, **out.delta}, con_features=True)
    assert foco is not None and foco.geojson is buffer

    async def sym(_g, _s):
        return {"geojson": buffer, "symbology": {"symbology_type": "single_symbol"}}

    out = await _run_node(None, sym, working, {"intent": "apply_symbology", "target_layer_id": "layer-predios"})
    assert out.delta["target_layer_id"] == "layer-predios"
