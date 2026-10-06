"""FH.3 — los endpoints de dibujos: crear, renombrar y editar vértices (store simulado).

El store real se prueba contra PostGIS en test_fh3_dibujos_postgis.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app

DS = "ds_0123456789abcdef"
POLI = {"type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.09, 4.6], [-74.09, 4.61], [-74.1, 4.6]]]}
FC = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": POLI, "properties": {"x": 1}}]}


def _ref(provider: str = "sketch", name: str = "Área 1") -> SimpleNamespace:
    return SimpleNamespace(id=DS, provider=provider, name=name,
                           model_dump=lambda mode=None: {"id": DS, "name": name, "provider": provider})


@pytest.fixture
def cliente(monkeypatch):
    store = SimpleNamespace(
        ingest_features=AsyncMock(return_value=_ref()),
        get=AsyncMock(side_effect=lambda ws, ds: _ref() if ws == "sess-a" else None),
        renombrar=AsyncMock(side_effect=lambda ws, ds, n: _ref(name=n.strip())),
        reemplazar_geometrias=AsyncMock(return_value=_ref()),
        to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": 0, "geometry": POLI, "properties": {}}]}),
    )
    monkeypatch.setattr("geo_copilot.api.routes.workspace.get_app_state",
                        lambda: SimpleNamespace(dataset_store=store))
    return SimpleNamespace(http=TestClient(create_app()), store=store)


def test_crear_un_dibujo_lo_guarda_como_sketch_sin_las_propiedades_del_cliente(cliente):
    r = cliente.http.post("/api/v1/workspace/sess-a/sketches", json={"name": "Área 1", "geojson": FC})
    assert r.status_code == 201
    j = r.json()
    assert j["layer_ref"]["id"] == DS and j["geojson"]["features"][0]["id"] == 0 and j["tiles"] is None
    args, kw = cliente.store.ingest_features.call_args
    assert args[0] == "sess-a" and args[1] == "Área 1"
    assert args[2]["features"][0]["properties"] == {}  # nada del cliente entra como atributo
    assert kw["provider"] == "sketch" and kw["provenance"].capability == "user.sketch"
    assert kw["provenance"].arguments == {"geometria": "Polygon"}


@pytest.mark.parametrize("geojson", [
    {"type": "FeatureCollection", "features": []},
    {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "GeometryCollection"}}]},
    {"type": "Feature", "geometry": POLI},
    {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0]] * 20_001}}]},
])
def test_un_dibujo_invalido_es_400(cliente, geojson):
    r = cliente.http.post("/api/v1/workspace/sess-a/sketches", json={"name": "x", "geojson": geojson})
    assert r.status_code == 400
    cliente.store.ingest_features.assert_not_called()


def test_renombrar(cliente):
    r = cliente.http.patch(f"/api/v1/workspace/sess-a/datasets/{DS}", json={"name": " Finca "})
    assert r.status_code == 200 and r.json()["layer_ref"]["name"] == "Finca"
    cliente.store.renombrar.assert_awaited_once_with("sess-a", DS, " Finca ")


def test_editar_vertices_de_un_dibujo_por_su_fid(cliente):
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": 0, "geometry": POLI}]}
    r = cliente.http.patch(f"/api/v1/workspace/sess-a/datasets/{DS}", json={"geojson": fc})
    assert r.status_code == 200
    enviado = cliente.store.reemplazar_geometrias.call_args.args[2]
    assert enviado["features"][0]["id"] == 0


def test_solo_se_editan_los_vertices_de_un_dibujo(cliente):
    cliente.store.get.side_effect = lambda ws, ds: _ref(provider="core")
    r = cliente.http.patch(f"/api/v1/workspace/sess-a/datasets/{DS}", json={"geojson": FC})
    assert r.status_code == 409
    cliente.store.reemplazar_geometrias.assert_not_called()


def test_dataset_de_otra_sesion_es_404_y_nada_que_cambiar_es_400(cliente):
    assert cliente.http.patch(f"/api/v1/workspace/sess-b/datasets/{DS}", json={"name": "x"}).status_code == 404
    assert cliente.http.patch(f"/api/v1/workspace/sess-a/datasets/{DS}", json={}).status_code == 400


def test_el_llm_ve_que_la_capa_la_dibujo_el_usuario():
    from geo_copilot.core.formatters import format_map_context

    txt = format_map_context({
        "layers": [{"id": "layer-3", "name": "Área 1", "kind": "vector-geojson", "geometry_type": "Polygon",
                    "feature_count": 1, "dataset_id": DS, "fields": [], "visible": True, "is_active": True,
                    "origin": {"capability": "user.sketch", "arguments": {"geometria": "Polygon"}}}],
        "acciones": [
            {"op": "add_layer", "layer_id": "layer-3", "layer_name": "Área 1", "args": {"dibujo": "Polygon"},
             "at": "2026-09-25T00:00:00Z", "author": "user", "undone": False},
            {"op": "rename_layer", "layer_id": "layer-3", "layer_name": "Finca", "args": {"antes": "Área 1"},
             "at": "2026-09-25T00:00:01Z", "author": "user", "undone": False},
            {"op": "edit_geometry", "layer_id": "layer-3", "layer_name": "Finca", "args": {},
             "at": "2026-09-25T00:00:02Z", "author": "user", "undone": False},
        ],
    })
    assert f'[layer-3] "Área 1" (dataset {DS}) — >> ACTIVA; Polygon; 1 features; campos: —; DIBUJADA por el usuario' in txt
    assert "el USUARIO dibujó «Área 1» (dibujo=Polygon)" in txt
    assert "el USUARIO renombró la capa «Finca» (antes=Área 1)" in txt
    assert "el USUARIO editó los vértices de «Finca»" in txt


@pytest.mark.asyncio
async def test_el_bloque_del_workspace_marca_los_dibujos(monkeypatch):
    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.platform.workspace import context as wsctx

    ref = SimpleNamespace(id=DS, name="Área 1", provider="sketch", geometry_type="Polygon",
                          feature_count=1, fields=[])
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(list_datasets=AsyncMock(return_value=[ref])))
    assert f"{DS} «Área 1» (DIBUJADO por el usuario) — Polygon" in await ce.bloque_workspace("s")
    # V5: borrado del mapa → el agente lo sabe (sigue en el workspace, pero ya no se ve)
    en_mapa = {"layers": [{"id": "layer-9", "dataset_id": DS}]}
    assert "NO está en el mapa" not in await ce.bloque_workspace("s", en_mapa)
    assert "(DIBUJADO por el usuario) (NO está en el mapa ahora" in await ce.bloque_workspace("s", {"layers": []})


@pytest.mark.asyncio
async def test_el_hub_resuelve_una_capa_por_su_nombre_exacto_si_es_unica():
    """V4 FH.3: el LLM pasó «Área 1» (el nombre que ve el usuario) como área de interés."""
    from geo_copilot.platform.mcp.hub import _geojson_de_referencia

    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": POLI, "properties": {}}]}
    working = {"map_context": {"layers": [{"id": "layer-1", "name": "Lotes"}, {"id": "layer-2", "name": "Área 1"}]},
               "map_layers": {"layer-2": {"data": fc}}}
    assert await _geojson_de_referencia("Área 1", working) == fc
    assert await _geojson_de_referencia(" [área 1] ", working) == fc
    # dos capas con el mismo nombre: no se elige una
    working["map_context"]["layers"].append({"id": "layer-3", "name": "Área 1"})
    assert await _geojson_de_referencia("Área 1", working) is None
