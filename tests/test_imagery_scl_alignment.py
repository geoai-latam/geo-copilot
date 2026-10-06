"""IMG-INFO: alineación de la máscara SCL al grid del NDVI.

La SCL nativa es 20 m y el NDVI (red/nir) 10 m. Si la SCL deriva su ventana
del bbox redondeando a su propio grid 20 m, la extensión efectiva difiere del
NDVI (10 m) hasta ~1 píxel → la máscara de nubes queda desplazada y enmascara
píxeles vecinos. ``read_scl_mask`` acepta el georreferenciado del NDVI
(``ref_transform``/``ref_crs``) y lee la SCL sobre EXACTAMENTE la misma
extensión → alineación exacta.

Test determinista con un raster SCL sintético en disco (sin red): la mitad
geográfica derecha es nube; la máscara alineada al grid del NDVI debe marcar
nube en la derecha y cielo despejado en la izquierda, con el borde donde toca
geográficamente (≈ x=100), no desplazado.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "imagery_mcp"))

from imagery_mcp import engine


@pytest.fixture
def _proj_env(monkeypatch):
    """Apunta PROJ/GDAL a los datos empaquetados de rasterio.

    En Windows el PROJ del sistema puede resolver a otra instalación (p.ej. la
    de PostgreSQL), rompiendo la creación de CRS. rasterio trae su propia base;
    resuelve perezosamente al abrir, así que fijar el env aquí basta."""
    import rasterio
    base = os.path.dirname(rasterio.__file__)
    proj = os.path.join(base, "proj_data")
    gdal = os.path.join(base, "gdal_data")
    if not os.path.isfile(os.path.join(proj, "proj.db")):
        pytest.skip("datos PROJ empaquetados de rasterio no disponibles")
    monkeypatch.setenv("PROJ_DATA", proj)
    monkeypatch.setenv("PROJ_LIB", proj)
    monkeypatch.setenv("GDAL_DATA", gdal)


def _write_scl_20m(path: str) -> None:
    """SCL 20 m, EPSG:32618, 10×10 sobre x∈[0,200], y∈[0,200].

    Nube (clase 9) donde el centro del píxel x ≥ 100 (mitad derecha);
    vegetación (clase 4, despejado) a la izquierda."""
    import rasterio
    from rasterio.transform import from_origin

    arr = np.full((10, 10), 4, dtype="uint8")   # 4 = vegetación (no-nube)
    for c in range(10):
        if c * 20 + 10 >= 100:                    # centro del píxel x ≥ 100
            arr[:, c] = 9                          # 9 = nube (en _SCL_CLOUD_CLASSES)
    prof = {
        "driver": "GTiff", "height": 10, "width": 10, "count": 1, "dtype": "uint8",
        "crs": "EPSG:32618", "transform": from_origin(0, 200, 20, 20),
    }
    with rasterio.open(path, "w", **prof) as ds:
        ds.write(arr, 1)


def test_scl_alineada_al_grid_del_ndvi(_proj_env, tmp_path):
    """La máscara alineada marca nube en la mitad derecha geográfica y despejado
    en la izquierda, con el borde cerca de x=100 (no desplazado)."""
    from rasterio.transform import from_origin

    scl_path = str(tmp_path / "scl.tif")
    _write_scl_20m(scl_path)

    # NDVI 10 m sobre x∈[50,150], y∈[50,150] → shape (10,10). Su origen NO cae
    # en el grid 20 m de la SCL: es justo el caso que desalineaba la máscara.
    ndvi_shape = (10, 10)
    ref_transform = from_origin(50, 150, 10, 10)
    ref_crs = "EPSG:32618"

    mask = engine.read_scl_mask(
        scl_path, (-74.0, 4.0, -73.9, 4.1), ndvi_shape,
        ref_transform=ref_transform, ref_crs=ref_crs, retries=1,
    )

    assert mask is not None
    assert mask.shape == ndvi_shape                       # remuestreada al grid del NDVI
    # Alineación geográfica EXACTA: el borde de nube (x=100) cae en la columna 5
    # del grid del NDVI. Las columnas 0-4 tienen su centro en x≤95 (despejado) y
    # las 5-9 en x≥105 (nube) — ambos lejos del límite del píxel 20 m, así que la
    # semántica de nearest es inequívoca. El camino DESALINEADO (ventana derivada
    # del bbox, redondeada al grid 20 m) desplaza el borde a la columna 6 → col 5
    # quedaría despejada: esta aserción rompe si se pierde la alineación.
    assert not mask[:, :5].any(), "columnas 0-4 (cielo despejado, x≤95) no deben enmascararse"
    assert mask[:, 5:].all(), "columnas 5-9 (nube, x≥105) deben enmascararse"


def test_scl_sin_refs_cae_al_bbox_sin_romper(_proj_env, tmp_path):
    """Sin ref_transform/ref_crs, mantiene el camino previo (ventana por bbox) y
    devuelve una máscara del out_shape pedido — la degradación no rompe."""
    from rasterio.warp import transform_bounds

    scl_path = str(tmp_path / "scl.tif")
    _write_scl_20m(scl_path)

    # bbox en 4326 que, proyectado a 32618, cae dentro del raster sintético.
    bbox4326 = transform_bounds(
        "EPSG:32618", "EPSG:4326", 40, 40, 160, 160,
    )
    mask = engine.read_scl_mask(scl_path, bbox4326, (6, 6), retries=1)

    assert mask is not None
    assert mask.shape == (6, 6)
    assert mask.dtype == bool
