"""lugares-mcp — de un NOMBRE de lugar a su ubicación y su límite (geocodificación), sobre geo_mcp_kit.

V5 (validando imagery): «muéstrame el NDVI de Chía en enero de 2025» terminaba en «dibuja el área de
Chía»: el agente no tenía cómo convertir un lugar nombrado en una geometría (`request_map_input` ya
lo prevé: «si tienes un geocodificador, úsalo antes»).

Dos herramientas, para que decida quien pide (el LLM) y no el servidor:
- `lugares_buscar`: los candidatos con sus hechos (qué es, nivel administrativo, dónde, si tiene
  límite). «Chía» es un municipio de Cundinamarca, su casco urbano y un pueblo de Huesca.
- `lugares_limite`: el límite (polígono) del candidato elegido, como capa — con él el NDVI, un
  recorte o un conteo operan sobre el lugar real, no sobre un rectángulo.

Fuente: Nominatim (OpenStreetMap). Su política de uso pide identificarse (User-Agent con contacto),
máximo 1 petición por segundo y guardar en caché: se cumple aquí. En producción, `LUGARES_NOMINATIM_URL`
puede apuntar a un Nominatim propio.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any

import httpx
from geo_mcp_kit import (
    GeoMcpAuth,
    KeyRing,
    RateLimiter,
    ToolRunner,
    feature_collection,
    geo_meta,
    geo_result,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

NOMINATIM = os.environ.get("LUGARES_NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")
USER_AGENT = os.environ.get("LUGARES_USER_AGENT", "geo-copilot/1.0 (lugares-mcp)")
#: segundos mínimos entre peticiones a Nominatim (su política: 1 por segundo como máximo)
ESPACIADO_S = float(os.environ.get("LUGARES_ESPACIADO_S", "1.0"))
TIMEOUT_S = 20.0
#: El límite SIN simplificar. Medido: con polygon_threshold=0.0005 la Localidad de Teusaquillo quedaba en
#: 9 vértices (191 reales) — una caricatura que recortaba mal; 0.00005 aún la deja en 32.
SIMPLIFICACION = 0.0
#: Los CANDIDATOS sí van simplificados (~1 km): una búsqueda de «Colombia» traería el país entero por
#: cada candidato. Por eso su área es aproximada y se llama así (Chía: 85,42 km² simplificado, 80,03 real).
UMBRAL_BUSQUEDA = 0.01
MAX_CANDIDATOS = 10

SCOPES = {"lugares_buscar": "lugares:read", "lugares_limite": "lugares:read"}
runner = ToolRunner(timeout_s=45, service="lugares-mcp")
mcp = FastMCP(
    "lugares-mcp",
    instructions="Geocodificación: de un nombre de lugar (municipio, barrio, ciudad, dirección) a sus "
                 "candidatos y al límite del elegido.",
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


class ErrorLugares(Exception):
    pass


# --- acceso a Nominatim: espaciado, caché e identificación --------------------------------------

_cerrojo = threading.Lock()
_ultima = 0.0
_cache: OrderedDict[tuple, Any] = OrderedDict()
_CACHE_MAX = 512


def _pedir(ruta: str, params: dict[str, Any]) -> Any:
    clave = (ruta, tuple(sorted(params.items())))
    if clave in _cache:
        _cache.move_to_end(clave)
        return _cache[clave]
    global _ultima
    with _cerrojo:  # una petición a la vez y espaciadas (política de uso de Nominatim)
        espera = ESPACIADO_S - (time.monotonic() - _ultima)
        if espera > 0:
            time.sleep(espera)
        try:
            r = httpx.get(f"{NOMINATIM}{ruta}", params=params, headers={"User-Agent": USER_AGENT},
                          timeout=TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise ErrorLugares(f"el geocodificador no respondió: {type(exc).__name__}") from exc
        finally:
            _ultima = time.monotonic()
    if r.status_code != 200:
        raise ErrorLugares(f"el geocodificador respondió HTTP {r.status_code}")
    datos = r.json()
    _cache[clave] = datos
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return datos


def _ref(x: dict) -> str:
    return f"{str(x.get('osm_type') or '?')[:1].upper()}{x.get('osm_id')}"


def _bbox(x: dict) -> list[float] | None:
    b = x.get("boundingbox")  # [sur, norte, oeste, este] como texto
    try:
        s, n, w, e = (float(v) for v in b)
    except (TypeError, ValueError):
        return None
    return [w, s, e, n]


def _candidato(x: dict) -> dict[str, Any]:
    geom = (x.get("geojson") or {}).get("type")
    direccion = x.get("address") or {}
    return {
        "ref": _ref(x),
        "nombre": x.get("display_name"),
        "que_es": f"{x.get('category')}/{x.get('type')}",
        "nivel": x.get("addresstype"),
        "pais": direccion.get("country"),
        "codigo_pais": direccion.get("country_code"),
        "tiene_limite": geom in ("Polygon", "MultiPolygon"),
        # el tamaño distingue un municipio o una localidad de un barrio o de una agrupación parcial.
        # APROXIMADA: sale del límite simplificado de la búsqueda (UMBRAL_BUSQUEDA). Llamada `area_km2`,
        # el agente la daba como el área del lugar (F0, verdades: Chía 85,42 km², son 80,03).
        **({"area_km2_aprox": _area_km2(x["geojson"])} if geom in ("Polygon", "MultiPolygon") else {}),
        "centro": [round(float(x["lon"]), 6), round(float(x["lat"]), 6)] if x.get("lon") else None,
        "bbox": _bbox(x),
    }


def _area_km2(geom: dict) -> float | None:
    try:
        from pyproj import Geod
        from shapely.geometry import shape

        g = shape(geom)
        area, _ = Geod(ellps="WGS84").geometry_area_perimeter(g)
        return round(abs(area) / 1e6, 2)
    except Exception:  # noqa: BLE001 — el área es un hecho de cortesía; sin ella el límite sirve igual
        return None


# --- herramientas --------------------------------------------------------------------------------

@mcp.tool(
    description="Busca un lugar por su NOMBRE (municipio, ciudad, barrio, localidad, vereda, dirección) y "
                "devuelve los candidatos con sus hechos: qué es, nivel administrativo, país, si tiene límite "
                "(polígono), su área, centro y extensión. Un nombre suele ser ambiguo (hay varios «Chía»): elige el "
                "candidato por sus hechos y pide su límite con `lugares_limite(ref)`. Para una división "
                "administrativa nómbrala con su tipo y su ciudad («Localidad Teusaquillo, Bogotá», «Comuna 14, "
                "Medellín»): solo «Teusaquillo» devuelve una UPZ y una agrupación parcial, no la localidad. "
                "`codigo_pais` (ISO, p. ej. «co») acota la búsqueda a un país.",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=[], cost="low"),
    structured_output=True,
)
def lugares_buscar(nombre: str, codigo_pais: str | None = None, limite: int = 5) -> dict[str, Any]:
    def buscar() -> dict:
        texto = (nombre or "").strip()
        if not texto or len(texto) > 200:
            return {"error": "falta el nombre del lugar (máximo 200 caracteres)"}
        params: dict[str, Any] = {"q": texto, "format": "jsonv2", "limit": max(1, min(int(limite), MAX_CANDIDATOS)),
                                  "polygon_geojson": 1, "polygon_threshold": UMBRAL_BUSQUEDA, "addressdetails": 1,
                                  "accept-language": "es"}
        if codigo_pais:
            if not re.fullmatch(r"[A-Za-z]{2}(,[A-Za-z]{2})*", codigo_pais):
                return {"error": "codigo_pais debe ser ISO de 2 letras (p. ej. «co»)"}
            params["countrycodes"] = codigo_pais.lower()
        try:
            datos = _pedir("/search", params)
        except ErrorLugares as exc:
            return {"error": str(exc)}
        candidatos = [_candidato(x) for x in datos if isinstance(x, dict)]
        nota = ("el área de los candidatos es APROXIMADA (límite simplificado, puede desviarse un 10 %): "
                "sirve para distinguirlos; el área del lugar la da lugares_limite"
                if candidatos else "ningún lugar con ese nombre")
        return geo_result([], facts={"buscado": texto, "candidatos": candidatos, "nota": nota})

    return runner.run(buscar)


def _sin_tildes(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _division_que_lo_contiene(x: dict) -> str | None:
    """La división que CONTIENE al lugar y cuyo nombre está dentro del suyo: «UPZs Localidad Chapinero»
    está en «Localidad Chapinero». F3: el agente usaba ese límite y lo llamaba «la localidad»."""
    partes = [p.strip() for p in str(x.get("display_name") or "").split(",")]
    if len(partes) < 2:
        return None
    propio = _sin_tildes(partes[0])
    return next((p for p in partes[1:] if _sin_tildes(p) != propio and _sin_tildes(p) in propio), None)


@mcp.tool(
    description="El límite (polígono) de un lugar elegido de `lugares_buscar`, por su `ref` (p. ej. «R10687625»), "
                "como capa en EPSG:4326, con su área. Con esa capa otras herramientas operan sobre el lugar "
                "(NDVI, recortes, conteos). Si el lugar es solo un punto, devuelve el punto y lo dice.",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["feature_collection"], cost="low"),
    structured_output=True,
)
def lugares_limite(ref: str) -> dict[str, Any]:
    def limite() -> dict:
        m = re.fullmatch(r"([NWR])(\d{1,12})", (ref or "").strip().upper())
        if not m:
            return {"error": "ref no válida: usa la de un candidato de lugares_buscar (p. ej. «R10687625»)"}
        try:
            datos = _pedir("/lookup", {"osm_ids": m.group(0), "format": "jsonv2", "polygon_geojson": 1,
                                       "polygon_threshold": SIMPLIFICACION, "addressdetails": 1,
                                       "accept-language": "es"})
        except ErrorLugares as exc:
            return {"error": str(exc)}
        x = next((d for d in datos or [] if isinstance(d, dict) and d.get("geojson")), None)
        if x is None:
            return {"error": f"no se encontró el lugar {m.group(0)}"}
        geom = x["geojson"]
        es_area = geom.get("type") in ("Polygon", "MultiPolygon")
        nombre = str(x.get("name") or x.get("display_name") or m.group(0))
        props = {"nombre": nombre, "nombre_completo": x.get("display_name"), "nivel": x.get("addresstype"),
                 "ref": m.group(0)}
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": geom, "properties": props}]}
        hechos: dict[str, Any] = {}
        division = _division_que_lo_contiene(x)
        if division:  # PRIMERO: el resultado llega recortado a quien juzga la respuesta
            hechos["aviso"] = (f"este límite es «{nombre}», una PARTE de «{division}» (que la contiene), no "
                               f"«{division}»: no lo nombres «{division}». Si se pidió «{division}», búscala "
                               "con lugares_buscar por ese nombre (y su ciudad) y usa SU límite")
        hechos.update({"lugar": x.get("display_name"), "nivel": x.get("addresstype"),
                       "geometria": geom.get("type"), "bbox": _bbox(x)})
        if es_area:
            hechos["area_km2"] = _area_km2(geom)
        else:
            hechos["nota"] = "este lugar es un punto (sin límite): para un área usa otro candidato o un radio"
        # un límite es un área de interés: se dibuja como contorno, para no tapar lo que se calcule dentro
        return geo_result([feature_collection(nombre if es_area else f"{nombre} (punto)", fc, crs="EPSG:4326")],
                          facts=hechos, style_hint={"solo_contorno": True} if es_area else None)

    return runner.run(limite)


def build_app():
    keys = KeyRing.from_json(os.environ.get("LUGARES_MCP_KEYS", "[]"), known_scopes={"lugares:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("lugares-mcp no arranca sin claves (LUGARES_MCP_KEYS)")
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="lugares-mcp")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("LUGARES_MCP_PORT", "9700")))
