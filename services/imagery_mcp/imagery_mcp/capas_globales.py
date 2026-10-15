"""Capas globales para una zona: cobertura del suelo (ESA WorldCover) y población (WorldPop).

Medido el 2026-10-15:
- WorldCover v200 (2021, 10 m): COG públicos de la ESA en S3 por celdas de 3°×3°
  (`ESA_WorldCover_10m_2021_v200_N03W075_Map.tif`, ~96 MB); el servidor atiende rangos (HTTP 206):
  se lee solo la ventana de la zona (y sus overviews si es grande).
- WorldPop R2024B (100 m, «constrained»: la población solo en áreas construidas; 2015–2030, proyecciones
  después de 2020): un GeoTIFF por país y año (Colombia 2025: 64 MB). Su servidor IGNORA `Range`
  (responde 200 con el archivo entero): se descarga una vez por año a disco y se lee en local.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
from typing import Any

import numpy as np

from imagery_mcp.engine import _GDAL_ENV, ImageryError

WORLDCOVER = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
              "ESA_WorldCover_10m_2021_v200_{ns}{lat:02d}{ew}{lon:03d}_Map.tif")
WORLDPOP = ("https://data.worldpop.org/GIS/Population/Global_2015_2030/R2024B/{anio}/{iso3}/v1/100m/constrained/"
            "{iso}_pop_{anio}_CN_100m_R2024B_v1.tif")
CACHE = os.environ.get("IMAGERY_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "imagery-cache")
#: Clases oficiales de WorldCover v200 (código: nombre, color de la leyenda de la ESA)
CLASES = {10: ("Árboles", "#006400"), 20: ("Arbustal", "#ffbb22"), 30: ("Pastizal", "#ffff4c"),
          40: ("Cultivos", "#f096ff"), 50: ("Construido", "#fa0000"), 60: ("Suelo desnudo / escasa vegetación",
          "#b4b4b4"), 70: ("Nieve y hielo", "#f0f0f0"), 80: ("Agua permanente", "#0064c8"),
          90: ("Humedal herbáceo", "#0096a0"), 95: ("Manglar", "#00cf75"), 100: ("Musgo y liquen", "#fae6a0")}
LADO_MAX = 2.0          # ° de lado de la zona
MAX_PX = 4000           # lado máximo leído: una zona grande se lee de los overviews (y se dice)
_cerrojo_descarga = threading.Lock()


def _celdas_worldcover(bbox: tuple[float, float, float, float]) -> list[str]:
    urls = []
    for lat in range(math.floor(bbox[1] / 3) * 3, math.floor(bbox[3] / 3) * 3 + 1, 3):
        for lon in range(math.floor(bbox[0] / 3) * 3, math.floor(bbox[2] / 3) * 3 + 1, 3):
            urls.append(WORLDCOVER.format(ns="N" if lat >= 0 else "S", lat=abs(lat),
                                          ew="E" if lon >= 0 else "W", lon=abs(lon)))
    return urls


def _mascara(aoi_geojson: dict, forma: tuple[int, int], transform) -> np.ndarray:
    """True en los píxeles cuyo CENTRO cae en el área (estadística zonal estándar; un punto: su recuadro)."""
    from rasterio.features import geometry_mask

    feats = aoi_geojson.get("features") if aoi_geojson.get("type") == "FeatureCollection" else [aoi_geojson]
    geoms = [(f.get("geometry") if f.get("type") == "Feature" else f) for f in feats or []]
    poligonos = [g for g in geoms if g and g.get("type") in ("Polygon", "MultiPolygon")]
    if not poligonos:
        return np.ones(forma, dtype=bool)
    return geometry_mask(poligonos, out_shape=forma, transform=transform, invert=True, all_touched=False)


def _area_px_km2(transform, filas: int) -> np.ndarray:
    """Área (km²) de un píxel de cada fila de una rejilla en grados (se achica con la latitud)."""
    dx, dy = abs(transform.a), abs(transform.e)
    lats = transform.f - dy * (np.arange(filas) + 0.5)
    return (dx * 111.320 * np.cos(np.radians(lats))) * (dy * 110.574)


def _leer(urls: list[str], bbox: tuple[float, float, float, float]):
    """(matriz, transform, resolución en °) de la ventana `bbox`, uniendo celdas y con tope de tamaño."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.errors import RasterioIOError
    from rasterio.merge import merge

    with rasterio.Env(**_GDAL_ENV):
        fuentes = []
        for url in urls:
            try:
                fuentes.append(rasterio.open(url))
            except RasterioIOError:
                continue  # celda sin datos (mar): no está en el bucket
        if not fuentes:
            raise ImageryError("no hay datos de cobertura para esta zona")
        res = max(abs(fuentes[0].res[0]), (bbox[2] - bbox[0]) / MAX_PX, (bbox[3] - bbox[1]) / MAX_PX)
        datos, transform = merge(fuentes, bounds=bbox, res=res, resampling=Resampling.nearest, nodata=0)
        for f in fuentes:
            f.close()
    return datos[0], transform, res


def cobertura(aoi_geojson: dict, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """Área por clase de WorldCover 2021 dentro de la zona (km² y %)."""
    if bbox[2] - bbox[0] > LADO_MAX or bbox[3] - bbox[1] > LADO_MAX:
        raise ImageryError(f"la zona pasa de {LADO_MAX:g}° de lado: pide la cobertura de un área más pequeña")
    datos, transform, res = _leer(_celdas_worldcover(bbox), bbox)
    dentro = _mascara(aoi_geojson, datos.shape, transform) & (datos > 0)
    area_fila = _area_px_km2(transform, datos.shape[0])[:, None] * np.ones((1, datos.shape[1]))
    total = float(area_fila[dentro].sum())
    if total <= 0:
        raise ImageryError("la zona no tiene píxeles de cobertura (¿mar o fuera de los datos?)")
    clases = []
    for codigo, (nombre, color) in CLASES.items():
        km2 = float(area_fila[dentro & (datos == codigo)].sum())
        if km2 > 0:
            clases.append({"clase": nombre, "codigo": codigo, "km2": round(km2, 3),
                           "porcentaje": round(100 * km2 / total, 2), "color": color})
    clases.sort(key=lambda c: -c["km2"])
    return {"area_km2": round(total, 3), "clases": clases, "resolucion_m": round(res * 111_320),
            "fuente": "ESA WorldCover v200 (2021, 10 m)",
            **({"nota": "zona grande: se leyó una versión reducida del mapa (resolución arriba): las áreas son "
                        "aproximadas"} if res > 1 / 12000 * 1.5 else {})}


def _archivo_worldpop(anio: int, iso3: str) -> str:
    """Ruta local del GeoTIFF del año (se descarga una vez: su servidor no sirve rangos)."""
    from geo_mcp_kit import red

    os.makedirs(CACHE, exist_ok=True)
    ruta = os.path.join(CACHE, f"worldpop_{iso3}_{anio}.tif")
    with _cerrojo_descarga:
        if not os.path.exists(ruta):
            url = WORLDPOP.format(anio=anio, iso3=iso3.upper(), iso=iso3.lower())
            try:
                r = red.get(url, None, timeout=600, user_agent="geo-copilot-imagery/1", max_bytes=400_000_000)
            except red.ErrorRed as exc:
                raise ImageryError(f"WorldPop no tiene {iso3.upper()} {anio} o no respondió: {exc}") from exc
            temporal = ruta + ".parcial"
            with open(temporal, "wb") as fh:
                fh.write(r.content)
            os.replace(temporal, ruta)
    return ruta


def poblacion(aoi_geojson: dict, bbox: tuple[float, float, float, float], anio: int,
              iso3: str = "COL") -> dict[str, Any]:
    """Personas estimadas por WorldPop dentro de la zona (suma de los píxeles cuyo centro cae dentro)."""
    import rasterio
    from rasterio.windows import from_bounds

    if not 2015 <= anio <= 2030:
        raise ImageryError("WorldPop R2024B cubre 2015–2030 (después de 2020 son proyecciones)")
    with rasterio.open(_archivo_worldpop(anio, iso3)) as ds:
        ventana = from_bounds(*bbox, ds.transform).round_offsets().round_lengths()
        datos = ds.read(1, window=ventana, boundless=True, fill_value=0).astype("float64")
        transform = ds.window_transform(ventana)
        nodata = ds.nodata
    if nodata is not None:
        datos[datos == nodata] = 0
    dentro = _mascara(aoi_geojson, datos.shape, transform)
    personas = float(np.nansum(datos[dentro]))
    return {"personas": round(personas), "anio": anio, "pais": iso3.upper(),
            "fuente": "WorldPop R2024B, 100 m, «constrained» (la población se asigna solo a áreas construidas)",
            "nota": ("ESTIMACIÓN por modelo, no un censo" + ("; después de 2020 es una PROYECCIÓN" if anio > 2020
                                                              else ""))}
