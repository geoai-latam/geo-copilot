"""F6: aprendizaje de largo plazo (métricas → cost-aware).

VALIDACIÓN CRÍTICA: las métricas reflejan hechos (tokens/éxito/fiabilidad por
herramienta) y el hint de costo solo aparece con suficiente historia y solo
señala herramientas REALMENTE poco fiables — no mete ruido prematuro ni inventa.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.core.agent_metrics import AgentMetrics


# ---------------------------------------------------------------------------
# Registro + agregación
# ---------------------------------------------------------------------------
def test_tool_stats_and_success_rate():
    m = AgentMetrics()
    m.record_tool("query_database", True)
    m.record_tool("query_database", False)
    m.record_tool("apply_symbology", True)
    stats = m.tool_stats()
    assert stats["query_database"] == {"calls": 2, "successes": 1, "success_rate": 0.5}
    assert stats["apply_symbology"]["success_rate"] == 1.0


def test_session_summary_tokens_and_success():
    m = AgentMetrics()
    m.record_turn(success=True, tokens=1000, n_tools=2)
    m.record_turn(success=False, tokens=2000, n_tools=1)
    s = m.session_summary()
    assert s["turns"] == 2 and s["total_tokens"] == 3000
    assert s["avg_tokens"] == 1500 and s["success_rate"] == 0.5


def test_turn_cap_is_bounded():
    m = AgentMetrics(max_turns=3)
    for i in range(5):
        m.record_turn(success=True, tokens=100)
    assert m.session_summary()["turns"] == 3  # solo los últimos 3


# ---------------------------------------------------------------------------
# cost_hint: cost-aware, sin ruido prematuro
# ---------------------------------------------------------------------------
def test_cost_hint_empty_without_history():
    m = AgentMetrics()
    m.record_turn(success=True, tokens=100)  # 1 turno < min_turns(2)
    assert m.cost_hint() == ""


def test_cost_hint_reports_tokens_and_unreliable_tools():
    m = AgentMetrics()
    m.record_turn(success=True, tokens=1000)
    m.record_turn(success=False, tokens=1000)
    # query_database falla seguido → poco fiable; apply_symbology fiable.
    for ok in (False, False, True):
        m.record_tool("query_database", ok)
    m.record_tool("apply_symbology", True)
    m.record_tool("apply_symbology", True)
    hint = m.cost_hint()
    assert "tokens/turno" in hint
    assert "query_database" in hint and "poco fiables" in hint
    assert "apply_symbology" not in hint  # fiable → no se señala


def test_cost_hint_ignores_low_sample_tools():
    m = AgentMetrics()
    m.record_turn(success=True, tokens=10)
    m.record_turn(success=True, tokens=10)
    m.record_tool("query_database", False)  # 1 sola llamada → no concluyente
    assert "query_database" not in m.cost_hint()


# ---------------------------------------------------------------------------
# Wiring en el bucle ReAct
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_agent_loop_records_metrics_and_injects_hint():
    from types import SimpleNamespace

    from geo_copilot.core.scripted_llm import ScriptedLLM, final_response, tool_call_response
    from geo_copilot.orchestrator.nodes import agent_loop

    metrics = AgentMetrics()
    # Sembrar historia para que cost_hint no esté vacío.
    metrics.record_turn(success=True, tokens=500)
    metrics.record_turn(success=False, tokens=500)
    for ok in (False, False, True):
        metrics.record_tool("query_database", ok)

    graph = MagicMock()
    graph.agent_metrics = metrics
    graph.llm = ScriptedLLM([
        tool_call_response("query_database", {"request": "x"}),
        tool_call_response("answer", {"text": "listo"}, call_id="c2"),
    ])
    settings = SimpleNamespace(react_max_tool_calls=8, react_token_budget=0,
                               react_max_reflections=0)

    with patch.object(agent_loop, "get_settings", return_value=settings), \
         patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": [{}]})):
        await agent_loop.run(graph, {"query": "trae datos"})

    # El hint de costo se inyectó en el system prompt del primer chat.
    first_system = graph.llm.calls[0]["messages"][0].content
    assert "HISTÓRICO DE SESIÓN" in first_system
    assert "query_database" in first_system  # poco fiable → señalado
    # Y el turno + la herramienta de ESTE turno quedaron registrados.
    assert metrics.session_summary()["turns"] == 3
    assert metrics.tool_stats()["query_database"]["calls"] == 4  # 3 sembradas + 1
