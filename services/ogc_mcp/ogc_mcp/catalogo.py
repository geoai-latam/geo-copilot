"""Catálogo curado de servicios OGC de Colombia (IGAC, Parques Nacionales, SGC, DANE…).

Vive en `catalogo.yaml` junto a este módulo; `OGC_CATALOGO` puede apuntar a otro (una organización
con sus propios servicios). Cada entrada se verificó en vivo (GetCapabilities) la fecha que declara.
El servidor no elige: devuelve el catálogo (filtrado por texto si se pide) y el LLM decide cuál sirve.
"""

from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RUTA_DEFECTO = Path(__file__).with_name("catalogo.yaml")


def _plano(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=1)
def cargar() -> list[dict[str, Any]]:
    ruta = Path(os.environ.get("OGC_CATALOGO") or RUTA_DEFECTO)
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    entradas = datos.get("servicios") or []
    for e in entradas:
        if e.get("verificado") is not None:
            e["verificado"] = str(e["verificado"])  # YAML la lee como fecha; la respuesta es JSON
        faltan = {"id", "titulo", "entidad", "temas", "descripcion"} - set(e)
        if faltan or not (e.get("wms") or e.get("wfs") or e.get("ogcapi")):
            raise ValueError(f"entrada del catálogo OGC incompleta: {e.get('id')} (faltan {sorted(faltan)} o URL)")
    return entradas


def wfs_de(wms_url: str) -> str | None:
    """El WFS del mismo servicio, si el catálogo lo conoce (un hecho, no se deriva de la URL)."""
    clave = wms_url.rstrip("/").lower()
    return next((e["wfs"] for e in cargar() if e.get("wms") and e.get("wfs")
                 and e["wms"].rstrip("/").lower() == clave), None)


def buscar(texto: str | None = None, entidad: str | None = None) -> dict[str, Any]:
    """Las entradas (todas, o las que contienen alguna palabra de `texto`, primero las que contienen más),
    con sus URLs por protocolo.

    V5 (ogc-mcp): con «todas las palabras», «departamentos Colombia» del DANE daba 0 (ninguna descripción
    dice «Colombia») y el agente acabó en el servicio equivocado. Cada entrada lleva cuántas coinciden.
    """
    entradas = cargar()
    if entidad:
        ent = _plano(entidad)
        entradas = [e for e in entradas if ent in _plano(e["entidad"]) or ent in _plano(e["id"])]
    coincidencias: dict[str, int] = {}
    if texto:
        palabras = [p for p in _plano(texto).split() if len(p) > 2]
        for e in entradas:
            pajar = _plano(" ".join([e["titulo"], e["entidad"], e["descripcion"], " ".join(e["temas"])]))
            coincidencias[e["id"]] = sum(p in pajar for p in palabras)
        entradas = sorted((e for e in entradas if coincidencias[e["id"]]), key=lambda e: -coincidencias[e["id"]])
    servicios = [{k: e[k] for k in ("id", "titulo", "entidad", "temas", "descripcion", "wms", "wfs", "ogcapi",
                                    "verificado") if e.get(k)}
                  | ({"palabras_que_coinciden": coincidencias[e["id"]]} if texto else {}) for e in entradas]
    hechos: dict[str, Any] = {"servicios": servicios, "encontrados": len(servicios), "en_catalogo": len(cargar())}
    if (texto or entidad) and not servicios:
        hechos["nota"] = ("ninguna entrada del catálogo coincide: prueba sin filtro (el catálogo es corto) o con "
                          "otra palabra; un servicio OGC que no esté aquí se describe con ogc_describir(url)")
    return hechos
