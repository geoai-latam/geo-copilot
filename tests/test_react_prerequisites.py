"""F4.T1/T2/T3: prerrequisitos del bucle ReAct — harness de test, contrato de
eventos del frontend, y auditoría de decisiones.

VALIDACIÓN CRÍTICA: las tres piezas componen el andamiaje para testear y observar
un bucle ReAct de forma DETERMINISTA y SANITIZADA antes de construirlo (F4.4):
un LLM guionizado dirige el bucle, cada decisión se audita sin filtrar secretos,
y se traduce a un evento tipado para el frontend.
"""

import json

import pytest
from pydantic import ValidationError

from geo_copilot.api.react_events import (
    ReActEvent,
    ReActEventType,
    event_from_decision,
)
from geo_copilot.core.agent_audit import DecisionTrace, _sanitize_args
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.scripted_llm import (
    ScriptedLLM,
    decision_kinds,
    ends_terminal,
    final_response,
    tool_call_response,
    tools_called,
)


# ===========================================================================
# F4.T3 — auditoría de decisiones
# ===========================================================================
def test_decision_trace_records_sequence():
    t = DecisionTrace()
    t.record_tool_call(0, "query_database", {"sql": "SELECT 1"})
    t.record_result(0, "query_database", success=True, result=[{"a": 1}])
    t.record_final(1, "listo")
    lst = t.to_list()
    assert decision_kinds(lst) == ["tool_call", "tool_result", "final"]
    assert lst[1]["success"] is True


def test_audit_redacts_secrets_and_truncates():
    s = _sanitize_args({"api_key": "supersecret", "q": "x" * 500})
    assert "supersecret" not in s and "***" in s
    assert len(s) <= 161  # truncado a MAX_LEN (+ elipsis)


def test_audit_flattens_newlines():
    t = DecisionTrace()
    t.record_error(0, "line1\nline2\nline3")
    assert "\n" not in t.to_list()[0]["detail"]


def test_audit_len_and_empty():
    t = DecisionTrace()
    assert len(t) == 0 and t.to_list() == []


# ===========================================================================
# F4.T2 — contrato de eventos del frontend
# ===========================================================================
def test_event_model_requires_nonnegative_step():
    ev = ReActEvent(type=ReActEventType.TOOL_CALL, step=0, tool="t", summary="x")
    assert ev.step == 0
    with pytest.raises(ValidationError):
        ReActEvent(type=ReActEventType.TOOL_CALL, step=-1)


def test_event_from_decision_maps_kinds():
    tc = event_from_decision({"step": 0, "kind": "tool_call", "tool": "q", "detail": "sql=.."})
    tr = event_from_decision({"step": 0, "kind": "tool_result", "tool": "q",
                              "detail": "12 filas", "success": True})
    fin = event_from_decision({"step": 1, "kind": "final", "detail": "ok"})
    err = event_from_decision({"step": 2, "kind": "error", "detail": "boom"})
    assert tc.type == ReActEventType.TOOL_CALL and tc.tool == "q"
    assert tr.type == ReActEventType.TOOL_RESULT and tr.success is True
    assert fin.type == ReActEventType.FINAL_ANSWER
    assert err.type == ReActEventType.ERROR


def test_event_serializes_json():
    ev = ReActEvent(type=ReActEventType.FINAL_ANSWER, step=1, summary="hay 12 lotes")
    data = ev.model_dump(mode="json")
    assert data["type"] == "final_answer" and data["step"] == 1


# ===========================================================================
# F4.T1 — harness de LLM guionizado
# ===========================================================================
@pytest.mark.asyncio
async def test_scripted_llm_replays_sequence():
    llm = ScriptedLLM([
        tool_call_response("query_database", {"sql": "SELECT 1"}),
        final_response("hay 1 fila"),
    ])
    r1 = await llm.chat([LLMMessage(role="user", content="cuántos hay")], tools=[{"x": 1}])
    r2 = await llm.chat([LLMMessage(role="user", content="...")])
    assert r1.tool_calls[0]["function"]["name"] == "query_database"
    assert json.loads(r1.tool_calls[0]["function"]["arguments"]) == {"sql": "SELECT 1"}
    assert r2.content == "hay 1 fila" and r2.tool_calls is None
    assert llm.call_count == 2 and llm.exhausted
    assert llm.calls[0]["tools"] == [{"x": 1}]  # registra los tools pasados


@pytest.mark.asyncio
async def test_scripted_llm_exhaustion_is_final_not_hang():
    llm = ScriptedLLM([])  # guion vacío
    r = await llm.chat([LLMMessage(role="user", content="x")])
    assert r.content == "" and r.tool_calls is None  # final por defecto, no cuelga


# ===========================================================================
# Integración: las tres piezas componen un mini-bucle ReAct determinista
# ===========================================================================
@pytest.mark.asyncio
async def test_prereqs_compose_into_a_deterministic_loop():
    """Simula el bucle que construirá F4.4 usando SOLO los prerrequisitos."""
    llm = ScriptedLLM([
        tool_call_response("query_database", {"sql": "SELECT count(*) FROM lotes"}),
        final_response("Hay 12 lotes."),
    ])
    # Herramienta de mentira: devuelve 12.
    async def _run_tool(name, args):
        return {"rows": 12} if name == "query_database" else {"error": "desconocida"}

    trace = DecisionTrace()
    events: list[ReActEvent] = []
    step = 0
    MAX = 5  # circuit-breaker (placeholder de F4.3)
    while step < MAX:
        resp = await llm.chat([LLMMessage(role="user", content="cuántos lotes hay")],
                              tools=[{"type": "function"}])
        if resp.tool_calls:
            call = resp.tool_calls[0]["function"]
            args = json.loads(call["arguments"])
            trace.record_tool_call(step, call["name"], args)
            result = await _run_tool(call["name"], args)
            trace.record_result(step, call["name"], success="error" not in result, result=result)
            step += 1
            continue
        trace.record_final(step, resp.content)
        break

    lst = trace.to_list()
    # Invariantes del contrato: 1 tool_call + 1 tool_result + final terminal.
    assert tools_called(lst) == ["query_database"]
    assert decision_kinds(lst) == ["tool_call", "tool_result", "final"]
    assert ends_terminal(lst)
    # Cada decisión se traduce a un evento tipado para el frontend.
    events = [event_from_decision(d, session_id="s1") for d in lst]
    assert [e.type for e in events] == [
        ReActEventType.TOOL_CALL, ReActEventType.TOOL_RESULT, ReActEventType.FINAL_ANSWER,
    ]
    assert events[-1].summary == "Hay 12 lotes."
