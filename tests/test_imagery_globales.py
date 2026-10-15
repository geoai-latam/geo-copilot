"""imagery-mcp: capas globales — cobertura (WorldCover), población (WorldPop) y radar (Sentinel-1).

Rásteres sintéticos escritos en local (sin red). Las verdades contra los datos reales están en
tests/test_imagery_globales_vivo.py (Tominé: radar «liso» 29,4 % contra agua WorldCover 29,0 %; Chapinero
WorldPop 2018 164 059 por la herramienta y por un cálculo independiente píxel a píxel).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "imagery_mcp"))
from imagery_mcp import auth, capas_globales, radar, tools_globales
from imagery_mcp.engine import ImageryError


def _tif(ruta: Path, datos: np.ndarray, bounds, crs="EPSG:4326", nodata=None) -> str:
    from rasterio.transform import from_bounds

    t = from_bounds(*bounds, datos.shape[1], datos.shape[0])
    with rasterio.open(ruta, "w", driver="GTiff", height=datos.shape[0], width=datos.shape[1], count=1,
                       dtype=datos.dtype, crs=crs, transform=t, nodata=nodata) as ds:
        ds.write(datos, 1)
    return str(ruta)


def _caja(x1, y1, x2, y2) -> dict:
    return {"type": "Polygon", "coordinates": [[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]]}


def test_celdas_de_worldcover_siguen_la_rejilla_de_3_grados():
    (u,) = capas_globales._celdas_worldcover((-74.1, 4.6, -74.0, 4.7))
    assert u.endswith("ESA_WorldCover_10m_2021_v200_N03W075_Map.tif")  # la celda real medida
    assert len(capas_globales._celdas_worldcover((-75.5, 2.5, -74.5, 3.5))) == 4  # cruza dos líneas de 3°


def test_cobertura_da_area_y_porcentaje_por_clase(tmp_path, monkeypatch):
    datos = np.full((100, 100), 50, dtype="uint8")
    datos[:, :25] = 80   # un cuarto de agua
    datos[:10, :] = 0    # sin dato: no cuenta
    ruta = _tif(tmp_path / "wc.tif", datos, (-74.1, 4.6, -74.0, 4.7))
    monkeypatch.setattr(capas_globales, "_celdas_worldcover", lambda bbox: [ruta])
    h = capas_globales.cobertura(_caja(-74.1, 4.6, -74.0, 4.7), (-74.1, 4.6, -74.0, 4.7))
    clases = {c["codigo"]: c for c in h["clases"]}
    assert set(clases) == {50, 80} and clases[80]["porcentaje"] == pytest.approx(25, abs=0.5)
    assert h["area_km2"] == pytest.approx(0.1 * 111.32 * np.cos(np.radians(4.65)) * 0.09 * 110.574, rel=0.02)
    with pytest.raises(ImageryError, match="2° de lado"):
        capas_globales.cobertura(_caja(-76, 3, -73, 6), (-76, 3, -73, 6))


def test_poblacion_suma_por_centro_de_pixel_sin_nodata(tmp_path, monkeypatch):
    datos = np.full((10, 10), 2.0, dtype="float32")
    datos[0, 0] = -99999.0
    ruta = _tif(tmp_path / "wp.tif", datos, (-74.1, 4.6, -74.0, 4.7), nodata=-99999.0)
    monkeypatch.setattr(capas_globales, "_archivo_worldpop", lambda anio, iso3: ruta)
    h = capas_globales.poblacion(_caja(-74.1, 4.6, -74.05, 4.7), (-74.1, 4.6, -74.05, 4.7), 2025)
    assert h["personas"] == 98  # 5 columnas × 10 filas × 2, menos la celda nodata
    assert "PROYECCIÓN" in h["nota"] and "ESTIMACIÓN" in h["nota"]
    with pytest.raises(ImageryError, match="2015–2030"):
        capas_globales.poblacion(_caja(0, 0, 1, 1), (0, 0, 1, 1), 2040)


def test_radar_agua_bajo_el_umbral_y_hechos_de_la_escena(tmp_path, monkeypatch):
    vv = np.full((50, 50), 0.3, dtype="float32")   # −5,2 dB: ciudad/campo
    vv[:, :20] = 0.005                             # −23 dB: agua
    ruta = _tif(tmp_path / "vv.tif", vv, (-74.1, 4.6, -74.0, 4.7))
    monkeypatch.setattr(radar, "escenas", lambda bbox, d, h: [
        {"id": "S1A_x", "fecha": "2026-10-13T23:13:15", "orbita": "descending", "plataforma": "sentinel-1a",
         "vv": ruta, "vh": None}, {"id": "S1A_y", "fecha": "2026-10-01T23:13:15", "orbita": "x", "plataforma": "y",
                                   "vv": ruta, "vh": None}])
    monkeypatch.setattr(radar, "_firmar", lambda href: href)
    h = radar.sar(_caja(-74.1, 4.6, -74.0, 4.7), (-74.1, 4.6, -74.0, 4.7), "2026-09-01", "2026-10-15", -18.0)
    assert h["superficie_lisa_pct"] == pytest.approx(40, abs=1) and h["umbral_agua_db"] == -18.0
    assert h["vv"]["p10_db"] < -18 < h["vv"]["p90_db"] and h["otras_fechas"] == ["2026-10-01"]
    assert "criterio" in h["nota"]  # el umbral se dice como criterio, no como dato


def test_radar_sin_escenas_es_error_honesto(monkeypatch):
    monkeypatch.setattr(radar, "escenas", lambda bbox, d, h: [])
    with pytest.raises(ImageryError, match="no hay escenas Sentinel-1"):
        radar.sar(_caja(-74.1, 4.6, -74.0, 4.7), (-74.1, 4.6, -74.0, 4.7), "2026-09-01", "2026-10-15", -18.0)


def test_un_punto_mide_su_entorno_y_las_tools_tienen_scope():
    b = tools_globales._bbox({"type": "Point", "coordinates": [-74.05, 4.65]})
    assert b == pytest.approx((-74.055, 4.645, -74.045, 4.655))
    assert {"imagery_land_cover", "imagery_population", "imagery_sar"} <= set(auth.TOOL_SCOPES)
