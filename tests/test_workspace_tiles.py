"""S2.3 — capas grandes del workspace como teselas MVT, autorizadas por sesión."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api import dependencies as deps
from geo_copilot.api.app import create_app
from geo_copilot.api.routes import query as q
from geo_copilot.api.routes import workspace as ws_route
from geo_copilot.platform.contracts import Provenance
from geo_copilot.platform.workspace import WorkspaceError

DS = "ds_0123456789abcdef"


@pytest.fixture
def cliente(monkeypatch):
    store = SimpleNamespace(tile=AsyncMock(return_value=b"\x1a\x02mvt"))
    monkeypatch.setattr(
        "geo_copilot.api.routes.tiles.get_app_state", lambda: SimpleNamespace(dataset_store=store),
    )
    return SimpleNamespace(http=TestClient(create_app()), store=store)


def test_sirve_la_tesela_del_dataset_de_la_sesion(cliente):
    r = cliente.http.get(f"/api/v1/tiles/ws/sess-a/{DS}/3/2/4.pbf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    assert r.headers["cache-control"].startswith("private")
    cliente.store.tile.assert_awaited_once_with("sess-a", DS, 3, 2, 4)


def test_dataset_de_otra_sesion_es_404(cliente):
    cliente.store.tile.side_effect = WorkspaceError("no existe en esta sesión")
    assert cliente.http.get(f"/api/v1/tiles/ws/sess-b/{DS}/3/2/4.pbf").status_code == 404


def test_tesela_vacia_es_204(cliente):
    cliente.store.tile.return_value = None
    assert cliente.http.get(f"/api/v1/tiles/ws/sess-a/{DS}/3/2/4.pbf").status_code == 204


@pytest.mark.parametrize("ruta", [
    "/api/v1/tiles/ws/sess-a/lotes/3/2/4.pbf",              # id que no emite el store
    f"/api/v1/tiles/ws/ses%20a/{DS}/3/2/4.pbf",           # sesión mal formada
    f"/api/v1/tiles/ws/sess-a/{DS}/3/9/4.pbf",            # x fuera de la pirámide
])
def test_entradas_invalidas_son_400(cliente, ruta):
    assert cliente.http.get(ruta).status_code == 400
    cliente.store.tile.assert_not_called()


@pytest.mark.parametrize("schema", ["ws_0123abcd", "ws_meta"])
def test_la_ruta_generica_no_sirve_el_workspace(cliente, schema):
    """Adivinar el esquema de otra sesión no da acceso por la ruta de dominio."""
    app = cliente.http.app
    app.dependency_overrides[deps.get_db_pool] = lambda: object()
    r = cliente.http.get(f"/api/v1/tiles/{schema}/datasets/3/2/4.pbf")
    assert r.status_code == 404


def test_umbral_inline_vs_mvt(monkeypatch):
    monkeypatch.setattr(q.get_settings(), "workspace_inline_max_features", 100)
    ref = {"id": DS, "feature_count": 101, "geometry_type": "Polygon",
           "bbox": [-74.2, 4.5, -74.0, 4.7], "storage": {"geometry_column": "geom"},
           "fields": [{"name": "lotcodigo", "type": "string"}]}
    t = ws_route.teselas_de("sess-a", ref)
    assert t["url"] == f"/api/v1/tiles/ws/sess-a/{DS}/{{z}}/{{x}}/{{y}}.pbf"
    assert t["source_layer"] == "dataset" and t["bbox"] == ref["bbox"]
    assert t["fields"] == ["lotcodigo"]
    assert ws_route.teselas_de("sess-a", {**ref, "feature_count": 100}) is None
    assert ws_route.teselas_de("sess-a", {**ref, "storage": {"geometry_column": None}}) is None
    assert ws_route.teselas_de("sess-a", None) is None


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_tesela_real_y_aislada(workspace_pool):
    import math

    from geo_copilot.platform.workspace import DatasetStore

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.08 + i * 1e-4, 4.6]},
         "properties": {"n": i, "meta": {"a": 1}}}
        for i in range(50)
    ]}
    ref = await store.ingest_features(
        ws, "puntos", fc, crs="EPSG:4326",
        provenance=Provenance(capability="core.query_database", produced_at=datetime.now(UTC)),
    )
    # tesela z12 (XYZ) que contiene (-74.08, 4.6)
    z, lon, lat = 12, -74.08, 4.6
    x = int((lon + 180) / 360 * 2**z)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**z)

    crudo = await store.tile(ws, ref.id, z, x, y)
    assert crudo and b"dataset" in crudo
    # propiedades codificadas (el json aplanado a texto no rompe ST_AsMVT)
    assert b"meta" in crudo and b"n" in crudo
    # tesela lejana: vacía
    assert await store.tile(ws, ref.id, z, 0, 0) is None

    with pytest.raises(WorkspaceError):
        await store.tile(f"otra-{uuid.uuid4()}", ref.id, z, x, y)


def test_h22_con_teselas_la_tabla_tampoco_viaja_entera(monkeypatch):
    """41.033 puntos: la geometría iba por teselas pero la respuesta pesaba 18 MB
    por las filas de atributos. Van las primeras N, marcadas como truncadas."""
    from unittest.mock import AsyncMock, MagicMock

    from geo_copilot.api import dependencies as deps
    from geo_copilot.orchestrator.conversation import ConversationManager

    monkeypatch.setattr(q.get_settings(), "workspace_inline_max_features", 10)
    filas = [{"zip": str(i)} for i in range(50)]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": f} for f in filas
    ]}
    ref = {"id": DS, "name": "ZIPs", "kind": "vector", "provider": "core", "crs": "EPSG:4326",
           "feature_count": 50, "geometry_type": "Point", "bbox": None,
           "storage": {"kind": "workspace-table", "schema_name": "ws_prueba", "table": "d_prueba",
                       "geometry_column": "geom"},
           "provenance": {"capability": "core.load_external", "produced_at": "2026-09-25T00:00:00Z"},
           "fields": [{"name": "zip", "type": "string"}]}
    monkeypatch.setattr(q, "_materializar_resultado", AsyncMock(return_value=ref))
    monkeypatch.setattr("geo_copilot.api.websocket.send_result", AsyncMock())
    monkeypatch.setattr("geo_copilot.api.websocket.send_status", AsyncMock())
    graph = MagicMock()
    graph.process = AsyncMock(return_value={"success": True, "message": "ok", "intent": "load_external",
                                             "geojson": fc, "data": {"results": filas}})
    app = create_app()
    manager = ConversationManager()
    manager.create_session("sess-a")
    app.dependency_overrides[deps.get_conversation_manager] = lambda: manager
    app.dependency_overrides[deps.get_agent_graph] = lambda: graph
    arts = TestClient(app).post("/api/v1/query/", json={"query": "carga", "session_id": "sess-a"}).json()["artifacts"]
    capa = next(a for a in arts if a["kind"] == "layer")
    tabla = next(a for a in arts if a["kind"] == "table")
    assert capa["tiles"] and capa["inline"] is None
    # la tabla viaja recortada al tope inline, con el total real (y no se pierde entera)
    assert len(tabla["preview"]) == 10 and tabla["total_rows"] == 50 and tabla["rows_ref"] == DS


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_h24_con_el_tope_por_tesela_el_recorte_es_uniforme(workspace_pool, monkeypatch):
    """Tope que muerde (zoom bajo): antes salían los primeros por fid —en ZIPs de
    EE. UU., solo el noreste—. Ahora la muestra cubre toda la extensión."""
    from geo_copilot.platform.workspace import DatasetStore
    from geo_copilot.platform.workspace import store as st

    monkeypatch.setattr(st, "MVT_MAX_FEATURES", 100)
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    # 1000 puntos ordenados de oeste a este (el fid sigue la longitud)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-120 + i * 0.05, 40.0]},
         "properties": {"i": i}}
        for i in range(1000)
    ]}
    ref = await store.ingest_features(
        ws, "oeste-este", fc, crs="EPSG:4326",
        provenance=Provenance(capability="t", produced_at=datetime.now(UTC)),
    )
    async with workspace_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE gis_readonly")
        t = f'"{ref.storage.schema_name}"."{ref.storage.table}"'
        # la misma selección que hace la tesela (z0 cubre todo)
        filas = await conn.fetch(f"SELECT fid FROM {t} ORDER BY md5(fid::text) LIMIT 100")
    fids = sorted(r["fid"] for r in filas)
    # repartida: hay fids del primer y del último tercio (antes: solo 0..99)
    assert min(fids) < 333 and max(fids) > 666
    assert await store.tile(ws, ref.id, 0, 0, 0)  # y la tesela sale
    # la tesela hace EXACTAMENTE esa selección (no hay decodificador MVT instalado)
    import inspect
    assert "ORDER BY md5(t.fid::text) LIMIT" in inspect.getsource(DatasetStore.tile)
