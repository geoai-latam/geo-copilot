"""Fixes LLM-as-pillar (auditoría): #2 densidad de viz → LLM, #3 fallback honesto.

VALIDACIÓN CRÍTICA: la densidad de heatmap/cluster la JUZGA el LLM (con el extent
que calcula el código), no un umbral fijo que ignora la dispersión espacial; sin
extent/LLM cae al umbral honesto. Y sin LLM, el diseño de viz no inventa un mapa:
lo declara degradado.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.insights_agent.agent import InsightsAgent
from geo_copilot.core.spatial import point_extent_km2


def _pts(coords):
    return [{"geometry": {"type": "Point", "coordinates": c}} for c in coords]


# ---------------------------------------------------------------------------
# Fix #2a — extent determinista (geo math = código, correcto)
# ---------------------------------------------------------------------------
def test_point_extent_km2_basic():
    # Bbox ~ 0.01° x 0.01° cerca del ecuador ≈ ~1.2 km² (1.11km x 1.11km).
    area = point_extent_km2(_pts([[-74.0, 4.0], [-73.99, 4.01]]))
    assert area is not None and 0.5 < area < 3


def test_point_extent_km2_degenerate_returns_none():
    assert point_extent_km2([]) is None
    assert point_extent_km2(_pts([[-74.0, 4.0]])) is None      # 1 punto
    assert point_extent_km2(_pts([[-74.0, 4.0], [-74.0, 4.0]])) is None  # coincidentes


def test_point_extent_km2_ignores_non_points():
    feats = [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}}]
    assert point_extent_km2(feats) is None


# ---------------------------------------------------------------------------
# Fix #2b — el LLM juzga la densidad; fallback honesto al umbral
# ---------------------------------------------------------------------------
def _agent(llm=None):
    a = InsightsAgent.__new__(InsightsAgent)  # sin __init__ pesado
    a.llm_client = llm
    return a


@pytest.mark.asyncio
async def test_density_judged_by_llm_overrides_threshold():
    # 20 puntos (< umbral 50) PERO muy densos → el LLM aprueba el heatmap.
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content=json.dumps({"appropriate": True, "reason": "muy denso en poca área"})))
    agent = _agent(llm)
    res = await agent.evaluate_visualization_fit(
        feature_count=20, geometry_type="Point", viz_type="heatmap",
        extent_km2=0.05,  # 20 pts / 0.05 km² = 400 pts/km² → denso
    )
    assert res["appropriate"] is True
    llm.chat.assert_awaited_once()
    prompt = llm.chat.call_args[0][0][0].content
    assert "densidad" in prompt and "400.0 puntos/km" in prompt  # código calculó la densidad


@pytest.mark.asyncio
async def test_density_judged_by_llm_can_reject_sparse():
    # 60 puntos (> umbral 50) PERO dispersísimos → el LLM rechaza.
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content=json.dumps(
        {"appropriate": False, "reason": "dispersos", "alternative": "point_map"})))
    agent = _agent(llm)
    res = await agent.evaluate_visualization_fit(
        feature_count=60, geometry_type="Point", viz_type="heatmap", extent_km2=5000.0)
    assert res["appropriate"] is False and res["alternative"] == "point_map"


@pytest.mark.asyncio
async def test_density_falls_back_to_threshold_without_extent():
    # Sin extent → no se puede juzgar densidad → umbral honesto (fc<50 rechaza).
    agent = _agent(MagicMock())  # hay llm, pero sin extent no se invoca
    res = await agent.evaluate_visualization_fit(
        feature_count=10, geometry_type="Point", viz_type="heatmap", extent_km2=None)
    assert res["appropriate"] is False  # 10 < 50 (umbral)


@pytest.mark.asyncio
async def test_density_falls_back_to_threshold_without_llm():
    agent = _agent(None)  # sin LLM
    res = await agent.evaluate_visualization_fit(
        feature_count=100, geometry_type="Point", viz_type="heatmap", extent_km2=0.1)
    assert res["appropriate"] is True  # 100 >= 50 (umbral honesto)


@pytest.mark.asyncio
async def test_type_check_still_code_not_llm():
    # 'heatmap requiere puntos' es type-check determinista — NO llama al LLM.
    llm = MagicMock()
    llm.chat = AsyncMock()
    agent = _agent(llm)
    res = await agent.evaluate_visualization_fit(
        feature_count=100, geometry_type="Polygon", viz_type="heatmap", extent_km2=0.1)
    assert res["appropriate"] is False and "requiere puntos" in res["reason"]
    llm.chat.assert_not_awaited()  # type-check no gasta LLM


@pytest.mark.asyncio
async def test_llm_density_judge_failure_falls_back_honestly():
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=RuntimeError("down"))
    agent = _agent(llm)
    res = await agent.evaluate_visualization_fit(
        feature_count=10, geometry_type="Point", viz_type="cluster", extent_km2=0.1)
    assert res["appropriate"] is False  # 10 < 30 (umbral, sin inventar)


# ---------------------------------------------------------------------------
# Fix #3 — sin LLM, el diseño de viz es honesto (no inventa un mapa)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_viz_design_without_llm_is_declared_degraded():
    agent = _agent(None)
    res = await agent._llm_design_visualizations(
        query="muestra X", analysis_type="general",
        geojson_data={"features": _pts([[-74, 4], [-73.9, 4.1]])},
        analysis_result={}, stats={})
    assert res.get("degraded") is True  # declara que no pudo diseñar
    assert "Sin LLM" in res["reasoning"]


@pytest.mark.asyncio
async def test_viz_design_no_data_is_not_degraded():
    agent = _agent(MagicMock())
    res = await agent._llm_design_visualizations(
        query="x", analysis_type="general",
        geojson_data={"features": []}, analysis_result={"data": []}, stats={})
    assert res.get("degraded") is not True  # "sin datos" ≠ "sin LLM"
    assert "sin datos" in res["reasoning"]
