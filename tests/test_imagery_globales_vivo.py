"""Capas globales de imagery-mcp contra los datos REALES (marca `integration`: red; `pytest -m integration`).

Verdades independientes (medidas el 2026-10-15):
- radar contra óptico: en el Embalse de Tominé la superficie «lisa» de Sentinel-1 (VV < −18 dB) y el agua
  permanente de WorldCover coinciden (29,4 % y 29,0 %); en el centro de Bogotá, ~0 % los dos;
- WorldPop: la suma de la herramienta contra un cálculo independiente (centros de píxel con shapely):
  Chapinero 2018 = 164 059 los dos (el censo dice 150 699: WorldPop es un modelo);
- cobertura: el centro de Bogotá es mayoría «Construido».
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

rasterio = pytest.importorskip("rasterio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "imagery_mcp"))
from imagery_mcp import capas_globales, radar

pytestmark = pytest.mark.integration
TOMINE = (-73.84, 4.94, -73.80, 4.97)
BOGOTA = (-74.10, 4.60, -74.06, 4.66)


def _caja(b) -> dict:
    x1, y1, x2, y2 = b
    return {"type": "Polygon", "coordinates": [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]]}


def _agua_wc(b) -> float:
    return next((c["porcentaje"] for c in capas_globales.cobertura(_caja(b), b)["clases"] if c["codigo"] == 80), 0.0)


def test_radar_y_optico_coinciden_en_el_agua_de_un_embalse():
    r = radar.sar(_caja(TOMINE), TOMINE, "2026-09-01", "2026-10-15", -18.0)
    assert r["superficie_lisa_pct"] == pytest.approx(_agua_wc(TOMINE), abs=8)


def test_la_ciudad_no_es_agua_para_ninguno_y_es_mayoria_construida():
    r = radar.sar(_caja(BOGOTA), BOGOTA, "2026-09-01", "2026-10-15", -18.0)
    assert r["superficie_lisa_pct"] < 3 and r["vv"]["media_db"] > -10
    clases = capas_globales.cobertura(_caja(BOGOTA), BOGOTA)["clases"]
    assert clases[0]["codigo"] == 50 and clases[0]["porcentaje"] > 60


def test_la_suma_de_worldpop_coincide_con_un_calculo_independiente():
    import numpy as np
    import shapely
    from rasterio.windows import from_bounds
    from shapely.geometry import box

    area = box(-74.07, 4.63, -74.05, 4.66)
    hechos = capas_globales.poblacion(area.__geo_interface__, area.bounds, 2018)
    with rasterio.open(capas_globales._archivo_worldpop(2018, "COL")) as ds:
        w = from_bounds(*area.bounds, ds.transform).round_offsets().round_lengths()
        a = ds.read(1, window=w).astype(float)
        t, nd = ds.window_transform(w), ds.nodata
    a[a == nd] = 0
    cols, rows = np.meshgrid(np.arange(a.shape[1]), np.arange(a.shape[0]))
    dentro = shapely.contains_xy(area, t.c + (cols + 0.5) * t.a, t.f + (rows + 0.5) * t.e)
    assert hechos["personas"] == round(float(a[dentro].sum()))
