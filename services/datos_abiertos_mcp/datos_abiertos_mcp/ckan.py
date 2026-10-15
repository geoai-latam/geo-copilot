"""CKAN (p. ej. Datos Abiertos Bogotá): buscar conjuntos, ver sus recursos y traer los que son capas.

Medido el 2026-10-15 contra datosabiertos.bogota.gov.co: un conjunto trae varios recursos del mismo dato
(Esri REST, WMS, WFS, GeoJSON, GPKG, SHP, KML…). Este servidor descarga GeoJSON y CSV; los servicios
(WFS/WMS/OGC, Esri REST) los consume otro servidor (ogc-mcp, arcgis) — los hechos lo dicen y el LLM decide.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any
from urllib.parse import urlparse

from geo_mcp_kit import red

USER_AGENT = "geo-copilot-datos-abiertos-mcp/1"
MAX_DESCARGA = 60_000_000
SERVICIOS = {"wfs": "ogc-mcp (ogc_traer_elementos, tipo wfs)", "wms": "ogc-mcp (ogc_ver_mapa)",
             "ogc api": "ogc-mcp (tipo ogcapi)", "esri rest": "el servidor arcgis (consultar la capa)",
             "arcgis geoservices rest api": "el servidor arcgis (consultar la capa)"}


class ErrorCkan(red.ErrorRed):
    pass


def _accion(base: str, accion: str, params: dict) -> Any:
    r = red.get(f"{base.rstrip('/')}/api/3/action/{accion}", params, timeout=60, user_agent=USER_AGENT)
    datos = r.json()
    if not datos.get("success"):
        raise ErrorCkan(f"CKAN respondió sin éxito: {str(datos.get('error'))[:200]}")
    return datos["result"]


def _recurso(r: dict) -> dict[str, Any]:
    formato = str(r.get("format") or "").strip()
    url = str(r.get("url") or "")
    uso = SERVICIOS.get(formato.lower())
    if not uso and "wfsserver" in url.lower():
        uso = SERVICIOS["wfs"]
    return {"id": r.get("id"), "nombre": r.get("name"), "formato": formato, "url": url,
            "bytes": r.get("size"),
            "se_trae_con": uso or ("datos_traer_recurso" if formato.lower() in ("geojson", "csv", "json") else
                                   "no se puede traer como capa con estas herramientas")}


def buscar(base: str, texto: str, limite: int = 10) -> list[dict[str, Any]]:
    res = _accion(base, "package_search", {"q": texto, "rows": max(1, min(limite, 25))})
    return [{"id": p.get("name"), "nombre": p.get("title"), "entidad": (p.get("organization") or {}).get("title"),
             "descripcion": " ".join(str(p.get("notes") or "").split())[:240] or None,
             "actualizado": str(p.get("metadata_modified") or "")[:10],
             "recursos": [_recurso(r) for r in p.get("resources") or []][:12]} for p in res.get("results") or []]


def describir(base: str, ident: str) -> dict[str, Any]:
    p = _accion(base, "package_show", {"id": ident})
    return {"id": p.get("name"), "nombre": p.get("title"), "entidad": (p.get("organization") or {}).get("title"),
            "descripcion": " ".join(str(p.get("notes") or "").split())[:800] or None,
            "licencia": p.get("license_title"), "actualizado": str(p.get("metadata_modified") or "")[:10],
            "recursos": [_recurso(r) for r in p.get("resources") or []]}


def descargar(url: str, dominios: list[str]) -> tuple[str, Any]:
    """('geojson', FeatureCollection) o ('csv', filas). Solo de los dominios de los portales configurados."""
    if (urlparse(url).hostname or "") not in dominios:
        raise ErrorCkan(f"el recurso no es de un portal configurado ({', '.join(dominios)})")
    r = red.get(url, None, dominios=dominios, timeout=120, user_agent=USER_AGENT, max_bytes=MAX_DESCARGA)
    tipo = r.headers.get("content-type", "").lower()
    texto = r.content.decode("utf-8-sig", errors="replace")
    if "json" in tipo or url.lower().endswith((".geojson", ".json")) or texto.lstrip().startswith("{"):
        datos = r.json()
        if isinstance(datos, dict) and isinstance(datos.get("features"), list):
            return "geojson", datos
        raise ErrorCkan("el recurso JSON no es un GeoJSON (sin `features`)")
    if "csv" in tipo or url.lower().endswith(".csv"):
        muestra = texto[:5000]
        try:
            dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
        except csv.Error:
            dialecto = csv.excel
        return "csv", list(csv.DictReader(io.StringIO(texto), dialect=dialecto))
    raise ErrorCkan(f"formato no soportado para traer como capa ({tipo or 'desconocido'}): GeoJSON o CSV")


_CRS = re.compile(r"(?:EPSG::?|EPSG:)(\d{4,6})$|CRS84$", re.IGNORECASE)


def crs_de_geojson(fc: dict) -> str:
    """El CRS que DECLARA el GeoJSON (miembro `crs`, GeoJSON 2008) o EPSG:4326 si no declara (RFC 7946).

    Medido (Datos Abiertos Bogotá, colegios privados 2024): `urn:ogc:def:crs:EPSG::3857`, coordenadas en
    metros. Etiquetarlo EPSG:4326 ponía los 1.143 colegios fuera del mapa. Sin declaración, las coordenadas
    tienen que estar en rango de lon/lat: si no, se dice; no se adivina la proyección.
    """
    nombre = str(((fc.get("crs") or {}).get("properties") or {}).get("name") or "").strip()
    if nombre:
        m = _CRS.search(nombre)
        if not m:
            raise ErrorCkan(f"el GeoJSON declara un CRS que no se reconoce: {nombre}")
        return "EPSG:4326" if m.group(1) is None else f"EPSG:{m.group(1)}"
    for f in fc.get("features", [])[:50]:
        nums = re.findall(r"-?\d+(?:\.\d+)?", json.dumps((f.get("geometry") or {}).get("coordinates")))[:2]
        if len(nums) == 2 and not (-180 <= float(nums[0]) <= 180 and -90 <= float(nums[1]) <= 90):
            raise ErrorCkan("el GeoJSON no declara CRS y sus coordenadas no son lon/lat (fuera de rango): "
                            "no se puede ubicar sin saber su proyección")
    return "EPSG:4326"
