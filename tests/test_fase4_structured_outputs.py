"""Fase 4 — T29 (A3): structured outputs en los 4 parsers frágiles.

El patrón «"Responde SOLO JSON" + parse_json_from_llm + default» se reemplazó
por function-calling FORZADO (`structured_call`): el JSON llega validado por
el proveedor y los fallbacks de parseo desaparecen por construcción.

Cubre:
- structured_call: args válidos al primer intento; re-pregunta UNA vez ante
  prosa; StructuredOutputError tras 2 intentos; tool_choice va FORZADO.
- Router: la decisión llega por tool_call `route`; prosa 2 veces ⇒ fallo
  honesto (no intent adivinado).
- Symbology: fallo de forma ⇒ [DEGRADED] declarado (política R2.3);
  `explicit_user_request` hace que el juez A2A NO degrade la elección literal.
- Insights: infer_visualization_type sin default "table" silencioso (un fallo
  de forma LANZA; el responder ya lo trata como best-effort).
- Traducción de tool_choice al dialecto Anthropic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.scripted_llm import ScriptedLLM, final_response, tool_call_response
from geo_copilot.core.structured_output import StructuredOutputError, structured_call

_PARAMS = {
    "type": "object",
    "properties": {"x": {"type": "integer"}},
    "required": ["x"],
}


# ---------------------------------------------------------------------------
# structured_call
# ---------------------------------------------------------------------------
class TestStructuredCall:
    @pytest.mark.asyncio
    async def test_args_validos_primer_intento(self):
        llm = ScriptedLLM([tool_call_response("f", {"x": 7})])
        result = await structured_call(
            llm, [LLMMessage(role="user", content="q")],
            name="f", description="d", parameters=_PARAMS,
        )
        assert result == {"x": 7}
        # tool_choice fue FORZADO a la función pedida.
        assert llm.calls[0]["tool_choice"] == {
            "type": "function", "function": {"name": "f"},
        }
        assert llm.calls[0]["tools"][0]["function"]["name"] == "f"

    @pytest.mark.asyncio
    async def test_prosa_re_pregunta_una_vez_y_recupera(self):
        llm = ScriptedLLM([
            final_response("bla bla sin tool_call"),
            tool_call_response("f", {"x": 1}),
        ])
        result = await structured_call(
            llm, [LLMMessage(role="user", content="q")],
            name="f", description="d", parameters=_PARAMS,
        )
        assert result == {"x": 1}
        assert llm.call_count == 2
        # La re-pregunta adjuntó el problema.
        second_msgs = llm.calls[1]["messages"]
        assert any("no invocó la función" in (m.content or "") for m in second_msgs)

    @pytest.mark.asyncio
    async def test_prosa_dos_veces_lanza(self):
        llm = ScriptedLLM([final_response("prosa"), final_response("más prosa")])
        with pytest.raises(StructuredOutputError):
            await structured_call(
                llm, [LLMMessage(role="user", content="q")],
                name="f", description="d", parameters=_PARAMS,
            )
        assert llm.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_call_de_otra_funcion_no_cuenta(self):
        llm = ScriptedLLM([
            tool_call_response("otra_funcion", {"x": 1}),
            final_response("nada"),
        ])
        with pytest.raises(StructuredOutputError):
            await structured_call(
                llm, [LLMMessage(role="user", content="q")],
                name="f", description="d", parameters=_PARAMS,
            )

    @pytest.mark.asyncio
    async def test_argumentos_json_invalidos_reintenta(self):
        from geo_copilot.core.llm_client import LLMResponse
        rota = LLMResponse(
            content="", model="m",
            tool_calls=[{"id": "1", "function": {"name": "f", "arguments": "{no json"}}],
        )
        llm = ScriptedLLM([rota, tool_call_response("f", {"x": 2})])
        result = await structured_call(
            llm, [LLMMessage(role="user", content="q")],
            name="f", description="d", parameters=_PARAMS,
        )
        assert result == {"x": 2}


# ---------------------------------------------------------------------------
# Router con structured outputs
# ---------------------------------------------------------------------------
class TestRouterStructured:
    @pytest.mark.asyncio
    async def test_decision_via_tool_call(self):
        from geo_copilot.agents.router_agent.agent import RouterAgent

        router = RouterAgent(llm_client=MagicMock())
        router.llm_client.chat = AsyncMock(return_value=tool_call_response("route", {
            "intent": "query_data", "reasoning": "consulta a BD", "entities": ["lotes"],
        }))
        resp = await router.process("trae lotes", context={})
        assert resp.success is True
        assert resp.data["intent"] == "query_data"
        # El chat fue llamado con la tool `route` forzada.
        kwargs = router.llm_client.chat.await_args.kwargs
        assert kwargs["tool_choice"]["function"]["name"] == "route"

    @pytest.mark.asyncio
    async def test_prosa_dos_veces_falla_honesto(self):
        from geo_copilot.agents.router_agent.agent import RouterAgent

        router = RouterAgent(llm_client=MagicMock())
        router.llm_client.chat = AsyncMock(side_effect=[
            final_response("no soy una tool call"),
            final_response("sigo sin serlo"),
        ])
        resp = await router.process("trae lotes", context={})
        assert resp.success is False
        assert resp.data["intent"] == "direct_response"  # fallo honesto, no adivinado

    @pytest.mark.asyncio
    async def test_intent_fuera_de_whitelist_falla(self):
        # La validación SEMÁNTICA sigue viva aunque la forma sea válida.
        from geo_copilot.agents.router_agent.agent import RouterAgent

        router = RouterAgent(llm_client=MagicMock())
        router.llm_client.chat = AsyncMock(return_value=tool_call_response("route", {
            "intent": "hacer_magia", "reasoning": "?",
        }))
        resp = await router.process("abracadabra", context={})
        assert resp.success is False


# ---------------------------------------------------------------------------
# Symbology: forma inválida ⇒ [DEGRADED]; instrucción literal ⇒ A2A no degrada
# ---------------------------------------------------------------------------
class TestSymbologyStructured:
    def _agent(self, chat):
        from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
        agent = SymbologyAgent(llm_client=MagicMock())
        agent.llm_client.chat = chat
        return agent

    @pytest.mark.asyncio
    async def test_fallo_de_forma_degrada_marcado(self):
        agent = self._agent(AsyncMock(side_effect=[
            final_response("prosa"), final_response("prosa 2"),
        ]))
        design = await agent._llm_design_symbology(
            query="colorea", field_analysis={}, primary_geom="Point", sample_props=[],
        )
        assert design["symbology_type"] == "single_symbol"
        assert "[DEGRADED]" in design["reasoning"]

    @pytest.mark.asyncio
    async def test_design_valido_via_tool_call(self):
        agent = self._agent(AsyncMock(return_value=tool_call_response(
            "design_symbology", {
                "symbology_type": "unique_values",
                "classification_field": "uso",
                "color_scheme": "Set2",
                "reasoning": "categórico con 3 valores",
            },
        )))
        design = await agent._llm_design_symbology(
            query="colorea por uso",
            field_analysis={"uso": {"data_type": "string"}},
            primary_geom="Point", sample_props=[],
        )
        assert design["symbology_type"] == "unique_values"
        assert design["classification_field"] == "uso"
        # Defaults mergeados para campos no emitidos.
        assert design["num_classes"] == 5
        assert design["explicit_user_request"] is False


# ---------------------------------------------------------------------------
# Insights: sin default "table" silencioso
# ---------------------------------------------------------------------------
class TestInsightsStructured:
    @pytest.mark.asyncio
    async def test_infer_viz_via_tool_call(self):
        from geo_copilot.agents.insights_agent import InsightsAgent

        agent = InsightsAgent(llm_client=MagicMock())
        agent.llm_client.chat = AsyncMock(return_value=tool_call_response(
            "design_visualization",
            {"type": "chart", "chart_type": "bar", "x_axis": "uso",
             "y_axis": "n", "reasoning": "comparación por categoría"},
        ))
        viz = await agent.infer_visualization_type(
            query="grafica por uso", data=[{"uso": "res", "n": 10}],
            geojson=None, sql=None,
        )
        assert viz["type"] == "chart"
        assert viz["chart_type"] == "bar"

    @pytest.mark.asyncio
    async def test_fallo_de_forma_lanza_no_adivina_table(self):
        from geo_copilot.agents.insights_agent import InsightsAgent

        agent = InsightsAgent(llm_client=MagicMock())
        agent.llm_client.chat = AsyncMock(side_effect=[
            final_response("prosa"), final_response("prosa"),
        ])
        with pytest.raises(StructuredOutputError):
            await agent.infer_visualization_type(
                query="q", data=[{"a": 1}], geojson=None, sql=None,
            )


# ---------------------------------------------------------------------------
# tool_choice → dialecto Anthropic
# ---------------------------------------------------------------------------
def test_anthropic_traduce_tool_choice_forzado():
    from geo_copilot.core.llm_client import AnthropicClient, LLMMessage

    c = AnthropicClient(api_key="x", model="claude-sonnet-4-5")
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    kw = c._kwargs([LLMMessage(role="user", content="hi")], tools, 0.1, 100,
                   {"type": "function", "function": {"name": "f"}})
    assert kw["tool_choice"]["type"] == "tool" and kw["tool_choice"]["name"] == "f", (
        "la traducción del forzado de función al dialecto Anthropic desapareció"
    )
