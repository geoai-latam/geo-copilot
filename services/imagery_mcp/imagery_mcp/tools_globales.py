"""Tools de capas globales: cobertura del suelo (WorldCover), población (WorldPop) y radar (Sentinel-1).

Van aparte de `server.py` (no pasa de 500 líneas): `registrar` las monta sobre el mismo FastMCP, con la
misma ejecución con tiempo máximo y errores honestos.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from geo_mcp_kit import geo_meta, geo_result, stats
from mcp.types import ToolAnnotations

from imagery_mcp import capas_globales, radar
from imagery_mcp.aoi import aoi_bbox


def _bbox(aoi_geojson: dict) -> tuple[float, float, float, float]:
    b = aoi_bbox(aoi_geojson)
    if b[2] - b[0] < 1e-3 and b[3] - b[1] < 1e-3:  # un punto: su entorno de ~1 km
        b = (b[0] - 0.005, b[1] - 0.005, b[2] + 0.005, b[3] + 0.005)
    return b


def cobertura(aoi_geojson: dict) -> dict[str, Any]:
    hechos = capas_globales.cobertura(aoi_geojson, _bbox(aoi_geojson))
    items = [{"label": c["clase"], "value": c["porcentaje"], "unit": "%"} for c in hechos["clases"]]
    return geo_result([stats(items, name="Cobertura del suelo (WorldCover 2021)")], facts=hechos)


def poblacion(aoi_geojson: dict, anio: int) -> dict[str, Any]:
    return geo_result([], facts=capas_globales.poblacion(aoi_geojson, _bbox(aoi_geojson), anio))


def sar(aoi_geojson: dict, desde: str | None, hasta: str | None, umbral: float) -> dict[str, Any]:
    hasta = hasta or date.today().isoformat()
    desde = desde or (date.fromisoformat(hasta[:10]) - timedelta(days=30)).isoformat()
    return geo_result([], facts=radar.sar(aoi_geojson, _bbox(aoi_geojson), desde[:10], hasta[:10], umbral))


def registrar(mcp, wrap) -> None:
    """Monta las tools de capas globales sobre `mcp`; `wrap` es la ejecución con tiempo máximo."""
    geo_aoi = {"aoi_geojson": ["geometry", "layer_ref"]}

    @mcp.tool(meta=geo_meta(inputs=geo_aoi, outputs=["stats"], cost="medium"),
              annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True), structured_output=True)
    def imagery_land_cover(aoi_geojson: dict) -> dict[str, Any]:
        """COBERTURA DEL SUELO de una zona (hasta 2°×2°) con ESA WorldCover 2021 (10 m, todo el planeta):
        el área (km²) y el % por clase — árboles, arbustal, pastizal, cultivos, construido, suelo desnudo,
        agua permanente, humedal, manglar… Es un mapa de 2021: no muestra cambios posteriores."""
        return wrap(cobertura, aoi_geojson)

    @mcp.tool(meta=geo_meta(inputs=geo_aoi, outputs=[], cost="medium"),
              annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True), structured_output=True)
    def imagery_population(aoi_geojson: dict, year: int = 2025) -> dict[str, Any]:
        """POBLACIÓN ESTIMADA de una zona con WorldPop (100 m, 2015–2030; después de 2020 son
        proyecciones): personas cuyo píxel cae en el área. Es un MODELO (asigna la población a las áreas
        construidas), no un censo: para cifras oficiales de Colombia está el censo del DANE."""
        return wrap(poblacion, aoi_geojson, int(year))

    @mcp.tool(meta=geo_meta(inputs=geo_aoi, outputs=[], cost="medium"),
              annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True), structured_output=True)
    def imagery_sar(aoi_geojson: dict, date_from: str | None = None, date_to: str | None = None,
                    water_threshold_db: float = -18.0) -> dict[str, Any]:
        """RADAR Sentinel-1 de una zona (hasta 0,5°): la escena más reciente del periodo (por defecto los
        últimos 30 días), su retrodispersión VV/VH en dB y el % de superficie «lisa» (VV bajo
        `water_threshold_db`: agua abierta probable). El radar ve A TRAVÉS DE LAS NUBES: sirve para agua e
        inundaciones cuando la imagen óptica está nublada. Compara fechas llamándola con otro periodo
        (`otras_fechas` lista las disponibles)."""
        return wrap(sar, aoi_geojson, date_from, date_to, float(water_threshold_db))
