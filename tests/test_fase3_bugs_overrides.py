"""Fase 3 del plan de remediación agéntica (T18–T24, parte unitaria).

Cubre:
- R4.1: search_internal_catalog se llama con kwargs REALES (el kwarg fantasma
  search_query producía TypeError en runtime).
- R4.2: la ruta active_source='previous' está viva en el PythonAgent.
- R4.4: get_color_palette(_, 1) devuelve un color visible (medio), no el más claro.
- R4.5: categorías fuera del top → clase "Otros" con conteo.
- R4.6: el responder no muere si infer_visualization_type lanza.
- R4.7: get_capabilities en sync con valid_intents (sin intents fantasma).
- R4.8: el ContextVar de sesión se resetea al salir de process().
- R4.10: la respuesta en PROSA del bucle ReAct pasa por el juez composicional.
- R5.1/R5.2: los overrides por keyword fueron eliminados (regresión estática);
  el juicio vive en el prompt (tests LLM reales en test_llm_overrides.py).
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# R4.1 — la llamada a search_internal_catalog usa la firma real
# ---------------------------------------------------------------------------
class TestSearchInternalFirma:
    def test_firma_no_acepta_search_query(self):
        from geo_copilot.agents.data_agent.agent import DataAgent
        params = inspect.signature(DataAgent.search_internal_catalog).parameters
        assert "search_query" not in params
        assert "keywords" in params

    def test_call_site_ya_no_usa_kwarg_fantasma(self):
        # Regresión: el call site debe pasar keywords=, no search_query=.
        import geo_copilot.agents.data_agent.agent as m
        src = inspect.getsource(m)
        assert not re.search(
            r"search_internal_catalog\(\s*search_query\s*=", src
        ), "el call site volvió a usar el kwarg inexistente search_query"


# ---------------------------------------------------------------------------
# R4.2 — la ruta 'previous' está viva
# ---------------------------------------------------------------------------
class TestRutaPreviousViva:
    @pytest.mark.asyncio
    async def test_previous_pasa_la_seleccion_de_fuente(self):
        from geo_copilot.agents.python_agent.agent import PythonAgent

        agent = PythonAgent(llm_client=MagicMock())
        # Capa heredada SIN features: si la fuente se acepta, el fallo debe ser
        # "no contiene features" (aguas abajo), NO "no hay fuente activa".
        resp = await agent.process(
            query="haz un buffer",
            context={
                "active_data_source": "previous",
                "last_geojson": {"type": "FeatureCollection", "features": []},
            },
        )
        assert resp.success is False
        assert "fuente de datos activa" not in resp.message
        assert "features" in resp.message.lower()

    @pytest.mark.asyncio
    async def test_none_sigue_rechazando_honesto(self):
        from geo_copilot.agents.python_agent.agent import PythonAgent

        agent = PythonAgent(llm_client=MagicMock())
        resp = await agent.process(query="buffer", context={"active_data_source": "none"})
        assert resp.success is False
        assert "fuente de datos activa" in resp.message


# ---------------------------------------------------------------------------
# R4.4 / R4.5 — paleta visible + clase "Otros"
# ---------------------------------------------------------------------------
class TestPaletaYOtros:
    def test_un_color_es_el_medio_no_el_mas_claro(self):
        from geo_copilot.agents.symbology_agent.styles import (
            COLOR_PALETTES,
            ColorScheme,
            get_color_palette,
        )
        blues = COLOR_PALETTES[ColorScheme.BLUES]
        [color] = get_color_palette(ColorScheme.BLUES, 1)
        assert color != blues[0]  # el casi-blanco invisible
        assert color == blues[len(blues) // 2]

    def test_paletas_multicolor_sin_cambio(self):
        from geo_copilot.agents.symbology_agent.styles import (
            ColorScheme,
            get_color_palette,
        )
        assert len(get_color_palette(ColorScheme.BLUES, 5)) == 5

    def test_breaks_agregan_otros_con_conteo(self):
        from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
        from geo_copilot.agents.symbology_agent.styles import ColorScheme

        agent = SymbologyAgent(llm_client=MagicMock())
        breaks = agent._calculate_categorical_breaks(
            {"a": 10, "b": 5}, ColorScheme.SET2, others_count=7
        )
        labels = [b.label for b in breaks]
        assert "Otros" in labels
        otros = next(b for b in breaks if b.label == "Otros")
        assert otros.count == 7

    def test_sin_resto_no_hay_otros(self):
        from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
        from geo_copilot.agents.symbology_agent.styles import ColorScheme

        agent = SymbologyAgent(llm_client=MagicMock())
        breaks = agent._calculate_categorical_breaks(
            {"a": 10}, ColorScheme.SET2, others_count=0
        )
        assert all(b.label != "Otros" for b in breaks)


# ---------------------------------------------------------------------------
# R4.6 — responder blindado en el nodo terminal
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_responder_sobrevive_fallo_de_insights():
    from geo_copilot.orchestrator.nodes import responder

    graph = MagicMock()
    graph.insights_agent.infer_visualization_type = AsyncMock(
        side_effect=RuntimeError("hiccup del LLM")
    )
    state = {
        "query": "trae lotes",
        "final_response": "Aquí están tus 10 lotes.",
        "raw_data": [{"a": 1}],
        "geojson": {"type": "FeatureCollection", "features": [{"x": 1}]},
    }
    updates = await responder.run(graph, state)
    # La respuesta sobrevive; la visualización degrada a None.
    assert updates["final_response"] == "Aquí están tus 10 lotes."
    assert updates["final_data"]["visualization"] is None


# ---------------------------------------------------------------------------
# R4.7 — capacidades en sync con el whitelist real
# ---------------------------------------------------------------------------
def test_router_capabilities_sin_intents_fantasma():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=MagicMock())
    announced = set(agent.get_capabilities()["intents"])
    # El whitelist real vive en process(); lo extraemos del source para no
    # duplicarlo a mano (si divergen, este test lo detecta).
    src = inspect.getsource(RouterAgent)
    m = re.search(r"valid_intents\s*=\s*\{([^}]+)\}", src)
    assert m, "no encontré valid_intents en el source"
    valid = set(re.findall(r'["\']([a-z_]+)["\']', m.group(1)))
    assert announced <= valid, f"intents anunciados fuera del whitelist: {announced - valid}"


# ---------------------------------------------------------------------------
# R4.8 — ContextVar reseteado al salir
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_contextvar_sesion_se_resetea():
    import geo_copilot.agents.data_agent.agent as data_mod
    from geo_copilot.agents.data_agent.agent import DataAgent

    agent = DataAgent.__new__(DataAgent)  # sin ctor pesado
    seen: dict = {}

    async def fake_impl(self, query, context=None):
        seen["during"] = data_mod._session_id_ctx.get()
        return MagicMock(success=True)

    with patch.object(DataAgent, "_process_impl", new=fake_impl):
        await DataAgent.process(agent, "q", context={}, session_id="s-123")

    assert seen["during"] == "s-123"          # visible durante el proceso
    assert data_mod._session_id_ctx.get() is None  # reseteado al salir


# ---------------------------------------------------------------------------
# R4.10 — la prosa del bucle ReAct pasa por el juez composicional
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prosa_evasiva_dispara_reflexion():
    from geo_copilot.core.scripted_llm import ScriptedLLM, final_response, tool_call_response
    from geo_copilot.orchestrator.nodes import agent_loop

    graph = MagicMock()
    graph.llm = ScriptedLLM([
        final_response("Mmm, no estoy seguro."),               # prosa evasiva
        tool_call_response("answer", {"text": "Hay 42 lotes."}),  # tras el empujón
    ])
    settings = SimpleNamespace(
        react_max_tool_calls=8, react_token_budget=0, react_max_reflections=1,
    )
    verdicts = [
        SimpleNamespace(needs_more_work=True, missing="el conteo pedido", reason=""),
    ]

    with patch.object(agent_loop, "get_settings", return_value=settings), \
         patch("geo_copilot.core.composition_judge.judge_answer",
               new=AsyncMock(side_effect=verdicts)):
        result = await agent_loop.run(graph, {"query": "cuántos lotes hay", "session_id": "s"})

    # La prosa evasiva NO cerró el bucle: hubo reflexión y la respuesta final
    # es la completa.
    assert result["final_response"] == "Hay 42 lotes."
    kinds = [d["kind"] for d in result["decision_trace"]]
    assert "reflection" in kinds


# ---------------------------------------------------------------------------
# R5.1 / R5.2 — regresión estática: los overrides no vuelven
# ---------------------------------------------------------------------------
def test_router_sin_override_de_feature():
    import geo_copilot.orchestrator.nodes.router as m
    src = inspect.getsource(m)
    assert 'intent = "query_data"' not in src, (
        "volvió el override en código de feature seleccionada — la regla vive "
        "en el prompt (R5.1)"
    )


def test_symbology_sin_override_por_substring():
    # F4: el diseño se repartió en mixins (diseno.py, paletas.py…): la guardia lee TODO el paquete
    from pathlib import Path

    import geo_copilot.agents.symbology_agent as paquete
    src = chr(10).join(f.read_text(encoding="utf-8") for f in Path(paquete.__file__).parent.glob("*.py"))
    assert '"mapa de calor" in _ql' not in src
    assert '"agrupa" in _ql' not in src, (
        "volvió el override por substring — ciego a negaciones (R5.2)"
    )
