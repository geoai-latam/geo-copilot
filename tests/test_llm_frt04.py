"""FRT-04 con LLM REAL (marker `llm`): el router elige la capa OBJETIVO por
NOMBRE, no la activa.

Ejecutar: pytest -m llm tests/test_llm_frt04.py
"""

from __future__ import annotations

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

# 2 capas: Predios (NO activa) y Ríos (activa = última añadida).
_MAP_CONTEXT = {
    "layers": [
        {"id": "layer-predios", "name": "Predios catastrales", "is_active": False,
         "geometry_type": "Polygon", "feature_count": 200,
         "fields": ["uso", "estrato", "area"], "visible": True},
        {"id": "layer-rios", "name": "Ríos", "is_active": True,
         "geometry_type": "LineString", "feature_count": 40,
         "fields": ["nombre", "caudal"], "visible": True},
    ],
}


async def _route(llm, query: str) -> dict:
    from geo_copilot.agents.router_agent.agent import RouterAgent
    ctx = {
        "schema_info": "Tablas: catastro.lotes (MULTIPOLYGON), catastro.rios (LINESTRING)",
        "map_context": _MAP_CONTEXT,
        "active_data_source": "external",
        "active_feature_count": 40,
        "active_geometry_type": "LineString",
        "active_field_names": ["nombre", "caudal"],
    }
    resp = await RouterAgent(llm_client=llm).process(query, ctx)
    assert resp.success, resp.message
    return resp.data


@pytest.mark.asyncio
async def test_router_estiliza_la_capa_NOMBRADA_no_la_activa():
    """"colorea los predios por estrato" → el router elige PREDIOS (la nombrada),
    NO Ríos (la activa/última). Ésta es la esencia de FRT-04."""
    llm = await get_real_llm_or_skip()
    data = await _route(llm, "colorea los predios por estrato")
    assert data["intent"] == "apply_symbology", data
    assert data.get("target_layer_id") == "layer-predios", data


@pytest.mark.asyncio
async def test_router_operacion_espacial_sobre_capa_nombrada():
    """"calcula el área de los predios" con Ríos activa → target = Predios."""
    llm = await get_real_llm_or_skip()
    data = await _route(llm, "calcula el área de cada predio")
    # spatial_operation o analyze/query — lo que importa es el target elegido.
    assert data.get("target_layer_id") == "layer-predios", data


@pytest.mark.asyncio
async def test_router_sin_nombrar_capa_no_desvia_el_target():
    """"coloréala de rojo" (sin nombrar capa) → NO debe elegir predios (la no
    activa); target null (usa la activa) o, a lo sumo, la activa Ríos."""
    llm = await get_real_llm_or_skip()
    data = await _route(llm, "coloréala de rojo")
    target = data.get("target_layer_id")
    assert target != "layer-predios", data  # no inventó una capa no nombrada
    assert target in (None, "", "layer-rios"), data
