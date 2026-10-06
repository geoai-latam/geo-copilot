"""Tests del canal de salida analítico del PythonAgent (sandbox).

Cubre que el agente y su nodo propaguen `table`/`stats`/`chart`/`stdout`
además de la geometría — el desbloqueo del sandbox como motor analítico, no
solo transformador de geometría. El sandbox real es POSIX-only (no corre en
Windows), así que aquí se mockea `sandbox.execute` y `graph.python_agent`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.python_agent.agent import PythonAgent
from geo_copilot.orchestrator.nodes import python_agent as py_node


def _fc(props=None):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": props or {"v": 1}},
        ],
    }


def _resp(success, data=None, message=""):
    return SimpleNamespace(success=success, data=data, message=message)


# ─── extracción del agente: analysis_output → table/stats/chart/stdout ───────
class TestExecuteInSandboxExtraction:
    @pytest.mark.asyncio
    async def test_analytics_outputs_passed_through(self):
        agent = PythonAgent(llm_client=MagicMock())
        agent.sandbox.execute = AsyncMock(return_value={
            "success": True,
            "output": "calculado",
            "results": {"analysis_output": {
                "table": [{"barrio": "Centro", "conteo": 12}],
                "stats": {"correlacion": 0.82, "n": 100},
                "chart": {"chart_type": "bar", "x": "barrio", "y": "conteo",
                          "data": [{"barrio": "Centro", "conteo": 12}]},
            }},
        })
        res = await agent._execute_in_sandbox("table=[]", _fc())
        assert res["success"] is True
        assert res["table"] == [{"barrio": "Centro", "conteo": 12}]
        assert res["stats"] == {"correlacion": 0.82, "n": 100}
        assert res["chart"]["chart_type"] == "bar"
        assert res["stdout"] == "calculado"
        # Sin geometría → result_geojson None, no error.
        assert res["result_geojson"] is None

    @pytest.mark.asyncio
    async def test_geometry_only_still_works(self):
        agent = PythonAgent(llm_client=MagicMock())
        gj = _fc({"buffered": True})
        agent.sandbox.execute = AsyncMock(return_value={
            "success": True, "output": "",
            "results": {"analysis_output": {"result_geojson": gj}},
        })
        res = await agent._execute_in_sandbox("result=gdf", _fc())
        assert res["success"] is True
        assert res["result_geojson"] == gj
        assert res["table"] is None and res["stats"] is None and res["chart"] is None

    @pytest.mark.asyncio
    async def test_no_geometry_no_analytics_is_error(self):
        agent = PythonAgent(llm_client=MagicMock())
        agent.sandbox.execute = AsyncMock(return_value={
            "success": True, "output": "",
            "results": {"analysis_output": {}},
        })
        res = await agent._execute_in_sandbox("x=1", _fc())
        assert res["success"] is False
        assert "ni geometría" in res["error"] or "ni análisis" in res["error"]

    @pytest.mark.asyncio
    async def test_geometry_validation_error_propagates(self):
        agent = PythonAgent(llm_client=MagicMock())
        agent.sandbox.execute = AsyncMock(return_value={
            "success": True, "output": "",
            "results": {"analysis_output": {
                "result_geojson": {"error": "no CRS", "validation": "crs_missing"},
            }},
        })
        res = await agent._execute_in_sandbox("result=gdf", _fc())
        assert res["success"] is False
        assert res["validation"] == "crs_missing"


# ─── nodo: chart/table/stats → canal de visualización + data ─────────────────
class TestNodeAnalyticsPropagation:
    @pytest.mark.asyncio
    async def test_chart_maps_to_visualization(self, monkeypatch):
        monkeypatch.setattr("geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True)
        graph = MagicMock()
        chart = {"chart_type": "scatter", "x": "area", "y": "poblacion",
                 "data": [{"area": 10, "poblacion": 50}]}
        graph.python_agent.process = AsyncMock(return_value=_resp(
            True, {"chart": chart, "code": "c", "result_count": 0}, message="Análisis completado: gráfico."))
        state = {"query": "correlación área vs población", "session_id": "",
                 "active_data_source": "internal", "geojson": _fc(), "autonomous_mode": False}
        updates = await py_node.run(graph, state)
        assert updates["visualization"] == {
            "type": "chart", "chart_type": "scatter", "x_axis": "area", "y_axis": "poblacion"}
        assert updates["data"]["results"] == chart["data"]
        assert updates["messages"][0]["content"] == "Análisis completado: gráfico."

    @pytest.mark.asyncio
    async def test_table_maps_to_visualization(self, monkeypatch):
        monkeypatch.setattr("geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True)
        graph = MagicMock()
        table = [{"uso": "residencial", "conteo": 30}]
        graph.python_agent.process = AsyncMock(return_value=_resp(
            True, {"table": table, "code": "c", "result_count": 0}, message="Análisis completado: tabla (1 filas)."))
        state = {"query": "cuenta por uso", "session_id": "",
                 "active_data_source": "internal", "geojson": _fc(), "autonomous_mode": False}
        updates = await py_node.run(graph, state)
        assert updates["visualization"] == {"type": "table"}
        assert updates["data"]["results"] == table

    @pytest.mark.asyncio
    async def test_stats_maps_to_metric_table(self, monkeypatch):
        monkeypatch.setattr("geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True)
        graph = MagicMock()
        stats = {"media": 45.6, "n": 100}
        graph.python_agent.process = AsyncMock(return_value=_resp(
            True, {"stats": stats, "code": "c", "result_count": 0}, message="Análisis completado: estadísticas."))
        state = {"query": "estadísticas de área", "session_id": "",
                 "active_data_source": "internal", "geojson": _fc(), "autonomous_mode": False}
        updates = await py_node.run(graph, state)
        assert updates["visualization"] == {"type": "table"}
        assert {"métrica": "media", "valor": 45.6} in updates["data"]["results"]
        assert {"métrica": "n", "valor": 100} in updates["data"]["results"]


class TestNodeRejectIsTerminal:
    """LIVE-02: un rechazo del HITL de código es TERMINAL — no debe regenerar
    en bucle. Con autonomous_mode=True y max_retries>0, sin el fix el
    RetryExecutor invocaría al corrector y volvería a pedir aprobación."""

    @pytest.mark.asyncio
    async def test_reject_does_not_regenerate(self, monkeypatch):
        monkeypatch.setattr(
            "geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True)
        graph = MagicMock()
        # El agente devuelve un RECHAZO (como python_agent/agent.py ante REJECTED).
        graph.python_agent.process = AsyncMock(return_value=_resp(
            False,
            {"code": "gdf.buffer(1)", "rejected": True},
            message="Operación rechazada: no la quiero",
        ))
        # autonomous + reintentos: sin el fix esto regeneraría hasta 3 veces.
        state = {
            "query": "haz un buffer", "session_id": "",
            "active_data_source": "internal", "geojson": _fc(),
            "autonomous_mode": True, "max_retries": 2,
        }
        updates = await py_node.run(graph, state)

        # GUARDRAIL: el agente se llamó UNA sola vez (no se regeneró).
        assert graph.python_agent.process.await_count == 1
        assert updates.get("hitl_approved") is False
        assert "rechaz" in (updates.get("error") or "").lower()
