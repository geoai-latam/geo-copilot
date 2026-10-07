"""Tools del relieve (Copernicus DEM): el terreno de una zona y la cuenca de un punto.

Van aparte de `server.py` (no pasa de 500 líneas): `registrar` las monta sobre el mismo FastMCP,
con la misma ejecución con tiempo máximo y errores honestos.
"""

from __future__ import annotations

from typing import Any, Literal

from geo_mcp_kit import feature_collection, geo_meta, geo_result, raster_tiles
from mcp.types import ToolAnnotations

from imagery_mcp import hidrologia, terreno
from imagery_mcp.aoi import aoi_bbox
from imagery_mcp.engine import ImageryError

#: Lado máximo (°) de la zona de imagery_terrain: más allá son demasiadas celdas de 1° que abrir.
_LADO_MAX = 3.0


def _rango_elevacion(hechos: dict) -> tuple[float, float]:
    """La rampa de elevación ajustada a la zona (p5–p95, redondeado): en la sabana de Bogotá
    (2.550 m) y en la costa (20 m) el relieve se ve igual de bien."""
    e = hechos["elevacion_m"]
    lo, hi = float(e["p5"]), float(e["p95"])
    if hi - lo < 20:
        lo, hi = lo - 10, hi + 10
    return (round(lo, -1) if lo > 50 else round(lo), round(hi, -1) if hi > 50 else round(hi))


def terreno_de_zona(aoi_geojson: dict, producto: str) -> dict[str, Any]:
    if producto not in terreno.PRODUCTOS:
        raise ImageryError(f"product desconocido: {producto!r}; válidos: {', '.join(terreno.PRODUCTOS)}")
    bbox = aoi_bbox(aoi_geojson)
    if bbox[2] - bbox[0] > _LADO_MAX or bbox[3] - bbox[1] > _LADO_MAX:
        raise ImageryError(f"la zona pasa de {_LADO_MAX:g}° de lado: pide el relieve de un área más pequeña")
    if bbox[2] - bbox[0] < 1e-3 and bbox[3] - bbox[1] < 1e-3:   # un punto: su entorno de ~1 km
        bbox = (bbox[0] - 0.005, bbox[1] - 0.005, bbox[2] + 0.005, bbox[3] + 0.005)
    hechos = terreno.hechos_zona(aoi_geojson, bbox)
    rescale = _rango_elevacion(hechos) if producto == "elevacion" else None
    nombre = terreno.PRODUCTOS[producto][0]
    capa = raster_tiles(f"{nombre} (Copernicus DEM 30 m)", terreno.url_teselas(producto, rescale),
                        bounds=list(bbox), minzoom=terreno.MINZOOM, legend=terreno.leyenda(producto, rescale))
    return geo_result([capa], facts={**hechos, "producto": producto, "bbox": list(bbox)})


def cuenca(geojson: dict, radio_km: float, umbral_km2: float) -> dict[str, Any]:
    r = hidrologia.cuenca_de(geojson, radio_km=radio_km, umbral_km2=umbral_km2)
    return geo_result([feature_collection("Cuenca y red de drenaje", r["geojson"], crs="EPSG:4326")],
                      facts=r["hechos"], style_hint={"field": "tipo", "method": "unique"})


def registrar(mcp, wrap) -> None:
    """Monta las tools del relieve sobre `mcp`; `wrap` es la ejecución con tiempo máximo."""

    @mcp.tool(
        meta=geo_meta(inputs={"aoi_geojson": ["geometry", "layer_ref"]}, outputs=["raster_tiles"], cost="medium"),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
        structured_output=True,
    )
    def imagery_terrain(
        aoi_geojson: dict,
        product: Literal["elevacion", "pendiente", "sombreado", "orientacion"] = "elevacion",
    ) -> dict[str, Any]:
        """El RELIEVE de una zona (hasta 3°×3°) con el modelo digital de elevación Copernicus
        GLO-30 (30 m, todo el planeta): pinta `product` — 'elevacion' (m), 'pendiente' (°),
        'sombreado' (relieve iluminado) u 'orientacion' (hacia dónde mira la ladera) — y mide la
        elevación (mín., media, percentiles, máx.), la pendiente y el % del área por clase de
        pendiente (llano, suave, moderada, fuerte, escarpada) y por orientación. Es un modelo de
        SUPERFICIE: en ciudad y bosque incluye edificios y dosel."""
        return wrap(terreno_de_zona, aoi_geojson, product)

    @mcp.tool(
        meta=geo_meta(inputs={"outlet_geojson": ["geometry", "layer_ref"]}, outputs=["feature_collection"],
                      cost="high"),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
        structured_output=True,
    )
    def imagery_watershed(
        outlet_geojson: dict,
        radius_km: float = 15.0,
        stream_threshold_km2: float = 1.0,
    ) -> dict[str, Any]:
        """La CUENCA HIDROGRÁFICA y su RED DE DRENAJE sobre el DEM Copernicus GLO-30 (relleno de
        depresiones + dirección de flujo D8), según lo que señale `outlet_geojson`: un PUNTO (la
        cuenca que drena a él; se ajusta al cauce a ≤300 m), una LÍNEA, p. ej. la capa de un río
        (la cuenca de su punto aguas abajo) o un ÁREA, p. ej. un municipio (la del cauce principal
        que sale de ella). Devuelve el polígono de la cuenca (área km², elevación), los cauces con
        más de `stream_threshold_km2` drenados y el punto de salida. Se calcula en una ventana de
        al menos `radius_km` (0,5–40) alrededor: si la cuenca toca su borde, los hechos lo dicen
        (`toca_borde`) y con un radio mayor se ve entera."""
        return wrap(cuenca, outlet_geojson, radius_km, stream_threshold_km2)
