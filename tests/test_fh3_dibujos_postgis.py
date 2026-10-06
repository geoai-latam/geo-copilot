"""FH.3 contra PostGIS REAL: un dibujo es un dataset del workspace, renombrable y editable.

Ejecutar: pytest -m postgis tests/test_fh3_dibujos_postgis.py
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from geo_copilot.platform.contracts import Provenance
from geo_copilot.platform.workspace import DatasetStore, WorkspaceError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgis,
    pytest.mark.asyncio(loop_scope="session"),
]

PROV = Provenance(capability="user.sketch", produced_at=datetime.now(UTC))


def _cuadrado(x: float, lado: float = 0.001) -> dict:
    return {"type": "Polygon", "coordinates": [[[x, 4.6], [x + lado, 4.6], [x + lado, 4.6 + lado],
                                                [x, 4.6 + lado], [x, 4.6]]]}


async def _dibujo(store: DatasetStore, ws: str):
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": _cuadrado(-74.1), "properties": {}}]}
    return await store.ingest_features(ws, "Área 1", fc, crs="EPSG:4326", provenance=PROV, provider="sketch")


async def test_un_dibujo_sin_propiedades_es_un_dataset(workspace_pool):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await _dibujo(store, ws)
    assert ref.provider == "sketch" and ref.provenance.capability == "user.sketch"
    assert ref.geometry_type == "Polygon" and ref.feature_count == 1 and ref.fields == []
    fc = await store.to_geojson(ws, ref.id)
    assert fc["features"][0]["id"] == 0 and fc["features"][0]["geometry"]["type"] == "Polygon"


async def test_renombrar_cambia_lo_que_ven_el_mapa_y_el_agente(workspace_pool):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await _dibujo(store, ws)
    await store.renombrar(ws, ref.id, "  Finca La Esperanza ")
    assert (await store.get(ws, ref.id)).name == "Finca La Esperanza"
    assert [r.name for r in await store.list_datasets(ws)] == ["Finca La Esperanza"]
    with pytest.raises(WorkspaceError):
        await store.renombrar(ws, ref.id, "   ")
    with pytest.raises(WorkspaceError):
        await store.renombrar(f"otra-{uuid.uuid4()}", ref.id, "robo")


async def test_editar_vertices_conserva_el_id_y_actualiza_la_extension(workspace_pool):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await _dibujo(store, ws)
    nuevo = _cuadrado(-74.2, lado=0.01)
    editado = await store.reemplazar_geometrias(
        ws, ref.id, {"type": "FeatureCollection", "features": [{"type": "Feature", "id": 0, "geometry": nuevo}]})
    assert editado.id == ref.id
    assert editado.bbox[0] == pytest.approx(-74.2) and editado.bbox[2] == pytest.approx(-74.19)
    geom = (await store.to_geojson(ws, ref.id))["features"][0]["geometry"]
    assert geom["coordinates"][0][0] == pytest.approx([-74.2, 4.6])
    # un fid que no existe no se inventa
    with pytest.raises(WorkspaceError):
        await store.reemplazar_geometrias(
            ws, ref.id, {"type": "FeatureCollection", "features": [{"type": "Feature", "id": 7, "geometry": nuevo}]})
