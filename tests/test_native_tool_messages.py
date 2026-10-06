"""Round-trip nativo de mensajes tool (en vez del scratchpad de texto).

VALIDACIÓN CRÍTICA: los mensajes planos se formatean IDÉNTICO a antes (backward-
compat para todos los agentes); el round-trip nativo (assistant.tool_calls +
role=tool con tool_call_id) se mapea bien a OpenAI y a Anthropic; y el bucle
ReAct arma el historial nativo (no texto) con ids que casan.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.core.llm_client import (
    LLMMessage,
    format_anthropic_messages,
    format_openai_messages,
)


# ---------------------------------------------------------------------------
# format_openai_messages
# ---------------------------------------------------------------------------
def test_openai_plain_messages_unchanged():
    msgs = [LLMMessage(role="system", content="s"),
            LLMMessage(role="user", content="u")]
    assert format_openai_messages(msgs) == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_openai_assistant_tool_call_and_tool_result():
    msgs = [
        LLMMessage(role="assistant", content="",
                   tool_calls=[{"id": "c1", "function": {"name": "q", "arguments": "{}"}}]),
        LLMMessage(role="tool", tool_call_id="c1", name="q", content="12 filas"),
    ]
    out = format_openai_messages(msgs)
    assert out[0]["tool_calls"][0]["id"] == "c1"
    assert out[0]["tool_calls"][0]["type"] == "function"  # se inyecta el type
    assert out[1] == {"role": "tool", "tool_call_id": "c1", "content": "12 filas"}


# ---------------------------------------------------------------------------
# format_anthropic_messages
# ---------------------------------------------------------------------------
def test_anthropic_separates_system_and_plain():
    system, msgs = format_anthropic_messages([
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content="hola"),
    ])
    assert system == "sys"
    assert msgs == [{"role": "user", "content": "hola"}]


def test_anthropic_tool_roundtrip_blocks():
    system, msgs = format_anthropic_messages([
        LLMMessage(role="assistant", content="",
                   tool_calls=[{"id": "c1", "function": {"name": "q", "arguments": '{"x":1}'}}]),
        LLMMessage(role="tool", tool_call_id="c1", content="ok"),
    ])
    # Anthropic exige empezar por user: el historial que empieza por assistant se abre con uno
    assert msgs[0]["role"] == "user"
    # assistant → bloque tool_use con input parseado
    assert msgs[1]["role"] == "assistant"
    tu = msgs[1]["content"][0]
    assert tu["type"] == "tool_use" and tu["id"] == "c1" and tu["input"] == {"x": 1}
    # tool → user con bloque tool_result
    tr = msgs[2]["content"][0]
    assert msgs[2]["role"] == "user"
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "c1" and tr["content"] == "ok"


def test_anthropic_bad_tool_arguments_default_empty():
    _, msgs = format_anthropic_messages([
        LLMMessage(role="assistant", tool_calls=[
            {"id": "c1", "function": {"name": "q", "arguments": "{not json"}}]),
    ])
    assert msgs[1]["content"][0]["input"] == {}  # no crashea


# ---------------------------------------------------------------------------
# El bucle ReAct arma historial NATIVO (no scratchpad de texto)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_agent_loop_builds_native_tool_history():
    from types import SimpleNamespace

    from geo_copilot.core.scripted_llm import (
        ScriptedLLM,
        final_response,
        tool_call_response,
    )
    from geo_copilot.orchestrator.nodes import agent_loop

    graph = MagicMock()
    graph.agent_metrics = None
    graph.llm = ScriptedLLM([
        tool_call_response("query_database", {"request": "x"}, call_id="call_abc"),
        final_response("listo"),
    ])
    settings = SimpleNamespace(react_max_tool_calls=8, react_token_budget=0,
                               react_max_reflections=0)
    with patch.object(agent_loop, "get_settings", return_value=settings), \
         patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": [{}]})):
        await agent_loop.run(graph, {"query": "trae datos"})

    # El 2do chat() (para la respuesta) ve el historial NATIVO del 1er tool:
    second_call_msgs = graph.llm.calls[1]["messages"]
    roles = [m.role for m in second_call_msgs]
    assert "tool" in roles  # hay un turno role=tool (no un 'user: Observación')
    asst_tool = [m for m in second_call_msgs if m.role == "assistant" and m.tool_calls]
    tool_msg = [m for m in second_call_msgs if m.role == "tool"]
    assert asst_tool and tool_msg
    # los ids casan entre el tool_call del assistant y el tool_call_id del tool
    assert asst_tool[0].tool_calls[0]["id"] == tool_msg[0].tool_call_id == "call_abc"
    # y NO se usó el scratchpad de texto
    assert not any("Observación:" in (m.content or "") for m in second_call_msgs)


def test_existing_plain_message_still_serializes():
    # Un agente cualquiera que manda LLMMessage(role, content) no cambia.
    m = LLMMessage(role="user", content="x")
    assert json.loads(m.model_dump_json())["content"] == "x"
    assert format_openai_messages([m]) == [{"role": "user", "content": "x"}]
