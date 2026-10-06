"""Auditoría pre-producción: cambiar de modelo = cambiar ``LLM_MODEL`` (y nada más).

Lo que antes rompía la primera llamada con otro modelo: ``temperature`` a un modelo que razona,
``max_tokens`` a la serie o/gpt-5, forzar la herramienta donde no se admite, el razonamiento de
Claude perdido en el bucle, ``usage`` ausente en un servidor compatible, una batería que se saltaba
en silencio. Aquí, con transporte simulado, qué se ENVÍA en cada caso.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.core.llm_client import (
    AnthropicClient,
    AzureOpenAIClient,
    LLMClient,
    LLMMessage,
    OpenAIClient,
)
from geo_copilot.core.model_profile import ModelProfile, adaptar, inferir

TOOLS = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}]
FORZAR = {"type": "function", "function": {"name": "f"}}
HOLA = [LLMMessage(role="user", content="hola")]


# --------------------------------------------------------------------------- perfiles
@pytest.mark.parametrize("prov,modelo,temp,param", [
    ("azure", "gpt-4.1-mini", True, "max_tokens"),
    ("openai", "gpt-4o", True, "max_tokens"),
    ("openai", "o4-mini", False, "max_completion_tokens"),
    ("azure", "gpt-5-mini", False, "max_completion_tokens"),
    ("anthropic", "claude-sonnet-4-5", True, "max_tokens"),
    ("anthropic", "claude-opus-5-5", False, "max_tokens"),
    ("anthropic", "claude-sonnet-5", False, "max_tokens"),
])
def test_inferencia_por_familia(prov, modelo, temp, param):
    p = inferir(prov, modelo)
    assert p.supports_temperature is temp and p.max_tokens_param == param


def test_el_operador_manda_sobre_la_inferencia():
    # Azure: LLM_MODEL es el deployment («prod-chat»), no dice que detrás hay un o3.
    p = inferir("azure", "prod-chat", '{"reasoning": true, "supports_temperature": false, "campo_raro": 1}')
    assert p.reasoning and not p.supports_temperature
    with pytest.raises(ValueError):
        inferir("azure", "x", "{no json")


@pytest.mark.parametrize("mensaje,rasgo", [
    ("Error code: 400 - Unsupported parameter: 'max_tokens' is not supported with this model. "
     "Use 'max_completion_tokens' instead.", {"max_tokens_param": "max_completion_tokens"}),
    ("Error code: 400 - Unsupported value: 'temperature' does not support 0.1 with this model.",
     {"supports_temperature": False}),
    ("Error code: 400 - temperature is deprecated for this model", {"supports_temperature": False}),
    ("Error code: 400 - 'parallel_tool_calls' is only allowed when 'tools' are specified",
     {"parallel_tool_control": False}),
])
def test_aprende_del_rechazo(mensaje, rasgo):
    nuevo = adaptar(ModelProfile(parallel_tool_control=True), Exception(mensaje))
    assert nuevo is not None
    for k, v in rasgo.items():
        assert getattr(nuevo, k) == v


def test_no_adapta_errores_que_no_son_de_parametros():
    assert adaptar(ModelProfile(), Exception("Error code: 429 - rate limit")) is None
    assert adaptar(ModelProfile(), Exception("Error code: 400 - context length exceeded")) is None


# --------------------------------------------------------------------------- OpenAI / Azure / compatible
def _respuesta_openai(*, tool_calls=None, usage=True, finish="stop"):
    msg = SimpleNamespace(content="ok", tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], model="m",
                           usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2) if usage else None)


def _sdk_openai(*respuestas):
    create = AsyncMock(side_effect=list(respuestas))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), create


@pytest.mark.asyncio
async def test_modelo_clasico_recibe_temperatura_y_tope_y_una_herramienta_por_paso():
    c = AzureOpenAIClient("k", "https://x", "gpt-4.1-mini")
    sdk, create = _sdk_openai(_respuesta_openai())
    c._client = sdk
    r = await c.chat(HOLA, tools=TOOLS, temperature=0.2, max_tokens=80)
    kw = create.call_args.kwargs
    assert kw["temperature"] == 0.2 and kw["max_tokens"] == 80
    # no se envía por defecto: MEDIDO, empeoraba el juicio (18/20 → 9/20 con gpt-4.1-mini)
    assert "parallel_tool_calls" not in kw
    assert r.stop_reason == "end" and r.usage["total_tokens"] == 5


@pytest.mark.asyncio
async def test_una_herramienta_por_paso_se_pide_solo_si_el_perfil_lo_dice():
    c = AzureOpenAIClient("k", "https://x", "gpt-4.1-mini", profile=ModelProfile(parallel_tool_control=True))
    sdk, create = _sdk_openai(_respuesta_openai())
    c._client = sdk
    await c.chat(HOLA, tools=TOOLS)
    assert create.call_args.kwargs["parallel_tool_calls"] is False


@pytest.mark.asyncio
async def test_modelo_que_razona_sin_temperatura_y_con_tope_suficiente():
    c = OpenAIClient("k", "o4-mini")
    sdk, create = _sdk_openai(_respuesta_openai())
    c._client = sdk
    await c.chat(HOLA, temperature=0.1, max_tokens=80)
    kw = create.call_args.kwargs
    assert "temperature" not in kw and "max_tokens" not in kw
    # 80 tokens no alcanzan ni para pensar: el tope sube al mínimo del perfil
    assert kw["max_completion_tokens"] >= 8192


@pytest.mark.asyncio
async def test_modelo_desconocido_que_rechaza_la_temperatura_se_adapta_y_lo_recuerda():
    c = AzureOpenAIClient("k", "https://x", "deployment-nuevo")
    rechazo = Exception("Error code: 400 - Unsupported value: 'temperature' does not support 0.1 with this model.")
    sdk, create = _sdk_openai(rechazo, _respuesta_openai(), _respuesta_openai())
    c._client = sdk
    assert (await c.chat(HOLA)).content == "ok"
    assert "temperature" in create.call_args_list[0].kwargs
    assert "temperature" not in create.call_args_list[1].kwargs
    await c.chat(HOLA)  # la siguiente ya no la manda (no repite el 400)
    assert create.await_count == 3 and "temperature" not in create.call_args_list[2].kwargs


@pytest.mark.asyncio
async def test_otro_error_no_se_reintenta():
    c = AzureOpenAIClient("k", "https://x", "gpt-4.1-mini")
    sdk, create = _sdk_openai(Exception("Error code: 400 - context_length_exceeded"))
    c._client = sdk
    with pytest.raises(Exception, match="context_length"):
        await c.chat(HOLA)
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_servidor_compatible_sin_usage_ni_clave():
    cliente = LLMClient(provider="local", model="qwen3:14b", base_url="http://localhost:11434/v1")
    assert cliente.provider == "openai_compatible"
    interno = cliente._get_client()
    assert isinstance(interno, OpenAIClient) and interno.base_url == "http://localhost:11434/v1"
    sdk, _ = _sdk_openai(_respuesta_openai(usage=False, finish="length"))
    interno._client = sdk
    r = await cliente.chat(HOLA)
    assert r.usage is None and r.stop_reason == "length"


def test_compatible_sin_url_falla_claro():
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        LLMClient(provider="openai_compatible", model="x")._get_client()


@pytest.mark.asyncio
async def test_sin_forzado_se_manda_auto():
    c = OpenAIClient("k", "gpt-4o", profile=ModelProfile(forced_tool_choice=False))
    sdk, create = _sdk_openai(_respuesta_openai())
    c._client = sdk
    await c.chat(HOLA, tools=TOOLS, tool_choice=FORZAR)
    assert create.call_args.kwargs["tool_choice"] == "auto"


# --------------------------------------------------------------------------- Anthropic
class _Bloque(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in vars(self).items() if v is not None}


def _sdk_anthropic(bloques, stop="tool_use"):
    resp = SimpleNamespace(content=bloques, stop_reason=stop, usage=SimpleNamespace(input_tokens=5, output_tokens=1))
    create = AsyncMock(return_value=resp)
    return SimpleNamespace(messages=SimpleNamespace(create=create)), create


@pytest.mark.asyncio
async def test_claude_que_razona_sin_temperatura_sin_forzado_y_con_su_pensamiento_de_vuelta():
    c = AnthropicClient("k", "claude-opus-5-5")
    bloques = [_Bloque(type="thinking", thinking="pienso…", signature="sig"),
               _Bloque(type="tool_use", id="t1", name="f", input={"a": 1})]
    sdk, create = _sdk_anthropic(bloques)
    c._client = sdk
    r = await c.chat(HOLA, tools=TOOLS, tool_choice=FORZAR, max_tokens=200)
    kw = create.call_args.kwargs
    assert "temperature" not in kw and kw["max_tokens"] >= 8192
    assert kw["tool_choice"] == {"type": "auto"}
    assert r.stop_reason == "tool_use" and r.provider_blocks[0]["type"] == "thinking"

    # el turno siguiente reenvía los bloques crudos (firma incluida), no una reconstrucción
    await c.chat([*HOLA, LLMMessage(role="assistant", tool_calls=r.tool_calls, provider_blocks=r.provider_blocks),
                  LLMMessage(role="tool", tool_call_id="t1", content="hecho")], tools=TOOLS)
    enviado = create.call_args.kwargs["messages"]
    assert enviado[1]["content"][0] == {"type": "thinking", "thinking": "pienso…", "signature": "sig"}


def test_varios_system_se_concatenan():
    from geo_copilot.core.llm_client import format_anthropic_messages
    system, _ = format_anthropic_messages([LLMMessage(role="system", content="A"), *HOLA,
                                           LLMMessage(role="system", content="B")])
    assert system == "A\n\nB"


# --------------------------------------------------------------------------- structured_call
@pytest.mark.asyncio
async def test_structured_call_pide_la_funcion_por_instruccion_si_no_se_puede_forzar():
    from geo_copilot.core.scripted_llm import tool_call_response
    from geo_copilot.core.structured_output import structured_call

    llm = MagicMock()
    llm.profile = ModelProfile(forced_tool_choice=False)
    llm.chat = AsyncMock(return_value=tool_call_response("f", {"x": 1}))
    assert await structured_call(llm, HOLA, name="f", description="d", parameters={"type": "object"}) == {"x": 1}
    enviados = llm.chat.call_args.args[0]
    assert "`f`" in enviados[-1].content


# --------------------------------------------------------------------------- bucle ReAct
def _settings():
    return SimpleNamespace(react_max_tool_calls=8, react_token_budget=0, react_max_reflections=0)


@pytest.mark.asyncio
async def test_argumentos_truncados_no_ejecutan_la_herramienta_sin_argumentos():
    from geo_copilot.core.llm_client import LLMResponse
    from geo_copilot.core.scripted_llm import ScriptedLLM, final_response
    from geo_copilot.orchestrator.nodes import agent_loop

    truncada = LLMResponse(content="", model="m", stop_reason="length", tool_calls=[
        {"id": "c1", "function": {"name": "query_database", "arguments": '{"request": "lotes de la manz'}}])
    graph = MagicMock()
    graph.agent_metrics = None
    graph.llm = ScriptedLLM([truncada, final_response("listo")])
    despacho = AsyncMock()
    with patch.object(agent_loop, "get_settings", return_value=_settings()), \
         patch.object(agent_loop, "dispatch_tool", despacho):
        await agent_loop.run(graph, {"query": "trae los lotes"})
    despacho.assert_not_awaited()
    obs = [m for m in graph.llm.calls[1]["messages"] if m.role == "tool"][0].content
    assert "truncada" in obs


@pytest.mark.asyncio
async def test_llamadas_paralelas_no_ejecutadas_se_le_dicen_al_modelo():
    from geo_copilot.core.llm_client import LLMResponse
    from geo_copilot.core.scripted_llm import ScriptedLLM, final_response
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.platform.capabilities import ToolOutcome

    dos = LLMResponse(content="", model="m", tool_calls=[
        {"id": "c1", "function": {"name": "query_database", "arguments": json.dumps({"request": "a"})}},
        {"id": "c2", "function": {"name": "ws_measure", "arguments": "{}"}}])
    graph = MagicMock()
    graph.agent_metrics = None
    graph.llm = ScriptedLLM([dos, final_response("listo")])
    with patch.object(agent_loop, "get_settings", return_value=_settings()), \
         patch.object(agent_loop, "dispatch_tool", AsyncMock(return_value=ToolOutcome("3 filas", success=True))):
        await agent_loop.run(graph, {"query": "q"})
    obs = [m for m in graph.llm.calls[1]["messages"] if m.role == "tool"][0].content
    assert "NO se ejecutaron: ws_measure" in obs


# --------------------------------------------------------------------------- parseo tolerante
def test_json_tras_un_pensamiento_con_llaves():
    from geo_copilot.core.utils import parse_json_from_llm
    assert parse_json_from_llm('<think>quizá {x} o {y}</think>\n{"addressed": true}') == {"addressed": True}
    assert parse_json_from_llm('Veredicto {breve}: {"addressed": false} fin') == {"addressed": False}
