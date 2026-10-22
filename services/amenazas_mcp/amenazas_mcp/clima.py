"""Serie diaria de clima de un punto (ERA5 por Open-Meteo, o NASA POWER) y su resumen mensual."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean
from typing import Any

from amenazas_mcp import fuentes
from amenazas_mcp.resumen import ErrorAmenazas

NOTA = ("cada fuente modela el punto a SU elevación (ver `elevacion_modelo_m`): en montaña, una elevación del "
        "modelo más baja que la real da temperaturas más altas (Bogotá: ERA5 2 594 m, POWER 1 790 m → enero 2023 "
        "11,9 °C contra 17,5 °C). Son reanálisis/pronósticos de malla, no estaciones")


def _filas_open_meteo(d: dict) -> list[dict]:
    dia = d.get("daily") or {}
    return [{"fecha": t, "t_media_c": dia["temperature_2m_mean"][i], "t_max_c": dia["temperature_2m_max"][i],
             "t_min_c": dia["temperature_2m_min"][i], "precipitacion_mm": dia["precipitation_sum"][i]}
            for i, t in enumerate(dia.get("time") or [])]


def _filas_power(d: dict) -> list[dict]:
    p = (d.get("properties") or {}).get("parameter") or {}
    def v(var: str, k: str) -> float | None:
        x = (p.get(var) or {}).get(k)
        return None if x is None or x <= -999 else x  # -999: sin dato en POWER
    return [{"fecha": f"{k[:4]}-{k[4:6]}-{k[6:]}", "t_media_c": v("T2M", k), "t_max_c": v("T2M_MAX", k),
             "t_min_c": v("T2M_MIN", k), "precipitacion_mm": v("PRECTOTCORR", k)} for k in sorted(p.get("T2M") or {})]


def totales(filas: list[dict]) -> dict[str, Any]:
    """Del periodo entero (V5: el agente sumaba los meses a mano y se corregía solo: 1 557,0 → 1 556,0)."""
    t = [f["t_media_c"] for f in filas if f["t_media_c"] is not None]
    p = [f["precipitacion_mm"] for f in filas if f["precipitacion_mm"] is not None]
    return {"precipitacion_total_periodo_mm": round(sum(p), 1) if p else None,
            "t_media_periodo_c": round(mean(t), 1) if t else None}


def mensual(filas: list[dict]) -> list[dict[str, Any]]:
    grupos: dict[str, list[dict]] = defaultdict(list)
    for f in filas:
        grupos[f["fecha"][:7]].append(f)
    salida = []
    for mes, fs in sorted(grupos.items()):
        t = [f["t_media_c"] for f in fs if f["t_media_c"] is not None]
        p = [f["precipitacion_mm"] for f in fs if f["precipitacion_mm"] is not None]
        salida.append({"mes": mes, "dias": len(fs), "t_media_c": round(mean(t), 1) if t else None,
                       "precipitacion_total_mm": round(sum(p), 1) if p else None})
    return salida


def serie(lon: float, lat: float, desde: str, hasta: str, fuente: str) -> tuple[list[dict], dict[str, Any]]:
    try:
        d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    except ValueError as exc:
        raise ErrorAmenazas("fechas en formato AAAA-MM-DD") from exc
    if d1 < d0 or (d1 - d0).days > 3700:
        raise ErrorAmenazas("periodo inválido o de más de ~10 años")
    futuro = d1 > date.today()
    if fuente == "nasa_power":
        if futuro:
            raise ErrorAmenazas("NASA POWER no pronostica: para fechas futuras usa fuente era5 (Open-Meteo)")
        d = fuentes.clima_power(lon, lat, desde, hasta)
        filas, elev = _filas_power(d), ((d.get("geometry") or {}).get("coordinates") or [None] * 3)[2]
    elif fuente == "era5":
        d = fuentes.clima_open_meteo(lon, lat, desde, hasta, pronostico=futuro)
        filas, elev = _filas_open_meteo(d), d.get("elevation")
    else:
        raise ErrorAmenazas("fuente: era5 (Open-Meteo, también pronóstico ≤16 días) o nasa_power")
    hechos = {"punto": [lon, lat], "fuente": fuente, "elevacion_modelo_m": elev, "dias": len(filas),
              **totales(filas), "mensual": mensual(filas), "pronostico": futuro, "nota": NOTA}
    return filas, hechos
