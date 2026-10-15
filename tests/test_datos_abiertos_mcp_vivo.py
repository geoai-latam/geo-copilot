"""datos-abiertos-mcp contra los portales REALES (marca `integration`: red; `pytest -m integration`).

La verdad se mide con consultas DIRECTAS e independientes al portal (SODA `count(*)`, la descarga del recurso),
no con la herramienta. Medido el 2026-10-15: Envigado g8hy-gwg8 = 82 instituciones, 70 en
[-75.62, 6.14, -75.55, 6.20]; colegios privados de Bogotá (CKAN) = 1 143, en EPSG:3857.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "datos_abiertos_mcp"))
from datos_abiertos_mcp import server as srv

pytestmark = pytest.mark.integration
ENVIGADO = "g8hy-gwg8"
BBOX = [-75.62, 6.14, -75.55, 6.20]


@pytest.fixture(autouse=True)
def _dir(monkeypatch, tmp_path):
    monkeypatch.setattr(srv.resultados, "directorio", str(tmp_path))


def _contar(where: str | None = None) -> int:
    params = {"$select": "count(*)", **({"$where": where} if where else {})}
    filas = httpx.get(f"https://www.datos.gov.co/resource/{ENVIGADO}.json", params=params, timeout=60).json()
    return int(filas[0]["count"])


def test_consulta_con_zona_trae_lo_que_cuenta_el_portal():
    verdad = _contar(f"within_box(coordenadas, {BBOX[3]}, {BBOX[0]}, {BBOX[1]}, {BBOX[2]})")
    sc = srv.datos_consultar(ENVIGADO, columna_geo="coordenadas", bbox=BBOX).structuredContent
    feats = sc["artifacts"][0]["data"]["features"]
    assert sc["facts"]["cumplen_en_el_portal"] == verdad == len(feats) and sc["facts"]["completo"] is True
    for f in feats:  # lon/lat de verdad, dentro del rectángulo pedido
        x, y = f["geometry"]["coordinates"]
        assert BBOX[0] <= x <= BBOX[2] and BBOX[1] <= y <= BBOX[3]


def test_describir_cuenta_las_filas_del_portal():
    assert srv.datos_describir(ENVIGADO).structuredContent["facts"]["filas"] == _contar()


def test_recurso_ckan_en_3857_se_declara_y_trae_todo():
    url = ("https://datosabiertos.bogota.gov.co/dataset/185ee4fc-af30-4342-9b52-370ec7ff1209/resource/"
           "badb0c92-20c8-4918-84f3-e61b06418062/download/colegios_privados_122024.geojson")
    verdad = len(httpx.get(url, timeout=120, follow_redirects=True).json()["features"])
    sc = srv.datos_traer_recurso(url).structuredContent
    assert sc["facts"]["elementos"] == verdad and sc["facts"]["crs_declarado"] == "EPSG:3857"
    assert sc["artifacts"][0]["crs"] == "EPSG:3857"
