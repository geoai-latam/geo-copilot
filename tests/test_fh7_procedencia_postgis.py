"""FH.7 contra PostGIS REAL: «cómo se hizo» una capa es su procedencia y, hacia atrás, la
de los datasets de los que sale; lo que la modificó en su sitio también consta.

Ejecutar: pytest -m postgis tests/test_fh7_procedencia_postgis.py
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from geo_copilot.platform.contracts import Provenance
from geo_copilot.platform.workspace import DatasetStore, ops
from tests.test_fh5_filtros_postgis import _cliente, _lotes

pytestmark = [pytest.mark.integration, pytest.mark.postgis, pytest.mark.asyncio(loop_scope="session")]

SQL = "SELECT lotcodigo, geom FROM catastro.lotes WHERE manzcodigo = '008510017'"


async def test_la_cadena_va_de_la_capa_a_sus_fuentes_con_sql_y_ediciones(workspace_pool, monkeypatch):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    lotes = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=Provenance(
        capability="core.query_data", produced_at=datetime.now(UTC), sql=SQL, arguments={"query": "los lotes"}))
    buf = (await ops.buffer(store, ws, lotes.id, 50)).ref
    await ops.agregar_medida(store, ws, buf.id, "area")
    async with await _cliente(monkeypatch, store) as http:
        r = await http.get(f"/api/v1/workspace/{ws}/datasets/{buf.id}/procedencia")
        assert r.status_code == 200, r.text
        pasos = r.json()["pasos"]
        assert [p["dataset_id"] for p in pasos] == [buf.id, lotes.id]
        assert pasos[0]["provenance"]["capability"] == "core.buffer"
        assert pasos[0]["provenance"]["arguments"]["dataset"] == lotes.id
        assert [(e["capability"], e["arguments"]["campo"]) for e in pasos[0]["provenance"]["edits"]] == [
            ("core.add_measure", "area_m2")]
        assert pasos[1]["provenance"]["sql"] == SQL and pasos[1]["nombre"] == "Lotes"
        # otra sesión no ve la procedencia de esta
        otra = await http.get(f"/api/v1/workspace/sesion-{uuid.uuid4()}/datasets/{buf.id}/procedencia")
        assert otra.status_code == 404


async def test_una_entrada_que_ya_no_existe_se_dice_no_se_inventa(workspace_pool, monkeypatch):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    perdido = "ds_" + "0" * 16
    ref = await store.ingest_features(ws, "Cruce", _lotes(), crs="EPSG:4326", provenance=Provenance(
        capability="core.overlay", produced_at=datetime.now(UTC), arguments={"dataset_a": perdido}))
    async with await _cliente(monkeypatch, store) as http:
        pasos = (await http.get(f"/api/v1/workspace/{ws}/datasets/{ref.id}/procedencia")).json()["pasos"]
    assert pasos[1] == {"dataset_id": perdido, "disponible": False}


async def test_medir_es_geodesico_exacto_como_el_agente(workspace_pool, monkeypatch):
    """FH.10: la herramienta Medir del mapa mide en PostGIS (geography), igual que ws_measure."""
    lotes = _lotes()
    ring = lotes["features"][0]["geometry"]
    linea = {"type": "LineString", "coordinates": [[-74.1, 4.6], [-74.09, 4.6]]}
    store = DatasetStore(workspace_pool)
    ws = f"sesion-{uuid.uuid4()}"
    async with await _cliente(monkeypatch, store) as http:
        a = (await http.post(f"/api/v1/workspace/{ws}/medir", json={"geometry": ring})).json()
        l_ = (await http.post(f"/api/v1/workspace/{ws}/medir", json={"geometry": linea})).json()
        punto = await http.post(f"/api/v1/workspace/{ws}/medir", json={"geometry": {"type": "Point", "coordinates": [0, 0]}})
    ref = await store.hechos(ws, "SELECT ST_Area(ST_GeomFromGeoJSON($1)::geography) AS a, "
                                 "ST_Length(ST_GeomFromGeoJSON($2)::geography) AS l",
                             (__import__("json").dumps(ring), __import__("json").dumps(linea)))
    assert a["tipo"] == "area" and a["area_m2"] == pytest.approx(ref["a"], abs=0.01)
    assert a["area_ha"] == pytest.approx(ref["a"] / 10_000, abs=1e-4)
    assert l_["tipo"] == "longitud" and l_["longitud_m"] == pytest.approx(ref["l"], abs=0.01)  # ≈ 1 109 m
    assert punto.status_code == 400
