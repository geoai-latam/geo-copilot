"""Explorador Sentinel-2, fase 4 (imagery-mcp): ver una escena entera en cualquier producto,
contraste por canal, bandas sueltas, histograma, píxel, descargas y la cuadrícula del mundo.

Sin red: GeoTIFF pequeños escritos con rasterio, parquets de juguete con DuckDB y bboxes reales
de escenas del catálogo (medidas el 2026-10-07) para las huellas MGRS.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "imagery_mcp"))

rasterio = pytest.importorskip("rasterio")

from imagery_mcp.engine import ImageryError
from imagery_mcp.escalado import BandScaling
from imagery_mcp.mgrs import huella
from imagery_mcp.providers import Scene
from imagery_mcp.rutas import _parse_canales, parse_band_tile_path

C1 = BandScaling(scale=0.0001, offset=-0.1)


# --- huellas MGRS -------------------------------------------------------------------------
# bbox de una escena con 100 % de cobertura de su tesela, leída del catálogo.
_REALES = {
    "18NWL": [-75.000172, 4.43425, -74.00908, 5.428216],
    "31UFU": [4.464995, 52.222575, 6.141754, 53.240199],
    "55HBU": [143.583964, -38.015755, 144.861498, -36.998137],
    "19HCC": [-71.176033, -34.425373, -69.97036, -33.420387],
    "60WVT": [174.812105, 64.821448, 177.213528, 65.821395],
    "10SEG": [-123.000216, 36.951489, -121.750657, 37.947581],
    "36RUU": [30.911407, 29.726058, 32.067221, 30.729641],
}


@pytest.mark.parametrize("tile", sorted(_REALES))
def test_la_huella_de_una_tesela_coincide_con_la_escena_que_la_llena(tile):
    xs, ys = zip(*huella(tile)["coordinates"][0], strict=True)
    calculado = [min(xs), min(ys), max(xs), max(ys)]
    assert max(abs(a - b) for a, b in zip(calculado, _REALES[tile], strict=True)) < 0.01


def test_un_id_que_no_es_mgrs_no_tiene_huella():
    assert huella("18NIL") is None and huella("XX") is None and huella("") is None


# --- el mundo -----------------------------------------------------------------------------
@pytest.fixture
def catalogo_mundo(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from imagery_mcp.catalogo import Catalogo

    con = duckdb.connect()
    meses = {
        "2026-07": [("18NWL", 5, 40, 70, 90, 100), ("31UFU", 3, 2, 30, 80, 100)],
        "2026-08": [("18NWL", 6, 1, 60, 95, 100), ("31UFU", 4, 9, 40, 85, 100)],
        "2026-09": [("18NWL", 4, 30, 80, 70, 98)],
    }
    (tmp_path / "months").mkdir()
    for mes, filas in meses.items():
        con.execute("CREATE OR REPLACE TABLE t (mgrs_tile VARCHAR, scene_count USMALLINT, "
                    "min_cloud_cover UTINYINT, median_cloud_cover UTINYINT, mean_cover UTINYINT, max_cover UTINYINT)")
        con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", filas)
        con.execute(f"COPY t TO '{(tmp_path / 'months' / f'{mes}.parquet').as_posix()}' (FORMAT parquet)")
    return Catalogo(base=tmp_path.as_posix(), listar=lambda a: [],
                    base_stats=tmp_path.as_posix(), listar_meses=lambda: list(meses))


def test_el_mundo_agrega_los_meses_que_toca_la_ventana(catalogo_mundo):
    r = catalogo_mundo.mundo("2026-07-15", "2026-08-02")
    assert r["meses"] == ["2026-07", "2026-08"]                  # meses completos, el 09 no
    filas = {f["tile"]: f for f in r["filas"]}
    assert filas["18NWL"] == {"tile": "18NWL", "escenas": 11, "nubes_min": 1, "nubes_mediana": 65,
                              "cobertura_max": 100}


def test_los_filtros_del_mundo_van_por_tesela(catalogo_mundo):
    r = catalogo_mundo.mundo("2026-07-01", "2026-09-30", max_nubes=5, min_escenas=10)
    assert [f["tile"] for f in r["filas"]] == ["18NWL"]          # 31UFU: solo 7 escenas


def test_sin_meses_publicados_es_un_error_honesto(catalogo_mundo):
    from imagery_mcp.catalogo import CatalogoError

    with pytest.raises(CatalogoError, match="no hay agregados"):
        catalogo_mundo.mundo("2020-01-01", "2020-02-01")


def test_el_mundo_sale_como_capa_con_las_mas_despejadas():
    from imagery_mcp import georesult as gr

    out = gr.mundo({"filas": [{"tile": "18NWL", "escenas": 5, "nubes_min": 1, "nubes_mediana": 60, "cobertura_max": 100},
                              {"tile": "nope", "escenas": 1, "nubes_min": 0, "nubes_mediana": 0, "cobertura_max": 1}],
                    "meses": ["2026-08"], "desde": "2026-08-01", "hasta": "2026-08-31"})
    fc = out["artifacts"][0]["data"]
    assert len(fc["features"]) == 1 and fc["features"][0]["properties"]["tile"] == "18NWL"
    assert out["facts"]["sin_huella"] == 1
    assert out["facts"]["mas_despejadas"][0]["tile"] == "nope"


# --- ver una escena -----------------------------------------------------------------------
def _tif(ruta: Path, datos: np.ndarray, *, nodata=0) -> str:
    from rasterio.transform import from_origin

    with rasterio.open(ruta, "w", driver="GTiff", height=datos.shape[0], width=datos.shape[1], count=1,
                       dtype=datos.dtype, crs="EPSG:32618", nodata=nodata,
                       transform=from_origin(500_000, 600_000, 100, 100)) as dst:
        dst.write(datos, 1)
    return str(ruta)


@pytest.fixture
def escena(tmp_path):
    rojo = np.full((64, 64), 1500, "uint16")             # reflectancia 0,05
    nir = np.full((64, 64), 4000, "uint16")              # reflectancia 0,30
    scl = np.full((64, 64), 4, "uint8")                  # vegetación
    scl[:32] = 9                                         # media escena: nube alta
    bandas = {"red": _tif(tmp_path / "red.tif", rojo), "nir": _tif(tmp_path / "nir.tif", nir),
              "scl": _tif(tmp_path / "scl.tif", scl), "cloud": _tif(tmp_path / "cld.tif", np.zeros((64, 64), "uint8")),
              "visual": "x"}
    return Scene(id="S2B_T18NWL_20260810T152745_L2A", datetime="2026-08-10T15:31:43Z", cloud_pct=1.3,
                 bbox=[-75.0, 5.36, -74.9, 5.43], red_href=bandas["red"], nir_href=bandas["nir"],
                 provider="earth-search", scl_href=bandas["scl"], bands=bandas,
                 scaling={"red": C1, "nir": C1})


def _proveedor(escena):
    return MagicMock(get_scene=lambda sid, **_: escena if sid == escena.id else None, sign=lambda h: h)


def test_ver_una_escena_entera_por_su_id(escena):
    from imagery_mcp.vista import ver_escena

    registradas = []
    r = ver_escena(_proveedor(escena), escena.id, "true_color", on_scene=registradas.append)
    assert registradas == [escena]
    assert r["tiles"]["bounds"] == escena.bbox                       # la escena entera, sin AOI
    assert r["tiles"]["url_template"] == f"/tiles-rgb/{escena.id}/true_color/{{z}}/{{x}}/{{y}}.png"
    assert {d["codigo"] for d in r["descargas"]} >= {"B04", "B08", "SCL"}


def test_cada_producto_tiene_su_ruta_y_su_leyenda(escena):
    from imagery_mcp.vista import ver_escena

    p = _proveedor(escena)
    ndwi = ver_escena(p, escena.id, "ndwi")["tiles"]
    assert "index=ndwi" in ndwi["url_template"] and "collection=sentinel-2-c1-l2a" in ndwi["url_template"]
    assert len(ndwi["legend"]["colores"]) == 5
    scl = ver_escena(p, escena.id, "scl")["tiles"]
    assert scl["url_template"].startswith(f"/tiles-band/{escena.id}/scl/")
    assert scl["legend"]["type"] == "clases" and scl["legend"]["clases"][3]["etiqueta"] == "Vegetación"
    nir = ver_escena(p, escena.id, "nir", rescale=[0, 0.6])["tiles"]
    assert nir["url_template"].endswith("?rescale=0,0.6")
    color = ver_escena(p, escena.id, "false_color", estiramiento=[[0, 0.5], [0, 0.3], [0.01, 0.2]])["tiles"]
    assert color["url_template"].endswith("?r=0,0.5&g=0,0.3&b=0.01,0.2")


def test_ver_rechaza_lo_que_no_existe(escena):
    from imagery_mcp.vista import ver_escena

    p = _proveedor(escena)
    with pytest.raises(ImageryError, match="producto desconocido"):
        ver_escena(p, escena.id, "rosa")
    with pytest.raises(ImageryError, match="no trae la banda"):
        ver_escena(p, escena.id, "swir22")
    with pytest.raises(ImageryError, match="no encontrada"):
        ver_escena(p, "S2A_T01AAA_20200101T000000_L2A", "red")
    with pytest.raises(ImageryError, match="estiramiento"):
        ver_escena(p, escena.id, "true_color", estiramiento=[[0.3, 0.1], [0, 1], [0, 1]])


def test_histograma_en_reflectancia_y_clases(escena):
    from imagery_mcp.vista import histograma

    r = histograma(_proveedor(escena), escena.id, ["red", "scl"], lado=32)
    rojo, scl = r["bandas"]
    assert rojo["p2"] == pytest.approx(0.05) and rojo["p98"] == pytest.approx(0.05)
    assert sum(rojo["conteos"]) == 32 * 32 and len(rojo["bordes"]) == 65
    clases = {c["etiqueta"]: c["pct"] for c in scl["clases"]}
    assert clases["Nube, probabilidad alta"] == 50.0 and clases["Vegetación"] == 50.0


def test_el_pixel_trae_cada_banda_su_clase_y_los_indices(escena):
    from imagery_mcp.vista import pixel

    r = pixel(_proveedor(escena), escena.id, -74.99, 5.40)   # dentro del GeoTIFF de juguete
    assert r["bandas"]["red"]["valor"] == pytest.approx(0.05)
    assert r["bandas"]["nir"]["valor"] == pytest.approx(0.30)
    assert r["bandas"]["scl"]["clase"] in ("Vegetación", "Nube, probabilidad alta")
    assert r["indices"]["ndvi"]["valor"] == pytest.approx((0.30 - 0.05) / 0.35, abs=1e-3)
    assert r["bandas"]["cloud"]["valor"] == 0.0                     # nodata 0 dentro de la escena = 0 %
    with pytest.raises(ImageryError, match="fuera de la escena"):
        pixel(_proveedor(escena), escena.id, 0, 0)


# --- rutas y render -----------------------------------------------------------------------
def test_rutas_de_bandas_y_contraste_por_canal():
    assert parse_band_tile_path("/tiles-band/S2X/scl/10/1/2.png") == ("S2X", "scl", 10, 1, 2)
    assert parse_band_tile_path("/tiles-band/S2X/visual/10/1/2.png") is None   # el TCI va por /tiles-rgb
    assert parse_band_tile_path("/tiles-band/S2X/rosa/10/1/2.png") is None
    qs = lambda q: {"query_string": q.encode()}  # noqa: E731
    assert _parse_canales(qs("r=0,0.4&g=0,0.3&b=0.02,0.25")) == ((0, 0.4), (0, 0.3), (0.02, 0.25))
    assert _parse_canales(qs("r=0,0.4&g=0,0.3")) is None
    assert _parse_canales(qs("r=0.5,0.1&g=0,1&b=0,1")) is None
    assert _parse_canales(qs("r=0,9&g=0,1&b=0,1")) is None


def _pool_con(datos_por_banda: dict, escena):
    from imagery_mcp.tiles import _TILESIZE, TilePool

    def _reader(href):
        banda = next(b for b, h in escena.bands.items() if h == href)
        t = MagicMock(data=datos_por_banda[banda][None], mask=np.full((_TILESIZE, _TILESIZE), 255, "uint8"))
        return MagicMock(**{"tile.return_value": t})

    pool = TilePool(MagicMock(sign=lambda h: h))
    pool.register_scene(escena)
    return pool, _reader


def _png(b: bytes) -> np.ndarray:
    from PIL import Image

    return np.array(Image.open(io.BytesIO(b)).convert("RGBA"))


def test_la_scl_se_pinta_con_la_paleta_de_esa(tmp_path, monkeypatch):
    from unittest.mock import patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.tiles import _TILESIZE

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
    clases = np.full((_TILESIZE, _TILESIZE), 6, "float32")       # agua
    clases[:, :8] = 0                                              # sin dato: transparente
    escena = Scene(id="S2A_SCL", datetime="x", cloud_pct=1, bbox=[0, 0, 1, 1], red_href="r", nir_href="n",
                   provider="t", scl_href="mem://scl", bands={"scl": "mem://scl"})
    pool, lector = _pool_con({"scl": clases}, escena)
    with patch("rio_tiler.io.Reader", side_effect=lector):
        im = _png(pool.render_band_tile("S2A_SCL", "scl", 12, 1, 1))
    assert tuple(im[10, 100]) == (0, 0, 255, 255)                  # agua = azul de ESA
    assert im[10, 2, 3] == 0


def test_una_banda_suelta_en_reflectancia_con_su_rango(tmp_path, monkeypatch):
    from unittest.mock import patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.tiles import _TILESIZE

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
    dn = np.full((_TILESIZE, _TILESIZE), 3000, "float32")          # reflectancia 0,2
    escena = Scene(id="S2A_NIR", datetime="x", cloud_pct=1, bbox=[0, 0, 1, 1], red_href="r", nir_href="mem://nir",
                   provider="t", bands={"nir": "mem://nir"}, scaling={"nir": C1})
    pool, lector = _pool_con({"nir": dn}, escena)
    with patch("rio_tiler.io.Reader", side_effect=lector):
        defecto = _png(pool.render_band_tile("S2A_NIR", "nir", 12, 1, 1))
        estirado = _png(pool.render_band_tile("S2A_NIR", "nir", 12, 1, 2, rescale=(0.0, 0.2)))
    assert defecto[0, 0, 0] == int(0.2 / 0.4 * 255)                 # 0–0,4 por defecto
    assert estirado[0, 0, 0] >= 254                                 # 0,2 en un rango 0–0,2: blanco (±1 por coma flotante)


def test_el_color_con_contraste_por_canal_se_compone_de_sus_bandas(tmp_path, monkeypatch):
    from unittest.mock import patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.tiles import _TILESIZE

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
    dn = np.full((_TILESIZE, _TILESIZE), 2000, "float32")          # reflectancia 0,1 en las tres
    bandas = {b: f"mem://{b}" for b in ("red", "green", "blue", "visual")}
    escena = Scene(id="S2A_RGB", datetime="x", cloud_pct=1, bbox=[0, 0, 1, 1], red_href="mem://red",
                   nir_href="n", provider="t", bands=bandas, scaling=dict.fromkeys(("red", "green", "blue"), C1))
    pool, lector = _pool_con({"red": dn, "green": dn, "blue": dn}, escena)
    with patch("rio_tiler.io.Reader", side_effect=lector):
        im = _png(pool.render_rgb_estirado("S2A_RGB", "true_color", 12, 1, 1, ((0, 0.1), (0, 0.2), (0, 0.4))))
    assert np.allclose(im[0, 0, :3], (255, 127, 63), atol=1)      # cada canal con su rango
