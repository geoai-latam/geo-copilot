"""F4.5: entry ReAct cableado en process() detrás del flag react_mode.

VALIDACIÓN CRÍTICA: con react_mode=True, process() ejecuta el bucle ReAct
(capability listing + tool-calling) en vez del routing por intent-enum, y devuelve
el MISMO shape de respuesta. Con react_mode=False (default) el camino normal queda
intacto (cubierto por el resto de la suite).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.core.scripted_llm import final_response, tool_call_response
from geo_copilot.orchestrator.graph import GeoAgentGraph


# ---------------------------------------------------------------------------
# _run_react_mode: mapea la salida del bucle al shape de respuesta normal
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_react_mode_maps_output_shape():
    from geo_copilot.core.scripted_llm import ScriptedLLM

    fake = MagicMock()
    fake.llm = ScriptedLLM([
        tool_call_response("query_database", {"request": "trae lotes"}),
        tool_call_response("answer", {"text": "Hay 3 lotes."}, call_id="c2"),
    ])
    fake.insights_agent.infer_visualization_type = AsyncMock(return_value={"type": "map"})
    gj = {"type": "FeatureCollection", "features": [{"id": i} for i in range(3)]}

    with patch("geo_copilot.orchestrator.nodes.agent_loop.get_settings",
               return_value=MagicMock(react_max_tool_calls=8, react_token_budget=0)), \
         patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"geojson": gj, "raw_data": [{}] * 3})):
        out = await GeoAgentGraph._run_react_mode(fake, {"query": "cuántos lotes hay"})

    assert out["success"] is True
    assert out["message"] == "Hay 3 lotes."
    assert len(out["geojson"]["features"]) == 3
    assert out["visualization"] == {"type": "map"}
    assert out["decision_trace"] and out["reasoning_trace"] == out["decision_trace"]
    # Shape compatible con el camino normal (claves que la API espera).
    for k in ("success", "message", "sql", "data", "geojson", "a2a_log"):
        assert k in out


@pytest.mark.asyncio
async def test_run_react_mode_marks_failure_on_breaker():
    from geo_copilot.core.scripted_llm import ScriptedLLM

    fake = MagicMock()
    # Nunca llama answer → el breaker corta → última decisión = error.
    fake.llm = ScriptedLLM([tool_call_response("query_database", {"request": "x"}, call_id=f"c{i}")
                            for i in range(5)])
    fake.insights_agent.infer_visualization_type = AsyncMock(return_value=None)
    with patch("geo_copilot.orchestrator.nodes.agent_loop.get_settings",
               return_value=MagicMock(react_max_tool_calls=2, react_token_budget=0)), \
         patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": []})):
        out = await GeoAgentGraph._run_react_mode(fake, {"query": "loop"})
    assert out["success"] is False
    assert "límite" in out["message"].lower()


# ---------------------------------------------------------------------------
# process() end-to-end con react_mode=True usa el bucle (grafo real, LLM scripted)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_process_uses_react_loop_when_flag_on(monkeypatch):
    from geo_copilot.core.config import get_settings as real_get_settings
    from geo_copilot.core.scripted_llm import ScriptedLLM

    scripted = ScriptedLLM([
        tool_call_response("query_database", {"request": "trae lotes"}),
        tool_call_response("answer", {"text": "Listo: 2 lotes."}, call_id="c2"),
    ])
    graph = GeoAgentGraph(llm_client=scripted, db_pool=None, semantic_layer=None)

    # Forzar react_mode=True en el settings que ve process().
    s = real_get_settings()
    monkeypatch.setattr(s, "react_mode", True)
    monkeypatch.setattr("geo_copilot.orchestrator.graph.get_settings", lambda: s)

    gj = {"type": "FeatureCollection", "features": [{"id": 1}, {"id": 2}]}
    with patch("geo_copilot.orchestrator.nodes.agent_loop.get_settings", return_value=s), \
         patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"geojson": gj, "raw_data": [{}, {}]})):
        result = await graph.process(query="cuántos lotes hay", session_id="s1")

    assert result["success"] is True
    assert result["message"] == "Listo: 2 lotes."
    assert len(result["geojson"]["features"]) == 2
    assert result["decision_trace"][-1]["kind"] == "final"


@pytest.mark.asyncio
async def test_process_forks_to_react_mode(monkeypatch):
    # Verifica el fork: react_mode=True → _run_react_mode; default no lo llama.
    from geo_copilot.core.config import get_settings as real_get_settings

    graph = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    sentinel = {"success": True, "message": "ruta react"}
    graph._run_react_mode = AsyncMock(return_value=sentinel)

    s = real_get_settings()
    monkeypatch.setattr(s, "react_mode", True)
    monkeypatch.setattr("geo_copilot.orchestrator.graph.get_settings", lambda: s)

    result = await graph.process(query="hola", session_id="s")
    assert result is sentinel
    graph._run_react_mode.assert_awaited_once()
