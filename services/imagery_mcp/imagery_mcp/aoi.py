"""El ÁREA y las FECHAS de un análisis: el bbox del AOI, su tamaño y límites, y el rango de fechas.

Salió de `engine.py` (F4 del plan de calidad: tenía 1.303 líneas), tal cual.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from imagery_mcp.config import Limits


def _eng():
    """`engine` importa este módulo; lo que vive allí (y las pruebas sustituyen allí) se resuelve
    al usarlo."""
    from imagery_mcp import engine

    return engine


def aoi_bbox(aoi_geojson: dict) -> tuple[float, float, float, float]:  # noqa: C901
    """BBox [minx,miny,maxx,maxy] de un Geometry o FeatureCollection EPSG:4326."""
    def _coords(geom: dict):
        t = geom.get("type")
        c = geom.get("coordinates", [])
        if t == "Point":
            yield c
        elif t in ("MultiPoint", "LineString"):
            yield from c
        elif t in ("MultiLineString", "Polygon"):
            for ring in c:
                yield from ring
        elif t == "MultiPolygon":
            for poly in c:
                for ring in poly:
                    yield from ring
        elif t == "GeometryCollection":
            for g in geom.get("geometries", []):
                yield from _coords(g)

    geoms: list[dict] = []
    if aoi_geojson.get("type") == "FeatureCollection":
        geoms = [f.get("geometry") or {} for f in aoi_geojson.get("features", [])]
    elif aoi_geojson.get("type") == "Feature":
        geoms = [aoi_geojson.get("geometry") or {}]
    else:
        geoms = [aoi_geojson]

    xs: list[float] = []
    ys: list[float] = []
    for g in geoms:
        for x, y in _coords(g):
            xs.append(float(x))
            ys.append(float(y))
    if not xs:
        raise _eng().ImageryError("El AOI no contiene coordenadas")
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_area_km2(bbox: tuple[float, float, float, float]) -> float:
    """Área planar aproximada del bbox en km² (coseno de la latitud media)."""
    minx, miny, maxx, maxy = bbox
    lat = math.radians((miny + maxy) / 2)
    dx_km = (maxx - minx) * 111.32 * math.cos(lat)
    dy_km = (maxy - miny) * 110.57
    return abs(dx_km * dy_km)


def check_aoi(bbox: tuple[float, float, float, float], limits: Limits) -> None:
    area = bbox_area_km2(bbox)
    if area > limits.aoi_max_km2:
        raise _eng().ImageryError(
            f"El AOI mide {area:,.0f} km² y el límite v1 es "
            f"{limits.aoi_max_km2:,.0f} km². Reduce la zona o divide el análisis."
        )


def bbox_polygon(bbox: tuple[float, float, float, float]) -> dict:
    minx, miny, maxx, maxy = bbox
    return {"type": "Polygon", "coordinates": [[
        [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
    ]]}


def default_date_range(limits: Limits) -> tuple[str, str]:
    today = datetime.now(UTC).date()
    return (str(today - timedelta(days=limits.default_lookback_days)), str(today))


def rango_fechas(date_from: str | None, date_to: str | None, limits: Limits) -> tuple[str, str]:
    """Rango ISO validado (V5 F3).

    Una fecha que no existe («2026-02-29») llegaba a STAC y volvía un 400 que el
    agente contaba como «fallo interno»; y con solo `date_from` se usaba en
    silencio la ventana reciente, ignorando lo pedido.
    """
    from datetime import date

    def leer(valor: str, nombre: str) -> date:
        try:
            return date.fromisoformat(str(valor)[:10])
        except ValueError:
            raise _eng().ImageryError(
                f"{nombre} «{valor}» no es una fecha válida: usa YYYY-MM-DD de un día que exista"
            ) from None

    hoy = datetime.now(UTC).date()
    ventana = timedelta(days=limits.default_lookback_days)
    d1 = leer(date_to, "date_to") if date_to else hoy
    d0 = leer(date_from, "date_from") if date_from else d1 - ventana
    if d0 > d1:
        raise _eng().ImageryError(f"date_from ({d0}) es posterior a date_to ({d1})")
    return str(d0), str(d1)
