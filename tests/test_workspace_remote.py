"""S2.1 — ingesta de `feature_ref` remotos (GeoJSON / GeoParquet) en el workspace.

La lectura se prueba sin BD; la descarga real va por safe_http (SSRF); el camino
completo se prueba contra PostGIS con la descarga sustituida.
"""

from __future__ import annotations

import io
import json

import geopandas as gpd
import pytest
from shapely.geometry import Point

from geo_copilot.platform.workspace import WorkspaceError
from geo_copilot.platform.workspace.store import _descargar, leer_remoto


def _parquet(crs: int, puntos=((-74.07, 4.71), (-74.1, 4.6))) -> bytes:
    gdf = gpd.GeoDataFrame({"nombre": ["a", "b"]},
                           geometry=[Point(x, y) for x, y in puntos], crs="EPSG:4326")
    if crs != 4326:
        gdf = gdf.to_crs(crs)
    buf = io.BytesIO()
    gdf.to_parquet(buf)
    return buf.getvalue()


def test_geoparquet_en_el_crs_declarado():
    fc = leer_remoto(_parquet(4326), format="geoparquet", crs_declarado="EPSG:4326")
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 2
    assert fc["features"][0]["properties"]["nombre"] == "a"


def test_crs_del_archivo_distinto_del_declarado_falla():
    with pytest.raises(WorkspaceError, match="no se elige uno a ciegas"):
        leer_remoto(_parquet(9377), format="geoparquet", crs_declarado="EPSG:4326")


def test_geojson_remoto():
    fc = {"type": "FeatureCollection", "features": []}
    assert leer_remoto(json.dumps(fc).encode(), format="geojson", crs_declarado="EPSG:4326") == fc
    with pytest.raises(WorkspaceError, match="no es un FeatureCollection"):
        leer_remoto(b'{"type":"Feature"}', format="geojson", crs_declarado="EPSG:4326")


def test_formato_no_soportado_y_archivo_roto():
    with pytest.raises(WorkspaceError, match="no soportado"):
        leer_remoto(b"x", format="shapefile", crs_declarado="EPSG:4326")
    with pytest.raises(WorkspaceError, match="no se pudo leer"):
        leer_remoto(b"esto no es parquet", format="geoparquet", crs_declarado="EPSG:4326")


@pytest.mark.asyncio
async def test_la_descarga_rechaza_redes_privadas():
    with pytest.raises(WorkspaceError, match="URL no permitida"):
        await _descargar("http://127.0.0.1:8000/x.parquet", max_bytes=1000)


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_ingesta_remota_completa(workspace_pool, monkeypatch):
    """GeoParquet en EPSG:9377 declarado como tal → queda en 4326 en el workspace."""
    import uuid
    from datetime import UTC, datetime

    from geo_copilot.platform.contracts import Provenance
    from geo_copilot.platform.workspace import DatasetStore
    from geo_copilot.platform.workspace import store as store_mod

    async def falsa(uri, *, max_bytes):
        return _parquet(9377)

    monkeypatch.setattr(store_mod, "_descargar", falsa)
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_remote(
        ws, "Puntos remotos", "https://datos.example/p.parquet", format="geoparquet",
        crs="EPSG:9377",
        provenance=Provenance(capability="mcp.demo.export", produced_at=datetime.now(UTC)),
    )
    lon, lat = (await store.to_geojson(ws, ref.id))["features"][0]["geometry"]["coordinates"]
    assert lon == pytest.approx(-74.07, abs=1e-6) and lat == pytest.approx(4.71, abs=1e-6)
