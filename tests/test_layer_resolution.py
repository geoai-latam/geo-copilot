"""S1.4 — una sola regla para decidir sobre qué capa se actúa.

Había cinco implementaciones y no coincidían: con una capa activa en el mapa y
una consulta nueva en el mismo turno, simbología elegía los datos nuevos y
python_agent la capa vieja. Estos tests fijan la regla y exigen que los tres
nodos elijan LA MISMA capa.
"""

from __future__ import annotations

import pytest

from geo_copilot.orchestrator.layer_resolution import resolver_capa
from geo_copilot.orchestrator.nodes._layer_context import resolve_active_layer
from geo_copilot.orchestrator.nodes.python_agent import _select_working_data


def _fc(n: int, tag: str) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, i]},
         "properties": {"capa": tag}} for i in range(n)
    ]}


NUEVOS = _fc(3, "lotes-del-turno")
MAPA = _fc(5, "capa-activa-del-mapa")
PREVIA = _fc(2, "capa-previa")
RIOS = _fc(4, "rios")

ESTADOS = {
    # El caso que discrepaba: capa activa del mapa (external, declarada activa)
    # + consulta nueva en el mismo turno.
    "mapa_activo_y_consulta_nueva": {
        "active_data_source": "external", "external_geojson": MAPA, "geojson": NUEVOS,
    },
    "solo_capa_del_mapa": {"active_data_source": "external", "external_geojson": MAPA},
    "solo_datos_del_turno": {"active_data_source": "internal", "geojson": NUEVOS},
    "solo_previa": {"previous_geojson": PREVIA},
    "objetivo_nombrado": {
        "geojson": NUEVOS, "external_geojson": MAPA,
        "target_layer_id": "rios", "map_layers": {"rios": {"data": RIOS, "name": "Ríos"}},
    },
    # V5 FH.5: sin estado previo ni objetivo, la capa ACTIVA del mapa (antes: «no hay datos»)
    "solo_la_activa_del_mapa": {
        "map_context": {"layers": [{"id": "rios", "is_active": False}, {"id": "lotes", "is_active": True}]},
        "map_layers": {"rios": {"data": RIOS}, "lotes": {"data": MAPA, "name": "Lotes"}},
    },
    # …pero los datos del turno siguen mandando sobre ella
    "turno_sobre_la_activa_del_mapa": {
        "geojson": NUEVOS, "map_context": {"layers": [{"id": "lotes", "is_active": True}]},
        "map_layers": {"lotes": {"data": MAPA}},
    },
}

ESPERADA = {
    "mapa_activo_y_consulta_nueva": NUEVOS,  # lo recién traído manda
    "solo_capa_del_mapa": MAPA,
    "solo_datos_del_turno": NUEVOS,
    "solo_previa": PREVIA,
    "objetivo_nombrado": RIOS,
    "solo_la_activa_del_mapa": MAPA,
    "turno_sobre_la_activa_del_mapa": NUEVOS,
}


@pytest.mark.parametrize("caso", list(ESTADOS))
def test_los_nodos_eligen_la_misma_capa(caso):
    state = ESTADOS[caso]
    simbologia, _, _ = resolve_active_layer(state)
    python, _, _ = _select_working_data(state)
    assert simbologia is ESPERADA[caso]
    assert python is ESPERADA[caso]


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", [c for c in ESTADOS if c != "objetivo_nombrado"])
async def test_la_capa_activa_de_un_servicio_mcp_es_la_misma(caso):
    """F3: `activa` en un argumento geo de una tool MCP = la capa que usan los nodos."""
    from types import SimpleNamespace

    from geo_copilot.platform.mcp.hub import _resolver_geo

    est = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["geometry"]}}})
    args, problema = await _resolver_geo(est, ESTADOS[caso], {"aoi": "activa"})
    assert problema is None and args["aoi"] is ESPERADA[caso]


def test_objetivo_inexistente_se_ignora():
    state = {"geojson": NUEVOS, "target_layer_id": "fantasma", "map_layers": {}}
    assert resolver_capa(state).geojson is NUEVOS


def test_previa_vacia_no_cuenta():
    vacia = {"type": "FeatureCollection", "features": []}
    assert resolver_capa({"previous_geojson": vacia}) is None


def test_aoi_exige_features_y_cae_al_viewport():
    vacia = {"type": "FeatureCollection", "features": []}
    state = {"geojson": vacia, "map_context": {"viewport": {"bbox": [-74.2, 4.5, -74.0, 4.7]}}}
    capa = resolver_capa(state, con_features=True, viewport=True)
    assert capa.origen == "viewport"
    assert capa.geojson["type"] == "Polygon"
    # Sin exigir features, el FeatureCollection vacío del turno sí cuenta
    # (simbología avisa honesto de que no hay elementos).
    assert resolver_capa(state).origen == "internal"


@pytest.mark.asyncio
async def test_sin_nada_no_hay_capa():
    from types import SimpleNamespace

    from geo_copilot.platform.mcp.hub import _resolver_geo

    assert resolver_capa({}) is None
    assert resolve_active_layer({}) == (None, "sin capa activa", "none")
    est = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["geometry"]}}})
    _, problema = await _resolver_geo(est, {}, {"aoi": "activa"})
    assert "no es una capa de esta sesión" in problema
