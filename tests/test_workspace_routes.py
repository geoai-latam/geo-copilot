"""E2.4 — el cliente restaura sus capas del workspace tras recargar la página."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app

DS = "ds_0123456789abcdef"
FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74, 4.6]}, "properties": {"n": 1}},
]}


def _ref(n: int) -> SimpleNamespace:
    dump = {"id": DS, "name": "Lotes", "feature_count": n, "geometry_type": "Point",
            "bbox": [-74, 4.6, -74, 4.6], "fields": [{"name": "n", "type": "integer"}],
            "storage": {"kind": "workspace-table", "geometry_column": "geom"}}
    return SimpleNamespace(
        model_dump=lambda mode=None: dump,
        storage=SimpleNamespace(kind="workspace-table", geometry_column="geom"),
    )


@pytest.fixture
def cliente(monkeypatch):
    store = SimpleNamespace(
        get=AsyncMock(side_effect=lambda ws, ds: _ref(1) if ws == "sess-a" else None),
        to_geojson=AsyncMock(return_value=FC),
        list_datasets=AsyncMock(return_value=[_ref(1)]),
    )
    monkeypatch.setattr(
        "geo_copilot.api.routes.workspace.get_app_state", lambda: SimpleNamespace(dataset_store=store),
    )
    return SimpleNamespace(http=TestClient(create_app()), store=store)


def test_capa_chica_vuelve_inline(cliente):
    r = cliente.http.get(f"/api/v1/workspace/sess-a/datasets/{DS}/capa")
    assert r.status_code == 200
    j = r.json()
    assert j["layer_ref"]["id"] == DS and j["geojson"] == FC and j["tiles"] is None


def test_capa_grande_vuelve_como_teselas(cliente, monkeypatch):
    cliente.store.get.side_effect = lambda ws, ds: _ref(60_000)
    j = cliente.http.get(f"/api/v1/workspace/sess-a/datasets/{DS}/capa").json()
    assert j["geojson"] is None
    assert j["tiles"]["url"] == f"/api/v1/tiles/ws/sess-a/{DS}/{{z}}/{{x}}/{{y}}.pbf"
    cliente.store.to_geojson.assert_not_called()


def test_dataset_de_otra_sesion_es_404(cliente):
    assert cliente.http.get(f"/api/v1/workspace/sess-b/datasets/{DS}/capa").status_code == 404


@pytest.mark.parametrize("ruta", [
    "/api/v1/workspace/sess-a/datasets/lotes/capa",
    f"/api/v1/workspace/ses%20a/datasets/{DS}/capa",
    "/api/v1/workspace/ses%20a/datasets",
])
def test_ids_invalidos_son_400(cliente, ruta):
    assert cliente.http.get(ruta).status_code == 400


def test_lista_los_datasets_de_la_sesion(cliente):
    j = cliente.http.get("/api/v1/workspace/sess-a/datasets").json()
    assert [d["id"] for d in j["datasets"]] == [DS]
    cliente.store.list_datasets.assert_awaited_once_with("sess-a")
