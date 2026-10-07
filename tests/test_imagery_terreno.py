"""Relieve e hidrología de imagery-mcp (Copernicus DEM) sobre DEM sintéticos: sin red."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "imagery_mcp"))

pytest.importorskip("rasterio")

from imagery_mcp import hidrologia, terreno
from imagery_mcp.tools_terreno import _rango_elevacion


def test_celdas_con_el_nombre_de_copernicus():
    assert terreno.celda_url(4, -75).endswith("Copernicus_DSM_COG_10_N04_00_W075_00_DEM.tif")
    assert terreno.celda_url(-1, 10).endswith("Copernicus_DSM_COG_10_S01_00_E010_00_DEM.tif")
    # Bogotá cae en una celda; una zona que cruza el meridiano 74 en dos
    assert len(terreno.celdas((-74.3, 4.5, -74.1, 4.8))) == 1
    assert len(terreno.celdas((-74.3, 4.5, -73.9, 4.8))) == 2


def test_tamano_de_pixel_en_el_suelo_se_encoge_con_la_latitud():
    assert terreno.tamano_pixel_m(10, 0) == pytest.approx(152.87, rel=1e-3)
    assert terreno.tamano_pixel_m(10, 60) == pytest.approx(152.87 / 2, rel=1e-3)


def _plano(pendiente_grados: float, hacia: str, n: int = 20, dx: float = 30.0) -> np.ndarray:
    """Un plano que BAJA hacia `hacia` (filas de norte a sur)."""
    t = math.tan(math.radians(pendiente_grados)) * dx
    filas, cols = np.indices((n, n)).astype(float)
    return {"este": -cols * t, "sur": -filas * t, "norte": filas * t}[hacia] + 1000


def test_pendiente_y_orientacion_de_un_plano():
    dem = _plano(30, "este")
    assert np.allclose(terreno.pendiente(dem, 30.0), 30.0)
    assert np.allclose(terreno.orientacion(dem, 30.0), 90.0)      # mira al este
    assert np.allclose(terreno.orientacion(_plano(10, "sur"), 30.0), 180.0)
    assert np.allclose(terreno.orientacion(_plano(10, "norte"), 30.0) % 360, 0.0)


def test_sombreado_ilumina_lo_que_mira_al_noroeste():
    al_no = terreno.sombreado(_plano(30, "norte"), 30.0).mean()
    al_se = terreno.sombreado(_plano(30, "sur"), 30.0).mean()
    assert 0 <= al_se < al_no <= 255


def test_rellenar_llena_un_hoyo_y_deja_salida():
    dem = np.full((7, 7), 10.0)
    dem[3, 3] = 0.0                       # un hoyo en medio de un llano
    lleno = hidrologia.rellenar(dem)
    assert lleno[3, 3] > 10.0             # se llenó por encima del borde
    rec = hidrologia.direcciones(lleno, 30.0, 30.0)
    # todo píxel interior tiene a dónde verter
    interiores = [r * 7 + c for r in range(1, 6) for c in range(1, 6)]
    assert all(rec[i] != i for i in interiores)


def _valle(n: int = 41) -> np.ndarray:
    """Un valle en V que baja hacia el sur por la columna central."""
    filas, cols = np.indices((n, n)).astype(float)
    return 2000 - filas * 5 + np.abs(cols - n // 2) * 20


def test_la_cuenca_de_la_salida_del_valle_es_todo_el_valle():
    dem = _valle()
    lleno = hidrologia.rellenar(dem)
    rec = hidrologia.direcciones(lleno, 30.0, 30.0)
    acc = hidrologia.acumulacion(lleno, rec)
    n = dem.shape[0]
    salida = (n - 1) * n + n // 2
    assert acc.ravel()[salida] == acc.max()                 # el cauce acumula todo lo que baja
    mascara = hidrologia.cuenca(rec, salida)
    assert mascara.sum() > 0.8 * n * n                      # casi todo el valle drena por ahí
    # un punto a mitad de cauce drena solo lo que tiene encima
    mitad = (n // 2) * n + n // 2
    assert hidrologia.cuenca(rec, mitad).sum() < mascara.sum()


def test_el_punto_se_ajusta_al_cauce_cercano():
    acc = np.ones((11, 11))
    acc[:, 5] = 100                         # el cauce va por la columna 5
    assert hidrologia._ajustar(acc, 4, 3, radio_px=3)[1] == 5


def test_red_de_drenaje_como_lineas():
    from rasterio.transform import from_origin

    dem = _valle()
    lleno = hidrologia.rellenar(dem)
    rec = hidrologia.direcciones(lleno, 30.0, 30.0)
    acc = hidrologia.acumulacion(lleno, rec)
    tramos = hidrologia._red(acc, rec, 20, np.ones_like(dem, bool), from_origin(-74, 5, 0.0003, 0.0003), 0.0009)
    assert tramos and all(t["geometry"]["type"] == "LineString" for t in tramos)
    assert all(t["properties"]["area_drenada_km2"] > 0 for t in tramos)


def test_radio_fuera_de_rango_es_error_honesto():
    from imagery_mcp.engine import ImageryError

    with pytest.raises(ImageryError):
        hidrologia.cuenca_de({"type": "Point", "coordinates": [-74, 4.6]}, radio_km=100)
    with pytest.raises(ImageryError):      # una línea de 200 km no cabe en la ventana
        hidrologia.cuenca_de({"type": "LineString", "coordinates": [[-75, 4], [-73.2, 4.5]]})
    with pytest.raises(ImageryError):
        hidrologia.cuenca_de({"type": "FeatureCollection", "features": []})


def test_ruta_de_teselas_del_relieve():
    assert terreno.parse_ruta("/tiles-dem/pendiente/12/1200/1900.png") == ("pendiente", 12, 1200, 1900)
    assert terreno.parse_ruta("/tiles-dem/volcan/12/1/1.png") is None
    assert terreno.parse_ruta("/tiles-dem/elevacion/3/99/1.png") is None      # fuera de la malla
    assert terreno.url_teselas("elevacion", (2500, 3300)).endswith("?rescale=2500,3300")


def test_rampa_de_elevacion_se_ajusta_a_la_zona():
    assert _rango_elevacion({"elevacion_m": {"p5": 2541.3, "p95": 3308.8}}) == (2540, 3310)
    lo, hi = _rango_elevacion({"elevacion_m": {"p5": 3.0, "p95": 5.0}})       # costa plana
    assert hi - lo >= 20


def test_bajo_el_zoom_minimo_no_se_lee_nada():
    from imagery_mcp.tiles_render import _TRANSPARENT_PNG

    assert terreno.TerrenoTiles().render("elevacion", terreno.MINZOOM - 1, 0, 0) == _TRANSPARENT_PNG


def test_las_tools_del_relieve_tienen_scope():
    from imagery_mcp.auth import SCOPE_COMPUTE, TOOL_SCOPES

    assert TOOL_SCOPES["imagery_terrain"] == SCOPE_COMPUTE
    assert TOOL_SCOPES["imagery_watershed"] == SCOPE_COMPUTE


def test_el_sombreado_es_una_sombra_translucida():
    import io

    from PIL import Image

    luz = np.array([[250.0, 180.0], [40.0, 40.0]])
    fuera = np.array([[False, False], [False, True]])
    img = Image.open(io.BytesIO(terreno._sombra_translucida(luz, fuera)))
    assert img.mode == "RGBA"
    alfa = np.asarray(img)[..., 3]
    assert alfa[0, 0] == 0                      # ladera al sol: transparente
    assert 0 < alfa[0, 1] < alfa[1, 0]          # cuanto más oscura, más sombra
    assert alfa[1, 1] == 0                      # fuera de datos: nada
    assert terreno.leyenda("sombreado") is None


def test_geometrias_de_lo_que_llegue():
    linea = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": linea, "properties": {}}]}
    assert hidrologia._geometrias(fc) == [linea]
    assert hidrologia._geometrias({"type": "Feature", "geometry": linea}) == [linea]
    assert hidrologia._geometrias(linea) == [linea]


def test_cuenca_de_una_linea_sale_por_su_punto_aguas_abajo(monkeypatch):
    """El valle sintético como DEM: la línea del cauce entero da la cuenca de su extremo bajo."""
    from rasterio.transform import from_origin

    dem = _valle()
    n = dem.shape[0]
    res = 0.0003
    transform = from_origin(-74.0, 5.0, res, res)
    monkeypatch.setattr(hidrologia, "leer_zona", lambda bbox, max_px=1500: (dem, transform, (30.0, 30.0)))
    x = -74.0 + (n // 2 + 0.5) * res
    rio = {"type": "LineString", "coordinates": [[x, 5.0 - 2 * res], [x, 5.0 - (n - 2) * res]]}
    r = hidrologia.cuenca_de(rio, radio_km=1)
    assert r["hechos"]["señalado"] == "linea"
    assert r["hechos"]["salida"][1] < 5.0 - (n - 4) * res          # salió por abajo (el sur)
    tipos = [f["properties"]["tipo"] for f in r["geojson"]["features"]]
    assert tipos[0] == "cuenca" and tipos[-1] == "salida" and "cauce" in tipos
