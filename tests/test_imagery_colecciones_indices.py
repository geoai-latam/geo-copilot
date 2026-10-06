"""F5 (T5.5) — imagery genérico: la colección (Sentinel-2 / Landsat) y el índice (NDVI, NDWI,
NDBI, NDMI) se eligen por petición. Sin red: items STAC con la forma real de Planetary Computer.

Validación real (2026-09-27, Bogotá, fuera de la suite): NDVI Landsat 8 del 11 jun = 0,288,
igual al cálculo independiente con rasterio y el scale/offset publicados en el item.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "imagery_mcp"))

from imagery_mcp import engine
from imagery_mcp.config import Limits
from imagery_mcp.providers import (
    COLECCIONES,
    ColeccionNoDisponible,
    coleccion,
    parse_item,
)
from imagery_mcp.tiles import _SCENE_RE

LS = "https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/x"


def _item_landsat() -> dict:
    banda = {"raster:bands": [{"scale": 2.75e-05, "offset": -0.2, "nodata": 0}]}
    return {
        "id": "LC08_L2SP_008057_20260611_02_T1", "bbox": [-75.0, 3.8, -73.2, 5.6],
        "properties": {"datetime": "2026-06-11T15:10:00Z", "eo:cloud_cover": 8.08},
        "assets": {**{k: {"href": f"{LS}/{k}.TIF", **banda} for k in ("red", "green", "blue", "nir08", "swir16",
                                                                       "swir22")},
                   "qa_pixel": {"href": f"{LS}/qa_pixel.TIF"}},
    }


def test_un_item_landsat_se_normaliza_con_bandas_canonicas_mascara_qa_y_factor_publicado():
    s = parse_item(_item_landsat(), COLECCIONES["landsat-c2-l2"], "planetary-computer")
    assert s.collection == "landsat-c2-l2" and s.mask_kind == "qa_pixel"
    assert s.nir_href.endswith("/nir08.TIF") and s.bands["nir"].endswith("/nir08.TIF")
    assert s.scl_href.endswith("/qa_pixel.TIF")
    f = s.scaling_for("nir")
    assert (f.scale, f.offset, f.source) == (2.75e-05, -0.2, "stac-asset")


def test_sin_factor_publicado_landsat_no_hereda_el_de_sentinel2():
    """El baseline de ESA solo describe Sentinel-2: en Landsat sin factor, identidad y aviso."""
    item = _item_landsat()
    for a in item["assets"].values():
        a.pop("raster:bands", None)
    item["properties"]["s2:processing_baseline"] = "05.11"   # aunque viniera, no aplica
    s = parse_item(item, COLECCIONES["landsat-c2-l2"], "planetary-computer")
    assert s.scaling_for("red").is_identity and s.scaling_for("nir").is_identity


def test_landsat_no_esta_en_earth_search_y_se_dice():
    with pytest.raises(ColeccionNoDisponible, match="Landsat.*planetary-computer"):
        coleccion("landsat-c2-l2", "earth-search")
    with pytest.raises(ColeccionNoDisponible, match="desconocida"):
        coleccion("modis", "planetary-computer")


def test_la_mascara_qa_pixel_marca_nube_cirro_y_sombra_y_no_el_agua():
    # bit 1 nube dilatada, 2 cirro, 3 nube, 4 sombra, 6 despejado, 7 agua
    qa = np.array([1 << 1, 1 << 2, 1 << 3, 1 << 4, 1 << 6, 1 << 7, 0], dtype="uint16")
    assert engine.mascara_de_nubes(qa, "qa_pixel").tolist() == [True, True, True, True, False, False, False]
    assert engine.mascara_de_nubes(np.array([3, 4, 8, 9, 10]), "scl").tolist() == [True, False, True, True, True]


@pytest.mark.parametrize("indice, bandas", [("ndvi", ("nir", "red")), ("ndwi", ("green", "nir")),
                                            ("ndbi", ("swir16", "nir")), ("ndmi", ("nir", "swir16"))])
def test_cada_indice_lee_sus_dos_bandas_y_calcula_a_menos_b_sobre_a_mas_b(monkeypatch, indice, bandas):
    s = parse_item(_item_landsat(), COLECCIONES["landsat-c2-l2"], "planetary-computer")
    s.scl_href = None
    valores = {"nir": 0.4, "red": 0.1, "green": 0.2, "swir16": 0.3}
    leidas = []

    def leer(href, bbox, **kw):
        banda = next(b for b in valores if s.bands[b] == href)
        leidas.append(banda)
        return engine.BandWindow(data=np.full((2, 2), valores[banda], "float32"), transform=None, crs=None,
                                 bounds4326=bbox)

    monkeypatch.setattr(engine, "read_band_window", leer)
    prov = type("P", (), {"sign": staticmethod(lambda h: h)})()
    win = engine._ndvi_window(prov, s, (-74.1, 4.6, -74.0, 4.7), Limits(), indice)
    a, b = valores[bandas[0]], valores[bandas[1]]
    assert sorted(leidas) == sorted(bandas)
    assert np.allclose(win.data, (a - b) / (a + b))


def test_bandas_de_resolucion_distinta_se_alinean_a_la_primera(monkeypatch):
    """Sentinel-2: el SWIR es 20 m y el NIR 10 m; se reamostrea el SWIR a la grilla del NIR."""
    s = parse_item(_item_landsat(), COLECCIONES["landsat-c2-l2"], "planetary-computer")
    s.scl_href = None
    formas = {"swir16": (2, 2), "nir": (4, 4)}

    def leer(href, bbox, **kw):
        banda = next(b for b in formas if s.bands[b] == href)
        return engine.BandWindow(data=np.full(formas[banda], 0.3 if banda == "swir16" else 0.5, "float32"),
                                 transform=None, crs=None, bounds4326=bbox)

    alineadas = []

    def reproyectar(win, like):
        alineadas.append((win.data.shape, like.data.shape))
        return engine.BandWindow(data=np.full(like.data.shape, 0.3, "float32"), transform=None, crs=None,
                                 bounds4326=like.bounds4326)

    monkeypatch.setattr(engine, "read_band_window", leer)
    monkeypatch.setattr(engine, "reproject_like", reproyectar)
    prov = type("P", (), {"sign": staticmethod(lambda h: h)})()
    win = engine._ndvi_window(prov, s, (-74.1, 4.6, -74.0, 4.7), Limits(), "ndmi")
    assert alineadas == [((2, 2), (4, 4))] and win.data.shape == (4, 4)
    assert np.allclose(win.data, (0.5 - 0.3) / (0.5 + 0.3))


def test_indice_desconocido_es_un_error_legible():
    with pytest.raises(engine.ImageryError, match="índice desconocido.*ndvi"):
        engine.indice("evi")


def test_run_ndvi_declara_indice_y_coleccion_y_los_lleva_a_la_tesela(monkeypatch):
    s = parse_item(_item_landsat(), COLECCIONES["landsat-c2-l2"], "planetary-computer")
    pedidos = {}

    class Prov:
        def search(self, *a, **k):
            pedidos.update(k)
            return [s]

        def sign(self, h):
            return h

    win = engine.BandWindow(data=np.array([[0.5, -0.2]], "float32"), transform=None, crs=None,
                            bounds4326=(-74.1, 4.6, -74.0, 4.7))
    monkeypatch.setattr(engine, "_ndvi_window", lambda *a, **k: win)
    aoi = {"type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.0, 4.6], [-74.0, 4.7], [-74.1, 4.7], [-74.1, 4.6]]]}
    out = engine.run_ndvi(Prov(), aoi, None, None, None, Limits(), index="ndwi", collection="landsat-c2-l2")
    assert pedidos == {"collection": "landsat-c2-l2"}
    assert out["index"]["nombre"] == "NDWI" and out["index"]["bandas"] == ["green", "nir"]
    assert "agua" in out["index"]["lectura"]
    assert out["collection"] == {"id": "landsat-c2-l2", "nombre": "Landsat 8/9 C2 L2", "resolucion_m": 30}
    url = out["tiles"]["url_template"]
    assert url.startswith(f"/tiles/{s.id}/") and "&index=ndwi" in url and "&collection=landsat-c2-l2" in url
    assert set(out["reflectance"]) >= {"green", "nir"}  # el factor de LAS bandas del índice


def test_las_teselas_aceptan_escenas_landsat_y_siguen_rechazando_basura():
    assert _SCENE_RE.match("LC08_L2SP_008057_20260611_02_T1") and _SCENE_RE.match("LC09_L2SP_008057_20260721_02_T1")
    assert _SCENE_RE.match("S2B_MSIL2A_20260810T152649_R025_T18NWL_20260810T204206")
    assert not _SCENE_RE.match("../../etc/passwd") and not _SCENE_RE.match("LC07_x_1234")


def test_la_ruta_de_teselas_valida_indice_y_coleccion():
    from imagery_mcp.server import _parse_indice

    assert _parse_indice({"query_string": b"rescale=0,1"}) == ("ndvi", None)
    assert _parse_indice({"query_string": b"rescale=0,1&index=ndbi&collection=landsat-c2-l2"}) == (
        "ndbi", "landsat-c2-l2")
    assert _parse_indice({"query_string": b"index=evi"}) is None
    assert _parse_indice({"query_string": b"collection=modis"}) is None


def test_sin_escenas_el_error_dice_que_coleccion_fechas_y_nubes_se_buscaron():
    """V5: buscando en Landsat decía «No hay escenas Sentinel-2»."""
    class Vacio:
        def search(self, *a, **k):
            return []

        def sign(self, h):
            return h

    aoi = {"type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.0, 4.6], [-74.0, 4.7], [-74.1, 4.7], [-74.1, 4.6]]]}
    with pytest.raises(engine.ImageryError, match=r"Landsat 8/9 C2 L2 entre 2026-06-01 y 2026-06-30 con nubes < 20 %"):
        engine.run_ndvi(Vacio(), aoi, "2026-06-01", "2026-06-30", None, Limits(), collection="landsat-c2-l2")
