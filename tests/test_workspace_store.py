"""S2.1 — Dataset Store: el workspace espacial contra PostGIS REAL.

Corre con la BD de docker-compose.test.yml (`pytest -m postgis`), conectado como
`gc_app` (espejo de `geo_app`): escribe solo asumiendo `geo_workspace`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from geo_copilot.platform.contracts import GeometryColumn, Provenance
from geo_copilot.platform.workspace import (
    DatasetStore,
    QuotaExceeded,
    WorkspaceError,
    WorkspaceLimits,
    schema_de,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgis,
    pytest.mark.asyncio(loop_scope="session"),
]

PROC = Provenance(capability="core.query_database", produced_at=datetime.now(UTC))


def _ws() -> str:
    return f"sesion-{uuid.uuid4()}"


def _lotes(n: int = 5) -> dict:
    return {"type": "FeatureCollection", "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [-74.15 + i * 1e-3, 4.55], [-74.149 + i * 1e-3, 4.55],
                [-74.149 + i * 1e-3, 4.551], [-74.15 + i * 1e-3, 4.551],
                [-74.15 + i * 1e-3, 4.55],
            ]]},
            "properties": {
                "lotcodigo": f"0045{i:06d}", "lotdispers": "N" if i % 2 else "D",
                "area_m2": 100.5 + i, "pisos": i, "activo": i % 2 == 0,
                "meta": {"fuente": "catastro"},
            },
        }
        for i in range(n)
    ]}


async def test_ida_y_vuelta_de_lotes(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)

    assert ref.kind == "vector" and ref.feature_count == 5
    assert ref.storage.kind == "workspace-table"
    assert ref.storage.schema_name == schema_de(ws)
    assert ref.geometry_type == "Polygon"
    assert ref.bbox[0] == pytest.approx(-74.15) and ref.bbox[3] == pytest.approx(4.551)
    tipos = {f.name: f.type for f in ref.fields}
    assert tipos == {"lotcodigo": "string", "lotdispers": "string", "area_m2": "number",
                     "pisos": "integer", "activo": "boolean", "meta": "json"}

    assert [r.id for r in await store.list_datasets(ws)] == [ref.id]
    fc = await store.to_geojson(ws, ref.id)
    assert len(fc["features"]) == 5
    p = fc["features"][2]["properties"]
    assert p["lotcodigo"] == "0045000002" and p["pisos"] == 2 and p["meta"] == {"fuente": "catastro"}
    assert fc["features"][2]["geometry"]["type"] == "Polygon"
    # FH.2: cada elemento lleva su identidad (fid) como `id` de Feature
    assert [f["id"] for f in fc["features"]] == [0, 1, 2, 3, 4]


async def test_reproyecta_desde_el_crs_declarado(workspace_pool):
    """Un punto en MAGNA-SIRGAS origen nacional (EPSG:9377) queda en 4326 donde debe."""
    from pyproj import Transformer

    x, y = Transformer.from_crs(4326, 9377, always_xy=True).transform(-74.0721, 4.7110)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
         "properties": {"nombre": "Bogotá"}},
    ]}
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await store.ingest_features(ws, "Punto 9377", fc, crs="EPSG:9377", provenance=PROC)
    lon, lat = (await store.to_geojson(ws, ref.id))["features"][0]["geometry"]["coordinates"]
    assert lon == pytest.approx(-74.0721, abs=1e-6) and lat == pytest.approx(4.7110, abs=1e-6)
    assert ref.crs == "EPSG:4326"


async def test_crs_no_soportado_falla_honesto(workspace_pool):
    with pytest.raises(WorkspaceError, match="CRS no soportado"):
        await DatasetStore(workspace_pool).ingest_features(
            _ws(), "x", _lotes(1), crs="ESRI:102100", provenance=PROC,
        )


async def test_nombres_de_campo_raros_se_sanean(workspace_pool):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74, 4.6]},
         "properties": {"Área (m²)": 12.5, "1col": "a", "fid": 7, "geom": "x",
                        'na"me; DROP TABLE x': "b"}},
    ]}
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await store.ingest_features(ws, "raros", fc, crs="EPSG:4326", provenance=PROC)
    nombres = [f.name for f in ref.fields]
    assert nombres == ["rea_m", "c_1col", "fid_2", "geom_2", "na_me_drop_table_x"]
    props = (await store.to_geojson(ws, ref.id))["features"][0]["properties"]
    assert props["rea_m"] == 12.5 and props["na_me_drop_table_x"] == "b"


async def test_tabla_con_wkb_hex_y_con_latlon(workspace_pool):
    from shapely.geometry import Point

    store, ws = DatasetStore(workspace_pool), _ws()
    wkb = Point(-74.1, 4.6).wkb_hex
    ref = await store.ingest_table(
        ws, "ventas", [{"municipio": "Soacha", "ventas": 1200, "geom_wkb": wkb}],
        geometry=GeometryColumn(column="geom_wkb", encoding="wkb_hex", crs="EPSG:4326"),
        provenance=PROC,
    )
    assert [f.name for f in ref.fields] == ["municipio", "ventas"]
    assert (await store.to_geojson(ws, ref.id))["features"][0]["geometry"]["coordinates"] == [-74.1, 4.6]

    ref2 = await store.ingest_table(
        ws, "estaciones", [{"lon": -74.2, "lat": 4.5, "id": 1}, {"lon": None, "lat": 4.5, "id": 2}],
        geometry=GeometryColumn(column="lon", lat_column="lat", encoding="latlon", crs="EPSG:4326"),
        provenance=PROC,
    )
    feats = (await store.to_geojson(ws, ref2.id))["features"]
    assert feats[0]["geometry"]["coordinates"] == [-74.2, 4.5]
    assert feats[1]["geometry"] is None  # sin coordenadas: sin geometría, no inventada


async def test_tabla_sin_geometria(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await store.ingest_table(
        ws, "conteo", [{"pisos": 1, "n": 40}, {"pisos": 2, "n": 12}], geometry=None, provenance=PROC,
    )
    assert ref.kind == "table" and ref.storage.geometry_column is None


async def test_cuota_por_dataset(workspace_pool):
    store = DatasetStore(workspace_pool, WorkspaceLimits(max_features_per_dataset=3))
    with pytest.raises(QuotaExceeded, match="máximo por dataset"):
        await store.ingest_features(_ws(), "grande", _lotes(4), crs="EPSG:4326", provenance=PROC)


async def test_cuota_por_workspace(workspace_pool):
    store, ws = DatasetStore(workspace_pool, WorkspaceLimits(max_features_per_workspace=6)), _ws()
    await store.ingest_features(ws, "a", _lotes(4), crs="EPSG:4326", provenance=PROC)
    with pytest.raises(QuotaExceeded, match="superaría"):
        await store.ingest_features(ws, "b", _lotes(4), crs="EPSG:4326", provenance=PROC)


async def test_vencidos_no_se_listan_y_se_purgan(workspace_pool):
    store, ws = DatasetStore(workspace_pool, WorkspaceLimits(ttl_hours=-1)), _ws()
    ref = await store.ingest_features(ws, "efímero", _lotes(2), crs="EPSG:4326", provenance=PROC)
    assert await store.list_datasets(ws) == []
    assert await store.purge_expired() >= 1
    async with workspace_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE geo_workspace")
        existe = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL",
            f"{ref.storage.schema_name}.{ref.storage.table}",
        )
    assert existe is False


async def test_una_sesion_no_ve_los_datasets_de_otra(workspace_pool):
    store, ws_a, ws_b = DatasetStore(workspace_pool), _ws(), _ws()
    ref = await store.ingest_features(ws_a, "de A", _lotes(2), crs="EPSG:4326", provenance=PROC)
    assert await store.get(ws_b, ref.id) is None
    with pytest.raises(WorkspaceError, match="no existe en esta sesión"):
        await store.to_geojson(ws_b, ref.id)
    assert schema_de(ws_a) != schema_de(ws_b)


async def test_sin_asumir_el_rol_la_app_no_escribe(workspace_pool):
    """gc_app es NOINHERIT: fuera de SET LOCAL ROLE no puede escribir ni en su workspace."""
    import asyncpg

    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await store.ingest_features(ws, "x", _lotes(1), crs="EPSG:4326", provenance=PROC)
    destino = f'"{ref.storage.schema_name}"."{ref.storage.table}"'
    async with workspace_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(f"DELETE FROM {destino}")


async def test_el_lector_lee_el_workspace_pero_no_escribe(workspace_pool):
    import asyncpg

    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await store.ingest_features(ws, "x", _lotes(3), crs="EPSG:4326", provenance=PROC)
    destino = f'"{ref.storage.schema_name}"."{ref.storage.table}"'
    async with workspace_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE gis_readonly")
        assert await conn.fetchval(f"SELECT count(*) FROM {destino}") == 3
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(f"DELETE FROM {destino}")
