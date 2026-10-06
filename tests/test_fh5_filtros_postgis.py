"""FH.5 contra PostGIS REAL: una herramienta del workspace sobre una capa filtrada recibe el subconjunto.

Ejecutar: pytest -m postgis tests/test_fh5_filtros_postgis.py
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from geo_copilot.platform.contracts import Provenance
from geo_copilot.platform.seleccion import dataset_efectivo
from geo_copilot.platform.workspace import DatasetStore

pytestmark = [pytest.mark.integration, pytest.mark.postgis, pytest.mark.asyncio(loop_scope="session")]

PROC = Provenance(capability="core.query_database", produced_at=datetime.now(UTC))


def _lotes() -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"lotcodigo": f"L{i}", "estrato": e},
         "geometry": {"type": "Polygon", "coordinates": [[[-74.1 + i * 1e-3, 4.6], [-74.099 + i * 1e-3, 4.6],
                                                          [-74.099 + i * 1e-3, 4.601], [-74.1 + i * 1e-3, 4.601],
                                                          [-74.1 + i * 1e-3, 4.6]]]}}
        for i, e in enumerate([1, 3, 3, 2, 3])]}


async def test_la_capa_filtrada_se_materializa_una_vez_por_turno(workspace_pool):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)
    working = {"map_context": {"layers": [{"id": "layer-1", "dataset_id": ref.id,
                                           "filtro": [{"field": "estrato", "op": "=", "value": 3}]}]}}
    ds = await dataset_efectivo(store, ws, ref.id, working)
    assert ds != ref.id
    codigos = sorted(f["properties"]["lotcodigo"] for f in (await store.to_geojson(ws, ds))["features"])
    assert codigos == ["L1", "L2", "L4"]
    # el nombre lleva la condición vigente (de ahí sale la narración del agente)
    assert (await store.get(ws, ds)).name == "Lotes (filtrada: estrato = 3)"
    assert await dataset_efectivo(store, ws, ref.id, working) == ds  # mismo turno: no se repite
    sin_filtro = {"map_context": {"layers": [{"id": "layer-1", "dataset_id": ref.id}]}}
    assert await dataset_efectivo(store, ws, ref.id, sin_filtro) == ref.id


async def _cliente(monkeypatch, store):
    from types import SimpleNamespace

    import httpx

    from geo_copilot.api.app import create_app

    monkeypatch.setattr("geo_copilot.api.routes.workspace.get_app_state", lambda: SimpleNamespace(dataset_store=store))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t")


async def test_filas_paginadas_con_filtro_orden_y_extension(workspace_pool, monkeypatch):
    import json

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)
    filtro = json.dumps([{"field": "estrato", "op": "=", "value": 3}])
    async with await _cliente(monkeypatch, store) as http:
        r = await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/filas",
                           params={"filtro": filtro, "orden": "lotcodigo", "desc": "true", "limit": 2})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["total"] == 3
        assert [f["properties"]["lotcodigo"] for f in j["filas"]] == ["L4", "L2"]
        assert [f["fid"] for f in j["filas"]] == [4, 2]
        assert j["filas"][0]["bbox"][0] == pytest.approx(-74.096)
        pag2 = (await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/filas",
                               params={"filtro": filtro, "orden": "lotcodigo", "desc": "true", "limit": 2,
                                       "offset": 2})).json()
        assert [f["properties"]["lotcodigo"] for f in pag2["filas"]] == ["L1"]
        # solo unos fid (la tabla «solo seleccionados»)
        sel = (await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/filas", params={"ids": "[0, 3]"})).json()
        assert [f["fid"] for f in sel["filas"]] == [0, 3]
        # inyección por el campo de orden o del filtro: 400
        assert (await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/filas",
                               params={"orden": "estrato; drop table x"})).status_code == 400
        malo = json.dumps([{"field": "x\"; drop", "op": "=", "value": 1}])
        assert (await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/filas",
                               params={"filtro": malo})).status_code == 400


async def test_estadistica_de_un_campo_con_filtro(workspace_pool, monkeypatch):
    import json

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)
    async with await _cliente(monkeypatch, store) as http:
        j = (await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/estadistica", params={"campo": "estrato"})).json()
        assert j["numerico"] and j["n"] == 5 and j["min"] == 1 and j["max"] == 3 and j["unicos"] == 3
        assert j["media"] == pytest.approx(2.4)
        assert j["frecuentes"][0] == {"valor": "3", "n": 3}
        f = json.dumps([{"field": "estrato", "op": "<", "value": 3}])
        j2 = (await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/estadistica",
                             params={"campo": "lotcodigo", "filtro": f})).json()
        assert not j2["numerico"] and j2["n"] == 2 and "min" not in j2


async def test_agregar_el_area_de_cada_elemento_a_la_misma_capa(workspace_pool):
    """V5 en Chrome: «el área de cada lote» creaba otra capa con un script; ahora es un campo exacto."""
    from geo_copilot.platform.workspace import WorkspaceError, ops

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)
    res = await ops.agregar_medida(store, ws, ref.id, "area")
    assert res.ref.id == ref.id and res.hechos["campo"] == "area_m2" and res.hechos["elementos"] == 5
    assert "area_m2" in [f.name for f in (await store.get(ws, ref.id)).fields]
    fc = await store.to_geojson(ws, ref.id)
    assert [f["id"] for f in fc["features"]] == [0, 1, 2, 3, 4]  # mismos fid: la selección sigue valiendo
    real = await store.hechos(ws, f"SELECT round(ST_Area(geom::geography)::numeric, 2)::float AS a FROM "
                                  f"{ops._tabla(ref)} WHERE fid = 0")
    assert fc["features"][0]["properties"]["area_m2"] == pytest.approx(real["a"])
    # otra vez: no duplica la columna
    await ops.agregar_medida(store, ws, ref.id, "area")
    assert [f.name for f in (await store.get(ws, ref.id)).fields].count("area_m2") == 1
    with pytest.raises(WorkspaceError):
        await ops.agregar_medida(store, ws, ref.id, "volumen")
