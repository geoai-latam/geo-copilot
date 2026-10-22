"""De lo que dan las fuentes a hechos para el LLM y capas para el mapa."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from statistics import mean
from typing import Any


class ErrorAmenazas(ValueError):
    """Pedido que no se puede atender (el mensaje dice qué dar)."""


def zona(area: dict | None, bbox: list[float] | None) -> tuple[list[float], Any]:
    """(bbox, geometría o None): de una capa de la sesión (`area`) o de un rectángulo."""
    if area:
        from geo_mcp_kit import area as areas

        g = areas.geometria(area)
        return list(g.bounds), g
    if bbox and len(bbox) == 4:
        x1, y1, x2, y2 = (float(v) for v in bbox)
        if -180 <= x1 < x2 <= 180 and -90 <= y1 < y2 <= 90:
            return [x1, y1, x2, y2], None
    raise ErrorAmenazas("falta la zona: `area` (una capa de la sesión) o `bbox` [minx,miny,maxx,maxy]")


def punto(ref: dict | None, lon: float | None, lat: float | None) -> tuple[float, float]:
    """(lon, lat) de una referencia de un elemento (`punto`, una capa) o de los números."""
    if ref:
        from shapely.geometry import shape

        feats = [f for f in ref.get("features") or [] if f.get("geometry")]
        if len(feats) != 1:
            raise ErrorAmenazas(f"la referencia tiene {len(feats)} elementos: hace falta UNO (o `lon`/`lat`)")
        p = shape(feats[0]["geometry"]).representative_point()
        return round(p.x, 5), round(p.y, 5)
    if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ErrorAmenazas("falta el punto: una referencia (`punto`, una capa de un elemento) o `lon` y `lat`")
    return float(lon), float(lat)


def sismos(fc: dict, total: int) -> dict[str, Any]:
    feats = fc.get("features") or []
    for f in feats:
        p = f["properties"]
        p["fecha"] = datetime.fromtimestamp(p["time"] / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC") if p.get("time") else None
        p["profundidad_km"] = (f["geometry"]["coordinates"] + [None])[2]
        f["properties"] = {k: p.get(k) for k in ("mag", "magType", "place", "fecha", "profundidad_km", "url")}
    mayores = [f["properties"] for f in feats[:5]]
    return {"sismos": len(feats), "en_el_catalogo": total, "completo": len(feats) >= total,
            "mayores": mayores, "fuente": "USGS (catálogo mundial; en Colombia la red oficial es la del SGC)"}


def focos(filas: list[dict]) -> dict:
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [f["longitude"], f["latitude"]]},
              "properties": {"fecha": f.get("acq_date"), "hora_utc": f.get("acq_time"), "confianza": f.get("confidence"),
                             "frp_mw": float(f["frp"]) if f.get("frp") else None, "dia_noche": f.get("daynight")}}
             for f in filas]
    return {"type": "FeatureCollection", "features": feats}


def incendios(fc: dict, periodo: str, sensor: str) -> dict[str, Any]:
    feats = fc["features"]
    hechos = {"focos": len(feats), "periodo": periodo, "sensor": sensor,
              "por_fecha": dict(sorted(Counter(f["properties"]["fecha"] for f in feats).items())),
              "por_confianza": dict(Counter(str(f["properties"]["confianza"]) for f in feats)),
              "frp_max_mw": max((f["properties"]["frp_mw"] or 0 for f in feats), default=None),
              "fuente": "NASA FIRMS (focos de calor activos casi en tiempo real; no todo foco es un incendio)"}
    return hechos


def caudal(celdas: list[dict], lon: float, lat: float) -> tuple[list[dict], dict[str, Any]]:
    """La serie de la celda del punto y el caudal medio de las vecinas, de mayor a menor."""
    def media(c: dict) -> float:
        v = [x for x in (c.get("daily") or {}).get("river_discharge") or [] if x is not None]
        return round(mean(v), 1) if v else 0.0

    propia = min(celdas, key=lambda c: (c["latitude"] - lat) ** 2 + (c["longitude"] - lon) ** 2)
    serie = [{"fecha": t, "caudal_m3s": q} for t, q in zip(propia["daily"]["time"], propia["daily"]["river_discharge"],
                                                         strict=False)]
    vecinas = sorted(({"lat": round(c["latitude"], 4), "lon": round(c["longitude"], 4), "caudal_medio_m3s": media(c)}
                      for c in celdas), key=lambda c: -c["caudal_medio_m3s"])
    hechos = {"celda_del_punto": {"lat": round(propia["latitude"], 4), "lon": round(propia["longitude"], 4),
                                  "caudal_medio_m3s": media(propia)},
              "vecinas_por_caudal": vecinas[:6],
              "nota": ("GloFAS es una malla de 0,05° (~5 km): el río que se pregunta puede pasar por una celda VECINA "
                       "(compara: un río grande lleva cientos o miles de m³/s). Es un modelo (reanálisis y pronóstico), "
                       "no una estación de aforo"),
              "fuente": "GloFAS (Copernicus) por la Flood API de Open-Meteo"}
    return serie, hechos
