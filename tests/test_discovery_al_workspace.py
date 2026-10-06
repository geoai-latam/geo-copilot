"""V5 (otra temática): la capa cargada desde el catálogo queda en el workspace de la sesión.

Sin su `ds_…` las herramientas exactas (ws_*) no podían operar sobre ella («¿cuántos
equipamientos hay a menos de 5 km?» agotó los pasos) y no volvía al recargar.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.api.routes import discovery

FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.5, 4.9]}, "properties": {"NOMBRE": "A"}}]}


def _ref(n: int = 1):
    datos = {"id": "ds_aaaaaaaaaaaaaaaa", "feature_count": n, "geometry_type": "Point", "bbox": [-75, 4, -74, 5],
             "storage": {"geometry_column": "geom"}, "fields": [{"name": "nombre"}]}
    return SimpleNamespace(id=datos["id"], model_dump=lambda mode=None: dict(datos))


@pytest.fixture
def store(monkeypatch):
    st = SimpleNamespace(ingest_features=AsyncMock(return_value=_ref()),
                         to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": [
                             {"type": "Feature", "geometry": None, "properties": {"nombre": "A"}}]}))
    monkeypatch.setattr("geo_copilot.api.dependencies.get_app_state", lambda: SimpleNamespace(dataset_store=st))
    return st


@pytest.mark.asyncio
async def test_la_capa_cargada_queda_como_dataset_de_la_sesion(store):
    cm = MagicMock()
    cm.get_session.return_value = object()
    ref, tiles, fc, _ = await discovery._al_workspace("sess-1", cm, "Equipamiento", "https://x/FeatureServer/0", FC)
    assert ref["id"] == "ds_aaaaaaaaaaaaaaaa" and tiles is None
    # el mapa dibuja la versión del workspace (campos normalizados: NOMBRE → nombre)
    assert fc["features"][0]["properties"] == {"nombre": "A"}
    args, kw = store.ingest_features.call_args
    assert args[:2] == ("sess-1", "Equipamiento") and kw["crs"] == "EPSG:4326"
    assert kw["provenance"].capability == "discovery.load"


@pytest.mark.asyncio
async def test_sin_sesion_existente_o_sin_features_no_materializa(store):
    cm = MagicMock()
    cm.get_session.return_value = None
    assert await discovery._al_workspace("sess-x", cm, "E", "u", FC) is None
    cm.get_session.return_value = object()
    assert await discovery._al_workspace(None, cm, "E", "u", FC) is None
    assert await discovery._al_workspace("s", cm, "E", "u", {"type": "FeatureCollection", "features": []}) is None
    store.ingest_features.assert_not_called()


@pytest.mark.asyncio
async def test_un_fallo_del_workspace_no_rompe_la_carga(store):
    store.ingest_features.side_effect = RuntimeError("sin postgis")
    cm = MagicMock()
    cm.get_session.return_value = object()
    assert await discovery._al_workspace("sess-1", cm, "E", "u", FC) is None


@pytest.mark.asyncio
async def test_la_procedencia_dice_cuantos_se_cargaron_y_cuantos_tiene_el_servicio(store):
    """Pendiente del acta FH: la carga trae como máximo 2000; el agente no lo sabía."""
    cm = MagicMock()
    cm.get_session.return_value = object()
    await discovery._al_workspace("sess-1", cm, "Sedes", "https://x/FeatureServer/0", FC, total_en_servicio=5311)
    args = store.ingest_features.call_args.kwargs["provenance"].arguments
    assert args["elementos_cargados"] == 1 and args["total_en_servicio"] == 5311


@pytest.mark.asyncio
async def test_una_capa_grande_va_por_teselas_y_el_estilo_se_disena_sobre_una_muestra(store):
    """El panel cargaba 2000 elementos (una muestra) porque devolvía la capa entera inline. Ahora la
    capa completa queda en el workspace y el navegador la pide por teselas, como desde el chat."""
    from geo_copilot.core.config import get_settings

    tope = get_settings().workspace_inline_max_features
    store.ingest_features.return_value = _ref(tope + 1)
    cm = MagicMock()
    cm.get_session.return_value = object()
    ref, tiles, inline, muestra = await discovery._al_workspace("sess-1", cm, "Malla vial", "https://x/FeatureServer/0", FC)
    assert inline is None and tiles["url"] == "/api/v1/tiles/ws/sess-1/ds_aaaaaaaaaaaaaaaa/{z}/{x}/{y}.pbf"
    assert tiles["feature_count"] == tope + 1 and muestra["features"]
    assert store.to_geojson.call_args.kwargs == {"limit": tope, "muestra": True}
