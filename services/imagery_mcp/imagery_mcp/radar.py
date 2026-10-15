"""Sentinel-1 (radar SAR, banda C) de una zona: retrodispersión VV/VH en dB y la superficie «lisa» (agua).

El radar ve a través de las nubes y de noche: en Colombia, donde la óptica suele estar nublada, es la forma
de ver agua e inundaciones. Fuente: `sentinel-1-rtc` de Planetary Computer (gamma0 con corrección de
terreno, COG en UTM; firmados con el SAS de PC). Medido el 2026-10-15: escenas cada ~6–12 días sobre Bogotá.

El agua abierta devuelve poco al radar (superficie especular): VV muy bajo. El umbral es un CRITERIO, no un
dato (la literatura usa −15 a −20 dB en VV); se dice cuál se usó y lo elige quien pide.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from imagery_mcp.engine import _GDAL_ENV, ImageryError

COLECCION = "sentinel-1-rtc"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
LADO_MAX = 0.5
MAX_PX = 3000
_firmador = None


def _firmar(href: str) -> str:
    global _firmador
    if _firmador is None:
        from imagery_mcp.providers import PlanetaryComputerProvider

        _firmador = PlanetaryComputerProvider()
    return _firmador.sign(href)


def escenas(bbox: tuple[float, float, float, float], desde: str, hasta: str) -> list[dict[str, Any]]:
    """Escenas que cubren el CENTRO de la zona, de la más reciente a la más antigua."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    cuerpo = {"collections": [COLECCION], "intersects": {"type": "Point", "coordinates": [cx, cy]},
              "datetime": f"{desde}/{hasta}", "limit": 50, "sortby": [{"field": "datetime", "direction": "desc"}]}
    import httpx

    try:
        r = httpx.post(STAC, json=cuerpo, timeout=60, headers={"User-Agent": "geo-copilot-imagery/1"})
    except httpx.HTTPError as exc:
        raise ImageryError(f"el catálogo de Planetary Computer no respondió ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise ImageryError(f"el catálogo respondió HTTP {r.status_code}: {r.text[:200]}")
    return [{"id": f["id"], "fecha": str(f["properties"].get("datetime"))[:19],
             "orbita": f["properties"].get("sat:orbit_state"), "plataforma": f["properties"].get("platform"),
             "vv": f["assets"]["vv"]["href"], "vh": f["assets"].get("vh", {}).get("href")}
            for f in r.json().get("features") or [] if "vv" in (f.get("assets") or {})]


def _leer_db(href: str, aoi_geojson: dict, bbox: tuple[float, float, float, float]) -> np.ndarray:
    """Valores en dB dentro del área (gamma0 lineal → 10·log10); fuera del área o sin dato: NaN."""
    import rasterio
    from affine import Affine
    from rasterio.enums import Resampling
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_bounds, transform_geom
    from rasterio.windows import from_bounds

    with rasterio.Env(**_GDAL_ENV), rasterio.open(_firmar(href)) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, *bbox)
        ventana = from_bounds(*b, ds.transform).round_offsets().round_lengths()
        escala = max(1.0, ventana.width / MAX_PX, ventana.height / MAX_PX)
        forma = (max(1, int(ventana.height / escala)), max(1, int(ventana.width / escala)))
        datos = ds.read(1, window=ventana, out_shape=forma, resampling=Resampling.average, boundless=True,
                        fill_value=0).astype("float64")
        # la ventana leída reducida: cada píxel de salida abarca (ancho/forma) píxeles de la escena
        t = ds.window_transform(ventana) * Affine.scale(ventana.width / forma[1], ventana.height / forma[0])
        crs = ds.crs
    feats = aoi_geojson.get("features") if aoi_geojson.get("type") == "FeatureCollection" else [aoi_geojson]
    geoms = [(f.get("geometry") if f.get("type") == "Feature" else f) for f in feats or []]
    polis = [transform_geom("EPSG:4326", crs, g) for g in geoms if g and g.get("type") in ("Polygon", "MultiPolygon")]
    dentro = geometry_mask(polis, out_shape=forma, transform=t, invert=True) if polis else np.ones(forma, bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10 * np.log10(datos)
    db[~dentro | ~np.isfinite(db) | (datos <= 0)] = np.nan
    return db


def _resumen(db: np.ndarray) -> dict[str, float] | None:
    v = db[np.isfinite(db)]
    if not v.size:
        return None
    return {"media_db": round(float(v.mean()), 2), "p10_db": round(float(np.percentile(v, 10)), 2),
            "mediana_db": round(float(np.median(v)), 2), "p90_db": round(float(np.percentile(v, 90)), 2)}


def sar(aoi_geojson: dict, bbox: tuple[float, float, float, float], desde: str, hasta: str,
        umbral_agua_db: float) -> dict[str, Any]:
    if bbox[2] - bbox[0] > LADO_MAX or bbox[3] - bbox[1] > LADO_MAX:
        raise ImageryError(f"la zona pasa de {LADO_MAX:g}° de lado: pide el radar de un área más pequeña")
    halladas = escenas(bbox, desde, hasta)
    if not halladas:
        raise ImageryError(f"no hay escenas Sentinel-1 entre {desde} y {hasta} sobre la zona")
    e = halladas[0]
    vv = _leer_db(e["vv"], aoi_geojson, bbox)
    validos = np.isfinite(vv)
    if not validos.any():
        raise ImageryError("la escena más reciente no cubre la zona con datos válidos")
    agua = float((vv[validos] < umbral_agua_db).mean())
    hechos: dict[str, Any] = {
        "escena": e["id"], "fecha": e["fecha"], "orbita": e["orbita"], "plataforma": e["plataforma"],
        "vv": _resumen(vv), "vh": _resumen(_leer_db(e["vh"], aoi_geojson, bbox)) if e["vh"] else None,
        "superficie_lisa_pct": round(100 * agua, 2), "umbral_agua_db": umbral_agua_db,
        "otras_fechas": [x["fecha"][:10] for x in halladas[1:10]],
        "fuente": "Sentinel-1 RTC (gamma0, corrección de terreno), Planetary Computer",
        "nota": ("«superficie lisa» = VV bajo el umbral: agua abierta probable; también la dan pavimento muy liso o "
                 "sombra de radar en laderas. El umbral es un criterio (literatura: −15 a −20 dB), no un dato"),
    }
    return hechos
