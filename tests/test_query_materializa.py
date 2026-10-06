"""S2.1 — el resultado geográfico de un turno se materializa en el workspace.

Best-effort: el workspace nunca rompe una respuesta que ya funcionaba.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.api.routes import query as q
from geo_copilot.platform.workspace import WorkspaceError

FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74, 4.6]}, "properties": {}},
]}


def _estado(store):
    return SimpleNamespace(dataset_store=store)


@pytest.fixture
def store(monkeypatch):
    s = MagicMock()
    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_1", "name": "Lotes"}
    s.ingest_features = AsyncMock(return_value=ref)
    monkeypatch.setattr("geo_copilot.api.dependencies.get_app_state", lambda: _estado(s))
    return s


@pytest.mark.asyncio
async def test_materializa_y_devuelve_el_layer_ref(store):
    out = await q._materializar_resultado(
        "sesion-1", "trae los lotes",
        {"geojson": FC, "intent": "query_data", "sql": "SELECT 1", "layer_name": "Lotes"},
    )
    assert out == {"id": "ds_1", "name": "Lotes"}
    args, kwargs = store.ingest_features.call_args
    assert args[:2] == ("sesion-1", "Lotes") and kwargs["crs"] == "EPSG:4326"
    assert kwargs["provenance"].capability == "core.query_data"
    assert kwargs["provenance"].sql == "SELECT 1"


@pytest.mark.asyncio
@pytest.mark.parametrize("resultado", [
    {}, {"geojson": None}, {"geojson": {"type": "FeatureCollection", "features": []}},
])
async def test_sin_features_no_materializa(store, resultado):
    assert await q._materializar_resultado("s", "q", resultado) is None
    store.ingest_features.assert_not_called()


@pytest.mark.asyncio
async def test_un_fallo_del_workspace_no_rompe_la_respuesta(store):
    store.ingest_features.side_effect = WorkspaceError("cuota")
    assert await q._materializar_resultado("s", "q", {"geojson": FC}) is None
    store.ingest_features.side_effect = RuntimeError("inesperado")
    assert await q._materializar_resultado("s", "q", {"geojson": FC}) is None


@pytest.mark.asyncio
async def test_sin_bd_no_hay_workspace(monkeypatch):
    monkeypatch.setattr("geo_copilot.api.dependencies.get_app_state", lambda: _estado(None))
    assert await q._materializar_resultado("s", "q", {"geojson": FC}) is None
