"""Regresión C3d-2: layer_name determinista (no hash() randomizado)."""

import hashlib
from unittest.mock import MagicMock

import pytest

from geo_copilot.agents.symbology_agent import SymbologyAgent


@pytest.mark.asyncio
async def test_layer_name_is_deterministic_md5():
    agent = SymbologyAgent(llm_client=MagicMock())
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {"v": 1},
            }
        ],
    }
    analysis = {
        "geometry_type": "Point",
        "design": {"symbology_type": "single_symbol"},
        "fields": {},
    }
    query = "mi consulta de prueba"

    cfg = await agent.generate_symbology(geojson, analysis, query=query)

    expected = f"layer_{hashlib.md5(query.encode('utf-8')).hexdigest()[:8]}"
    # Solo coincide si se usa md5 (determinista), no hash() randomizado.
    assert cfg.layer_name == expected
