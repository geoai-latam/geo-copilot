"""Terreno: el modelo digital de superficie Copernicus GLO-30 (30 m, todo el planeta) y lo que se
deriva de él — elevación, pendiente, sombreado y orientación — como teselas y como hechos de una
zona.

El DEM son COG públicos de 1°×1° en S3 (``copernicus-dem-30m``, sin firma). El bucket no da CORS,
así que no se pinta en el navegador: lo teselan estas rutas con rio-tiler, igual que las escenas.
Una celda que no existe (mar abierto) se salta: no hay tierra que medir.

Las derivadas se calculan sobre la tesela con un píxel de borde (la pendiente de un píxel necesita
a sus vecinos) y con el tamaño de píxel REAL en el suelo a esa latitud (Web Mercator estira hacia
los polos): una ladera mide lo mismo a zoom 9 que a 13.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from typing import Any

import numpy as np

from imagery_mcp.engine import _GDAL_ENV, ImageryError

_CELDA = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
          "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
_TILESIZE = 256
_CIRCUNFERENCIA = 40075016.686
#: Por debajo de este zoom una tesela toca demasiadas celdas de 1°: no se pinta.
MINZOOM = 8
#: Lado máximo (px) del DEM que se lee para medir una zona; a 30 m son ~45 km.
MAX_PX = 1500

#: producto → (nombre, unidad, rango por defecto, rampa de rio-tiler)
PRODUCTOS: dict[str, tuple[str, str, tuple[float, float], str | None]] = {
    "elevacion": ("Elevación", "m", (0.0, 4000.0), "terrain"),
    "pendiente": ("Pendiente", "°", (0.0, 45.0), "ylorrd"),
    "sombreado": ("Sombreado del relieve", "", (0.0, 255.0), None),
    "orientacion": ("Orientación de la ladera", "° desde el norte", (0.0, 360.0), "hsv"),
}


def celda_url(lat: int, lon: int) -> str:
    """URL de la celda de 1° cuya esquina SO es (lat, lon)."""
    return _CELDA.format(ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon))


def celdas(bbox: tuple[float, float, float, float]) -> list[str]:
    minx, miny, maxx, maxy = bbox
    eps = 1e-9
    return [celda_url(la, lo)
            for la in range(math.floor(miny), math.floor(maxy - eps) + 1)
            for lo in range(math.floor(minx), math.floor(maxx - eps) + 1)]


def tamano_pixel_m(z: int, lat: float) -> float:
    """Lado en el suelo (m) de un píxel de tesela a zoom `z` y latitud `lat`."""
    return _CIRCUNFERENCIA * math.cos(math.radians(lat)) / (_TILESIZE * 2 ** z)


# ── derivadas ─────────────────────────────────────────────────────────────────────────────────


def _gradientes(dem: np.ndarray, dx: float, dy: float) -> tuple[np.ndarray, np.ndarray]:
    """dz/dx (hacia el este) y dz/dy (hacia el norte), con filas de norte a sur."""
    gy, gx = np.gradient(dem.astype("float64"), dy, dx)
    return gx, -gy


def pendiente(dem: np.ndarray, dx: float, dy: float | None = None) -> np.ndarray:
    gx, gy = _gradientes(dem, dx, dy or dx)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def orientacion(dem: np.ndarray, dx: float, dy: float | None = None) -> np.ndarray:
    """Hacia dónde mira la ladera, en grados desde el norte (0 = norte, 90 = este)."""
    gx, gy = _gradientes(dem, dx, dy or dx)
    return (np.degrees(np.arctan2(-gx, -gy)) + 360.0) % 360.0


def sombreado(dem: np.ndarray, dx: float, dy: float | None = None, azimut: float = 315.0,
              altura: float = 45.0) -> np.ndarray:
    """Relieve iluminado desde el NO a 45° (lo habitual en cartografía): 0–255."""
    gx, gy = _gradientes(dem, dx, dy or dx)
    pend = np.arctan(np.hypot(gx, gy))
    asp = np.arctan2(-gx, -gy)
    az, alt = math.radians(azimut), math.radians(altura)
    luz = math.sin(alt) * np.cos(pend) + math.cos(alt) * np.sin(pend) * np.cos(az - asp)
    return np.clip(luz, 0, 1) * 255.0


def derivada(producto: str, dem: np.ndarray, dx: float, dy: float | None = None) -> np.ndarray:
    if producto == "elevacion":
        return dem.astype("float64")
    if producto == "pendiente":
        return pendiente(dem, dx, dy)
    if producto == "sombreado":
        return sombreado(dem, dx, dy)
    if producto == "orientacion":
        return orientacion(dem, dx, dy)
    raise ImageryError(f"producto de terreno desconocido: {producto!r}; válidos: {', '.join(PRODUCTOS)}")


# ── lectura ───────────────────────────────────────────────────────────────────────────────────


def leer_zona(bbox: tuple[float, float, float, float], max_px: int = MAX_PX):
    """El DEM de una zona en EPSG:4326 (bilineal, a ≤30 m o lo que quepa en `max_px`):
    (array float32 con NaN fuera de tierra, transform, (dx_m, dy_m))."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.errors import RasterioIOError
    from rasterio.merge import merge

    minx, miny, maxx, maxy = bbox
    res = max(1 / 3600, (maxx - minx) / max_px, (maxy - miny) / max_px)
    with rasterio.Env(**_GDAL_ENV):
        fuentes = []
        try:
            for url in celdas(bbox):
                try:
                    fuentes.append(rasterio.open(url))
                except RasterioIOError:
                    continue   # celda sin tierra (mar abierto)
            if not fuentes:
                raise ImageryError("no hay modelo de elevación en esa zona (¿mar abierto?)")
            arr, transform = merge(fuentes, bounds=bbox, res=res, nodata=np.nan, dtype="float32",
                                   resampling=Resampling.bilinear)
        finally:
            for f in fuentes:
                f.close()
    lat = (miny + maxy) / 2
    dx = res * 111_320.0 * math.cos(math.radians(lat))
    dy = res * 110_574.0
    return arr[0], transform, (dx, dy)


def mascara_aoi(aoi_geojson: dict, forma: tuple[int, int], transform) -> np.ndarray:
    """True dentro del área (si es un polígono); un punto o una línea miden su recuadro."""
    from rasterio.features import geometry_mask

    geoms = []
    if aoi_geojson.get("type") == "FeatureCollection":
        geoms = [f.get("geometry") for f in aoi_geojson.get("features") or []]
    elif aoi_geojson.get("type") == "Feature":
        geoms = [aoi_geojson.get("geometry")]
    else:
        geoms = [aoi_geojson]
    poligonos = [g for g in geoms if g and g.get("type") in ("Polygon", "MultiPolygon")]
    if not poligonos:
        return np.ones(forma, dtype=bool)
    return geometry_mask(poligonos, out_shape=forma, transform=transform, invert=True, all_touched=True)


def _pct(v: np.ndarray, q: float) -> float:
    return round(float(np.nanpercentile(v, q)), 1)


def hechos_zona(aoi_geojson: dict, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """Los números del relieve de una zona: elevación, pendiente y orientación dominante."""
    dem, transform, (dx, dy) = leer_zona(bbox)
    dentro = mascara_aoi(aoi_geojson, dem.shape, transform) & np.isfinite(dem)
    if not dentro.any():
        raise ImageryError("la zona no tiene datos de elevación (¿mar abierto?)")
    pend = pendiente(np.where(np.isfinite(dem), dem, np.nanmean(dem)), dx, dy)
    orient = orientacion(np.where(np.isfinite(dem), dem, np.nanmean(dem)), dx, dy)
    e, p = dem[dentro], pend[dentro]
    sectores = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    idx = (((orient[dentro & (pend > 2)] + 22.5) % 360) // 45).astype(int)
    conteo = np.bincount(idx, minlength=8) if idx.size else np.zeros(8, int)
    return {
        "fuente": "Copernicus DEM GLO-30 (modelo de superficie: incluye edificios y dosel)",
        "resolucion_m": round(max(dx, dy), 1),
        "pixeles": int(dentro.sum()),
        "elevacion_m": {"min": round(float(e.min()), 1), "p5": _pct(e, 5), "media": round(float(e.mean()), 1),
                        "p95": _pct(e, 95), "max": round(float(e.max()), 1)},
        "pendiente_grados": {"media": round(float(p.mean()), 1), "p50": _pct(p, 50), "p95": _pct(p, 95),
                             "max": round(float(p.max()), 1)},
        "area_por_pendiente_pct": {
            "llano_<3°": round(float((p < 3).mean() * 100), 1),
            "suave_3-12°": round(float(((p >= 3) & (p < 12)).mean() * 100), 1),
            "moderada_12-25°": round(float(((p >= 12) & (p < 25)).mean() * 100), 1),
            "fuerte_25-45°": round(float(((p >= 25) & (p < 45)).mean() * 100), 1),
            "escarpada_>45°": round(float((p >= 45).mean() * 100), 1),
        },
        "orientacion_pct": {s: round(float(c / max(1, conteo.sum()) * 100), 1)
                            for s, c in zip(sectores, conteo, strict=True)},
    }


# ── teselas ───────────────────────────────────────────────────────────────────────────────────


class TerrenoTiles:
    """Teselas PNG del relieve con una caché LRU propia (el DEM no depende de ninguna escena)."""

    def __init__(self, capacidad: int = 512):
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._capacidad = capacidad
        self._lock = threading.Lock()

    def _leer_tesela(self, z: int, x: int, y: int) -> tuple[np.ndarray, np.ndarray] | None:
        """DEM de la tesela con 1 px de borde ((258, 258) float64) y su máscara de validez."""
        import morecantile
        import rasterio
        from rasterio.errors import RasterioIOError
        from rio_tiler.errors import EmptyMosaicError, TileOutsideBounds
        from rio_tiler.io import Reader
        from rio_tiler.mosaic import mosaic_reader

        b = morecantile.tms.get("WebMercatorQuad").bounds(x, y, z)

        def _una(url: str, *a, **kw):
            with Reader(url) as src:
                return src.tile(x, y, z, tilesize=_TILESIZE, buffer=1, resampling_method="bilinear")

        with rasterio.Env(**_GDAL_ENV):
            try:
                img, _ = mosaic_reader(celdas((b.left, b.bottom, b.right, b.top)), _una,
                                       allowed_exceptions=(TileOutsideBounds, RasterioIOError))
            except EmptyMosaicError:
                return None
        return img.data[0].astype("float64"), img.mask != 0

    def render(self, producto: str, z: int, x: int, y: int, rescale: tuple[float, float] | None = None) -> bytes:
        from rio_tiler.colormap import cmap
        from rio_tiler.models import ImageData

        from imagery_mcp.tiles_render import _TRANSPARENT_PNG

        if producto not in PRODUCTOS:
            raise KeyError(producto)
        lo, hi = rescale or PRODUCTOS[producto][2]
        key = f"{producto}/{z}/{x}/{y}/{lo:g}_{hi:g}"
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        if z < MINZOOM:
            return _TRANSPARENT_PNG
        leido = self._leer_tesela(z, x, y)
        if leido is None:
            png = _TRANSPARENT_PNG
        else:
            dem, valido = leido
            n = 2 ** z
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
            valores = derivada(producto, dem, tamano_pixel_m(z, lat))[1:-1, 1:-1]
            gris = (np.clip((valores - lo) / max(1e-9, hi - lo), 0, 1) * 255).astype("uint8")
            mascara = ~valido[1:-1, 1:-1]
            rampa = PRODUCTOS[producto][3]
            if producto == "sombreado":
                png = _sombra_translucida(gris, mascara)
            else:
                png = ImageData(np.ma.MaskedArray(gris[None], mask=mascara[None])).render(
                    img_format="PNG", **({"colormap": cmap.get(rampa)} if rampa else {}))
        with self._lock:
            self._cache[key] = png
            while len(self._cache) > self._capacidad:
                self._cache.popitem(last=False)
        return png


def _sombra_translucida(luz: np.ndarray, fuera: np.ndarray) -> bytes:
    """El sombreado como SOMBRA: negro con transparencia según lo oscura que es la ladera (las
    iluminadas quedan casi transparentes). Así se superpone al mapa base o a la pendiente y la
    elevación en vez de taparlas, como en cualquier mapa topográfico."""
    import io

    from PIL import Image

    rgba = np.zeros((*luz.shape, 4), dtype="uint8")
    rgba[..., 3] = np.where(fuera, 0, np.clip((235.0 - luz) * 0.9, 0, 200)).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


def leyenda(producto: str, rescale: tuple[float, float] | None = None) -> dict[str, Any] | None:
    from imagery_mcp.vista import _colores

    if producto == "sombreado":   # es una sombra sobre el mapa: no tiene escala que leer
        return None
    nombre, unidad, rango, rampa = PRODUCTOS[producto]
    lo, hi = rescale or rango
    colores = _colores(rampa) if rampa else ["#000000", "#ffffff"]
    return {"type": "ramp", "field": f"{nombre} ({unidad})" if unidad else nombre, "min": lo, "max": hi,
            "colores": colores}


def url_teselas(producto: str, rescale: tuple[float, float] | None = None) -> str:
    url = f"/tiles-dem/{producto}/{{z}}/{{x}}/{{y}}.png"
    return url + (f"?rescale={rescale[0]:g},{rescale[1]:g}" if rescale else "")


def parse_ruta(path: str) -> tuple[str, int, int, int] | None:
    """``/tiles-dem/{producto}/{z}/{x}/{y}.png`` → (producto, z, x, y)."""
    if not path.startswith("/tiles-dem/"):
        return None
    partes = path[len("/tiles-dem/"):].split("/")
    if len(partes) != 4 or partes[0] not in PRODUCTOS or not partes[3].endswith(".png"):
        return None
    try:
        z, x, y = int(partes[1]), int(partes[2]), int(partes[3][:-4])
    except ValueError:
        return None
    return (partes[0], z, x, y) if 0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z else None
