"""Las fuentes públicas de amenazas y clima, sin clave, y lo medido de cada una (2026-10-15).

- Sismos: USGS FDSN (`/query` GeoJSON y `/count` con los mismos filtros). Colombia ±, M≥4, un año: 191.
- Incendios: NASA FIRMS publica CSV regionales SIN clave (24 h, 48 h, 7 días; VIIRS S-NPP / NOAA-20 y MODIS);
  la API por área pide MAP_KEY. Se filtra la región «South_America» por la zona aquí.
- Caudales: GloFAS por la Flood API de Open-Meteo (malla de 0,05°). OJO: un punto cae en UNA celda, y la del
  río grande puede ser la vecina: en Honda la celda del punto da 1,5 m³/s y a ~5 km el Magdalena ~2 500.
- Clima: ERA5 por Open-Meteo (archivo; pronóstico hasta 16 días) y NASA POWER. Cada uno modela el punto a SU
  elevación: Bogotá (2 600 m) = 2 594 m en Open-Meteo y 1 790 m en POWER → enero 2023: 11,9 °C contra 17,5 °C.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from geo_mcp_kit import red

USGS = "https://earthquake.usgs.gov/fdsnws/event/1"
FIRMS = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
FIRMS_SENSORES = {"viirs_snpp": ("suomi-npp-viirs-c2", "SUOMI_VIIRS_C2"),
                  "viirs_noaa20": ("noaa-20-viirs-c2", "J1_VIIRS_C2"), "modis": ("modis-c6.1", "MODIS_C6_1")}
FIRMS_PERIODOS = ("24h", "48h", "7d")
GLOFAS = "https://flood-api.open-meteo.com/v1/flood"
OPEN_METEO = {"archivo": "https://archive-api.open-meteo.com/v1/archive",
              "pronostico": "https://api.open-meteo.com/v1/forecast"}
POWER = "https://power.larc.nasa.gov/api/temporal/daily/point"
UA = "geo-copilot-amenazas-mcp/1"


def _get(url: str, params: dict | None = None, timeout: float = 90) -> Any:
    return red.get(url, params, timeout=timeout, user_agent=UA, max_bytes=60_000_000)


def sismos(bbox: list[float], desde: str, hasta: str, magnitud_min: float, maximo: int) -> tuple[dict, int]:
    """(GeoJSON de los sismos, cuántos hay en el catálogo con esos filtros)."""
    p = {"starttime": desde, "endtime": hasta, "minlatitude": bbox[1], "maxlatitude": bbox[3],
         "minlongitude": bbox[0], "maxlongitude": bbox[2], "minmagnitude": magnitud_min}
    total = int(_get(f"{USGS}/count", p).text.strip())
    fc = _get(f"{USGS}/query", {**p, "format": "geojson", "orderby": "magnitude", "limit": maximo}).json()
    return fc, total


def incendios(bbox: list[float], periodo: str, sensor: str) -> list[dict[str, Any]]:
    """Focos de calor del CSV regional dentro del rectángulo."""
    carpeta, prefijo = FIRMS_SENSORES[sensor]
    texto = _get(f"{FIRMS}/{carpeta}/csv/{prefijo}_South_America_{periodo}.csv", timeout=120).text
    salida = []
    for fila in csv.DictReader(io.StringIO(texto)):
        try:
            x, y = float(fila["longitude"]), float(fila["latitude"])
        except (KeyError, ValueError):
            continue
        if bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
            salida.append({**fila, "longitude": x, "latitude": y})
    return salida


def caudal(lon: float, lat: float, dias_pasados: int, dias_pronostico: int, radio_celdas: int) -> list[dict]:
    """La serie diaria de la celda del punto y de sus vecinas (malla de 0,05°): una petición, varias celdas."""
    paso = 0.05
    pares = [(round(lat + i * paso, 4), round(lon + j * paso, 4))
             for i in range(-radio_celdas, radio_celdas + 1) for j in range(-radio_celdas, radio_celdas + 1)]
    datos = _get(GLOFAS, {"latitude": ",".join(str(a) for a, _ in pares), "longitude": ",".join(str(b) for _, b in pares),
                          "daily": "river_discharge", "past_days": dias_pasados, "forecast_days": dias_pronostico})
    respuesta = datos.json()
    return respuesta if isinstance(respuesta, list) else [respuesta]


def clima_open_meteo(lon: float, lat: float, desde: str, hasta: str, pronostico: bool) -> dict:
    url = OPEN_METEO["pronostico" if pronostico else "archivo"]
    return _get(url, {"latitude": lat, "longitude": lon, "start_date": desde, "end_date": hasta,
                      "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum",
                      "timezone": "America/Bogota"}).json()


def clima_power(lon: float, lat: float, desde: str, hasta: str) -> dict:
    return _get(POWER, {"parameters": "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR", "community": "AG", "longitude": lon,
                        "latitude": lat, "start": desde.replace("-", ""), "end": hasta.replace("-", ""),
                        "format": "JSON"}, timeout=120).json()
