"""F1.2: reasoning_trace sanitizado en la respuesta."""

from geo_copilot.orchestrator.graph import _build_reasoning_trace


def test_trace_includes_router_decision_and_agents():
    state = {
        "intent": "apply_symbology",
        "messages": [
            {"agent": "router", "content": "Intent: apply_symbology", "success": True},
            {"agent": "symbology_agent", "content": "Apliqué unique_values por LocNombre", "success": True},
        ],
        "a2a_log": [{"target": "data_agent", "ok": True}, {"target": "insights_agent", "ok": True}],
    }
    trace = _build_reasoning_trace(state)
    agentes = [t["agent"] for t in trace]
    assert trace[0] == {"step": 0, "kind": "decision", "agent": "router", "detail": "apply_symbology", "success": True}
    assert "symbology_agent" in agentes
    assert any(t["kind"] == "a2a" and "2" in t["detail"] for t in trace)
    # el router aparece como decisión, no duplicado como mensaje
    assert agentes.count("router") == 1
    assert [t["step"] for t in trace] == list(range(len(trace)))


def test_la_traza_cumple_el_contrato():
    """F4: la traza del grafo cableado tiene la forma de `TraceEntry` (la del bucle ReAct también)."""
    from geo_copilot.platform.contracts.respuesta import TraceEntry

    state = {"intent": "query_data", "messages": [{"agent": "gis_agent", "content": "ok", "success": True}],
             "a2a_log": [{}]}
    for t in _build_reasoning_trace(state):
        TraceEntry.model_validate(t)


def test_trace_sanitizes_long_content():
    state = {
        "intent": "query_data",
        "messages": [
            {"agent": "gis_agent", "content": "x" * 500 + "\nSELECT secreto", "success": True},
        ],
    }
    trace = _build_reasoning_trace(state)
    gis = next(t for t in trace if t["agent"] == "gis_agent")
    # truncado y sin saltos de línea (resumen, no volcado)
    assert len(gis["detail"]) <= 160
    assert "\n" not in gis["detail"]


def test_trace_empty_state():
    assert _build_reasoning_trace({}) == []
