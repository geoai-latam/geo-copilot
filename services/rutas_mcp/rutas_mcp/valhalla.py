"""Cliente de Valhalla (ruteo sobre OpenStreetMap): ruta, isócronas y matriz de tiempos/distancias.

`VALHALLA_URL` es el motor: el autoalojado del compose (perfil `rutas`, teselas de Colombia) o, en
desarrollo, la instancia pública de FOSSGIS (valhalla1.openstreetmap.de; política de uso: poco tráfico).
La URL es configuración del despliegue (no viene del LLM): se pide con httpx directo, no con el cliente SSRF
(el autoalojado vive en la red interna del compose).

Medido el 2026-10-15 (FOSSGIS, Valhalla 3.9.1): Plaza de Bolívar → Chía en carro 33,09 km (OSRM, otro
motor: 33,94 km; los TIEMPOS difieren — 72 contra 40 min — porque cada motor modela las velocidades a su
manera: se dicen como estimación sin tráfico en vivo); isócrona de 15 min en carro desde la Plaza: 22,1 km².
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

VALHALLA = os.environ.get("VALHALLA_URL", "https://valhalla1.openstreetmap.de").rstrip("/")
USER_AGENT = os.environ.get("RUTAS_USER_AGENT", "geo-copilot/1.0 (rutas-mcp)")
TIMEOUT_S = 90.0
MODOS = {"auto": "auto", "a_pie": "pedestrian", "bicicleta": "bicycle", "camion": "truck", "moto": "motorcycle"}


class ErrorRutas(Exception):
    """El motor no pudo responder (mensaje honesto para el LLM)."""


def costo(modo: str) -> str:
    if modo not in MODOS:
        raise ErrorRutas(f"modo «{modo}» no existe; los que hay: {', '.join(MODOS)}")
    return MODOS[modo]


def pedir(accion: str, cuerpo: dict[str, Any]) -> dict[str, Any]:
    """POST al motor; un fallo de red se reintenta una vez; el error del motor se devuelve en palabras."""
    for intento in range(2):
        try:
            r = httpx.post(f"{VALHALLA}/{accion}", json=cuerpo, timeout=TIMEOUT_S,
                           headers={"User-Agent": USER_AGENT})
            break
        except httpx.TransportError as exc:
            if intento == 1:
                raise ErrorRutas(f"el motor de rutas no respondió ({type(exc).__name__})") from exc
            time.sleep(2.0)
    datos = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        # Valhalla explica el fallo («No path could be found», «Location is unreachable»…): es un hecho
        raise ErrorRutas(f"el motor no pudo: {datos.get('error') or r.text[:200]} (código {datos.get('error_code')})")
    return datos


def decodificar(polilinea: str, precision: int = 6) -> list[list[float]]:
    """Polilínea codificada de Valhalla (precisión 6) → [[lon, lat], …]."""
    coords, indice, lat, lon, factor = [], 0, 0, 0, 10 ** precision
    while indice < len(polilinea):
        for eje in (0, 1):
            desplazamiento, resultado = 0, 0
            while True:
                b = ord(polilinea[indice]) - 63
                indice += 1
                resultado |= (b & 0x1F) << desplazamiento
                desplazamiento += 5
                if b < 0x20:
                    break
            delta = ~(resultado >> 1) if resultado & 1 else resultado >> 1
            if eje == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lon / factor, lat / factor])
    return coords


def ruta(puntos: list[tuple[float, float]], modo: str) -> dict[str, Any]:
    cuerpo = {"locations": [{"lon": x, "lat": y} for x, y in puntos], "costing": costo(modo),
              "units": "kilometers", "language": "es-ES"}
    return pedir("route", cuerpo)["trip"]


def isocronas(centro: tuple[float, float], minutos: list[int], modo: str) -> dict[str, Any]:
    cuerpo = {"locations": [{"lon": centro[0], "lat": centro[1]}], "costing": costo(modo),
              "contours": [{"time": m} for m in minutos], "polygons": True}
    return pedir("isochrone", cuerpo)


def matriz(origenes: list[tuple[float, float]], destinos: list[tuple[float, float]], modo: str) -> dict:
    cuerpo = {"sources": [{"lon": x, "lat": y} for x, y in origenes],
              "targets": [{"lon": x, "lat": y} for x, y in destinos], "costing": costo(modo), "units": "kilometers"}
    return pedir("sources_to_targets", cuerpo)
