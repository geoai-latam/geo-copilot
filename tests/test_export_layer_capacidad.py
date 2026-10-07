"""`export_layer`: el agente prepara la descarga y devuelve el enlace que va en la respuesta."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geo_copilot.orchestrator import capabilities_salidas as cs


@pytest.fixture
def entorno(monkeypatch):
    ref = SimpleNamespace(name="Cauces", feature_count=392, geometry_type="MultiLineString")

    class Store:
        async def get(self, sesion, ds):
            return ref if ds == "ds_0123456789abcdef" else None

    async def resolver(valor, working):
        return "ds_0123456789abcdef" if valor == "activa" else None

    monkeypatch.setattr(cs, "store_actual", lambda: Store())
    monkeypatch.setattr(cs._ce(), "_resolver", resolver)
    return ref


async def _correr(args):
    return await cs._exportar(None, {"session_id": "s1"}, args)


async def test_devuelve_el_enlace_exacto_con_formato_y_crs(entorno):
    r = await _correr({"dataset": "activa", "format": "shp", "crs": "EPSG:9377"})
    assert r.success
    assert "[[descarga:ds_0123456789abcdef?formato=shp&crs=EPSG:9377|Descargar «Cauces» (Shapefile (zip))]]" in r.observation
    assert "10 caracteres" in r.observation        # el aviso del Shapefile va en los hechos


async def test_kml_va_sin_crs_y_crs_malo_es_error(entorno):
    r = await _correr({"dataset": "activa", "format": "kml"})
    assert r.success and "formato=kml|" in r.observation
    r = await _correr({"dataset": "activa", "format": "gpkg", "crs": "EPSG:999999"})
    assert not r.success and "desconocido" in r.observation


async def test_capa_vacia_o_inexistente(entorno):
    entorno.feature_count = 0
    assert not (await _correr({"dataset": "activa", "format": "gpkg"})).success
    assert not (await _correr({"dataset": "otra", "format": "gpkg"})).success
    assert not (await _correr({"dataset": "activa", "format": "xlsx"})).success


def test_esta_registrada():
    from geo_copilot.orchestrator.capabilities_core import ensure_core
    from geo_copilot.platform.capabilities import registry

    ensure_core()
    cap = registry().get("export_layer")
    assert cap is not None and cap.risk == "read"
    assert set(cap.parameters["properties"]["format"]["enum"]) == {"gpkg", "shp", "kml", "geojson", "csv", "dxf"}
