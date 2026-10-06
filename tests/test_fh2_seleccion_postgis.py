"""FH.2 contra PostGIS REAL: lo que el usuario seleccionó es lo que se materializa.

La identidad que ve el mapa (el `id` de cada Feature del GeoJSON del workspace)
y la que usa el backend (`fid`) son la misma, también en datasets hechos por SQL
(que numeran su fid desde 1). Ejecutar: pytest -m postgis tests/test_fh2_seleccion_postgis.py
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from geo_copilot.platform.contracts import Provenance
from geo_copilot.platform.seleccion import Seleccion, dataset_de_seleccion
from geo_copilot.platform.workspace import DatasetStore
from geo_copilot.platform.workspace.ops import _props, _tabla

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgis,
    pytest.mark.asyncio(loop_scope="session"),
]

PROC = Provenance(capability="core.query_database", produced_at=datetime.now(UTC))


def _lotes(n: int = 6) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[
             [-74.15 + i * 1e-3, 4.55], [-74.149 + i * 1e-3, 4.55],
             [-74.149 + i * 1e-3, 4.551], [-74.15 + i * 1e-3, 4.551], [-74.15 + i * 1e-3, 4.55]]]},
         "properties": {"lotcodigo": f"L{i}", "area_m2": 100 * (i + 1)}}
        for i in range(n)
    ]}


async def _codigos(store: DatasetStore, ws: str, ds: str) -> list[str]:
    return sorted(f["properties"]["lotcodigo"] for f in (await store.to_geojson(ws, ds))["features"])


@pytest.mark.parametrize("origen", ["ingest", "sql"])
async def test_los_ids_del_mapa_materializan_esos_elementos(workspace_pool, origen):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)
    if origen == "sql":
        # dataset hecho por SQL, en OTRO orden: fid desde 1 y no por posición
        cols = ", ".join([*_props(ref, "s"), "s.geom"])
        ref = await store.crear_desde_sql(
            ws, "Lotes ordenados", f"SELECT {cols} FROM {_tabla(ref)} s ORDER BY s.area_m2 DESC", (),
            provenance=PROC)
    fc = await store.to_geojson(ws, ref.id)
    # el usuario hace clic en L1 y L4: el mapa ve sus `id`
    ids = tuple(f["id"] for f in fc["features"] if f["properties"]["lotcodigo"] in ("L1", "L4"))
    sel = Seleccion(layer_id="layer-1", layer_name="Lotes", ids=ids, where=None, count=2,
                    origin="click", dataset_id=str(ref.id))
    nuevo = await dataset_de_seleccion(store, ws, sel, {"map_context": {}})
    assert nuevo is not None
    assert await _codigos(store, ws, nuevo) == ["L1", "L4"]


async def test_la_condicion_se_evalua_en_la_bd(workspace_pool):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)
    sel = Seleccion(layer_id="layer-1", layer_name="Lotes", ids=None,
                    where={"field": "area_m2", "op": ">", "value": 400}, count=None,
                    origin="agent", dataset_id=str(ref.id))
    nuevo = await dataset_de_seleccion(store, ws, sel, {"map_context": {}})
    assert await _codigos(store, ws, nuevo) == ["L4", "L5"]
