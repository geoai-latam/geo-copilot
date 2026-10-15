"""Qué ofrece un servicio OGC: GetCapabilities de WMS / WFS y `/collections` de OGC API - Features.

Todo sale de la respuesta del servicio (hechos): capas, títulos, extensión en EPSG:4326, CRS, formatos.
Las URLs vienen de fuera (catálogo, usuario, LLM): se piden con el cliente SSRF del kit (IP fijada, sin
redirecciones a otro host). El XML se lee con defusedxml (sin entidades externas).

Medido contra servicios reales (2026-10-15): mapas.igac.gov.co (ArcGIS Server 11.3) tarda hasta ~60 s en
un GetCapabilities; por eso se guarda en caché unos minutos.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from defusedxml import ElementTree
from geo_mcp_kit.red import UrlNoPermitida, cliente

TIMEOUT_S = float(os.environ.get("OGC_TIMEOUT_S", "90"))
USER_AGENT = "geo-copilot-ogc-mcp/1"
MAX_BYTES = 40_000_000
TTL_CACHE_S = 900
MAX_CAPAS = 150
REINTENTOS = 1
ESPERA_REINTENTO_S = 2.0


class ErrorOgc(Exception):
    """El servicio no respondió lo que se esperaba (error honesto para el LLM)."""


class RespuestaGrande(ErrorOgc):
    """La respuesta pasa de MAX_BYTES: quien pagina puede pedir menos de una vez."""


def dominios() -> list[str] | None:
    crudo = os.environ.get("OGC_ALLOWED_DOMAINS", "").strip()
    return [d.strip().lower() for d in crudo.split(",") if d.strip()] or None


# --- HTTP ----------------------------------------------------------------------------------------

_cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_cerrojo = threading.Lock()


def base(url: str) -> str:
    """La URL del servicio sin los parámetros OGC (service/request/version…), conservando los demás."""
    p = urlparse((url or "").strip())
    propios = {"service", "request", "version", "typename", "typenames", "layers", "outputformat", "f"}
    resto = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in propios]
    return urlunparse(p._replace(query=urlencode(resto), fragment=""))


def pedir(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET con el cliente SSRF; error honesto si no responde 200."""
    # Medido: mapas.igac.gov.co corta conexiones de vez en cuando (ConnectTimeout) y a la siguiente
    # responde: un fallo de RED se reintenta una vez; un HTTP de error es la respuesta, no se repite.
    for intento in range(REINTENTOS + 1):
        try:
            with cliente(url, dominios=dominios(), timeout=TIMEOUT_S, user_agent=USER_AGENT) as c:
                r = c.get(url, params=params)
            break
        except UrlNoPermitida:
            raise
        except httpx.TransportError as exc:
            if intento == REINTENTOS:
                raise ErrorOgc(f"el servicio no respondió ({type(exc).__name__})") from exc
            time.sleep(ESPERA_REINTENTO_S)
    if r.status_code != 200:
        raise ErrorOgc(f"el servicio respondió HTTP {r.status_code}")
    if len(r.content) > MAX_BYTES:
        raise RespuestaGrande(f"la respuesta del servicio pasa de {MAX_BYTES // 1_000_000} MB")
    return r


def _en_cache(clave: tuple, producir) -> Any:
    ahora = time.monotonic()
    with _cerrojo:
        if clave in _cache and ahora - _cache[clave][0] < TTL_CACHE_S:
            return _cache[clave][1]
    valor = producir()
    with _cerrojo:
        _cache[clave] = (ahora, valor)
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return valor


# --- XML -----------------------------------------------------------------------------------------

def _tag(el) -> str:
    return el.tag.split("}")[-1] if isinstance(el.tag, str) else ""


def _hijo(el, nombre: str):
    return next((h for h in el if _tag(h) == nombre), None)


def _hijos(el, nombre: str) -> list:
    return [h for h in el if _tag(h) == nombre]


def _texto(el, nombre: str) -> str | None:
    h = _hijo(el, nombre) if el is not None else None
    t = (h.text or "").strip() if h is not None else ""
    return t or None


def _xml(r) -> Any:
    try:
        raiz = ElementTree.fromstring(r.content)
    except Exception as exc:
        raise ErrorOgc("la respuesta no es un XML de capacidades válido") from exc
    if _tag(raiz) in ("ExceptionReport", "ServiceExceptionReport"):
        detalle = " ".join(t.strip() for t in raiz.itertext() if t.strip())[:300]
        raise ErrorOgc(f"el servicio devolvió una excepción OGC: {detalle}")
    return raiz


def _buscar(raiz, nombre: str):
    return next((e for e in raiz.iter() if _tag(e) == nombre), None)


def _corto(texto: str | None, n: int = 240) -> str | None:
    if not texto:
        return None
    return texto if len(texto) <= n else texto[: n - 1] + "…"


def _entidad(raiz) -> str | None:
    # sin `or` entre elementos: un Element sin hijos es falso aunque exista
    for nombre in ("ContactOrganization", "ProviderName"):
        org = _buscar(raiz, nombre)
        if org is not None and (org.text or "").strip():
            return (org.text or "").strip()
    return None


# --- WMS -----------------------------------------------------------------------------------------

def _bbox_wms(capa) -> list[float] | None:
    g = _hijo(capa, "EX_GeographicBoundingBox")
    if g is not None:
        try:
            return [float(_texto(g, k) or "") for k in
                    ("westBoundLongitude", "southBoundLatitude", "eastBoundLongitude", "northBoundLatitude")]
        except ValueError:
            return None
    ll = _hijo(capa, "LatLonBoundingBox")  # WMS 1.1.1
    if ll is not None:
        try:
            return [float(ll.get(k) or "") for k in ("minx", "miny", "maxx", "maxy")]
        except ValueError:
            return None
    return None


def _capas_wms(el, heredado: dict, salida: list) -> None:
    """Recorre el árbol de capas: cada una hereda CRS y extensión de su madre (WMS §7.2.4.8)."""
    crs = heredado["crs"] | {(c.text or "").strip() for c in _hijos(el, "CRS") + _hijos(el, "SRS")}
    bbox = _bbox_wms(el) or heredado["bbox"]
    nombre = _texto(el, "Name")
    if nombre:
        estilos = [_texto(s, "Name") for s in _hijos(el, "Style") if _texto(s, "Name")]
        salida.append({"nombre": nombre, "titulo": _texto(el, "Title"), "resumen": _corto(_texto(el, "Abstract")),
                       "bbox_4326": bbox, "estilos": estilos, "consultable": el.get("queryable") == "1",
                       "_crs": crs})
    for hija in _hijos(el, "Layer"):
        _capas_wms(hija, {"crs": crs, "bbox": bbox}, salida)


def capacidades_wms(url: str) -> dict[str, Any]:
    def producir() -> dict[str, Any]:
        r = pedir(base(url), {"service": "WMS", "request": "GetCapabilities"})
        raiz = _xml(r)
        if _tag(raiz) not in ("WMS_Capabilities", "WMT_MS_Capabilities"):
            raise ErrorOgc(f"no es un WMS (respondió <{_tag(raiz)}>)")
        capas: list[dict] = []
        cap = _hijo(raiz, "Capability")
        for capa in _hijos(cap, "Layer") if cap is not None else []:
            _capas_wms(capa, {"crs": set(), "bbox": None}, capas)
        getmap = _buscar(cap, "GetMap") if cap is not None else None
        formatos = [(f.text or "").strip() for f in _hijos(getmap, "Format")] if getmap is not None else []
        return {"tipo": "WMS", "version": raiz.get("version"), "url": base(url),
                "titulo": _texto(_hijo(raiz, "Service"), "Title"), "entidad": _entidad(raiz),
                "formatos": formatos, "capas": capas}

    return _en_cache(("WMS", base(url)), producir)


# --- WFS -----------------------------------------------------------------------------------------

def _bbox_wfs(ft) -> list[float] | None:
    w = _hijo(ft, "WGS84BoundingBox")
    if w is None:
        return None
    try:
        x1, y1 = (float(v) for v in (_texto(w, "LowerCorner") or "").split())
        x2, y2 = (float(v) for v in (_texto(w, "UpperCorner") or "").split())
    except ValueError:
        return None
    return [x1, y1, x2, y2]


def capacidades_wfs(url: str) -> dict[str, Any]:
    def producir() -> dict[str, Any]:
        r = pedir(base(url), {"service": "WFS", "request": "GetCapabilities", "version": "2.0.0"})
        raiz = _xml(r)
        if _tag(raiz) != "WFS_Capabilities":
            raise ErrorOgc(f"no es un WFS (respondió <{_tag(raiz)}>)")
        capas = []
        for ft in (e for e in raiz.iter() if _tag(e) == "FeatureType"):
            capas.append({"nombre": _texto(ft, "Name"), "titulo": _texto(ft, "Title"),
                          "resumen": _corto(_texto(ft, "Abstract")), "bbox_4326": _bbox_wfs(ft),
                          "crs_defecto": _texto(ft, "DefaultCRS") or _texto(ft, "DefaultSRS")})
        valores = {(v.text or "").strip() for v in raiz.iter() if _tag(v) == "Value"}
        geojson = next((f for f in ("application/json", "application/geo+json", "GEOJSON", "json",
                                    "geojson") if f in valores), None)
        return {"tipo": "WFS", "version": raiz.get("version"), "url": base(url),
                "titulo": _texto(_buscar(raiz, "ServiceIdentification"), "Title"), "entidad": _entidad(raiz),
                "formato_geojson": geojson, "capas": capas}

    return _en_cache(("WFS", base(url)), producir)


def campos_wfs(url: str, capa: str) -> list[str]:
    """Los atributos de una capa WFS («nombre (tipo)»), de su DescribeFeatureType; la geometría aparte.

    V5 (ogc-mcp): sin conocerlos, el agente pidió `campos` «nombre, categoría» (son `ap_nombre`,
    `ap_categoria`), recibió el error y reintentó con 8 campos: vio 2 de 13 filas.
    Medido: GeoServer (PNN) declara `type="xsd:int"`; ArcGIS (DANE) anida `<xsd:restriction base=…>`.
    """
    def producir() -> list[str]:
        r = pedir(base(url), {"service": "WFS", "version": "2.0.0", "request": "DescribeFeatureType",
                              "typeNames": capa})
        raiz = _xml(r)
        campos = []
        for el in (e for e in raiz.iter() if _tag(e) == "element" and e.get("name")):
            if el.get("substitutionGroup"):  # la declaración de la capa, no un atributo
                continue
            restr = next((x for x in el.iter() if _tag(x) == "restriction"), None)
            tipo = el.get("type") or (restr.get("base") if restr is not None else "") or ""
            tipo = tipo.split(":")[-1]
            campos.append(f"{el.get('name')} (geometría)" if "Property" in tipo else f"{el.get('name')} ({tipo})")
        return campos

    return _en_cache(("CAMPOS", base(url), capa), producir)


def con_campos(caps: dict[str, Any], hechos: dict[str, Any], maximo: int = 3) -> None:
    """Si el resumen quedó en pocas capas WFS, cada una lleva sus atributos (un fallo no tumba la descripción)."""
    if caps["tipo"] != "WFS" or not 0 < len(hechos["capas"]) <= maximo:
        return
    for c in hechos["capas"]:
        try:
            c["campos"] = campos_wfs(caps["url"], c["nombre"])
        except ErrorOgc as exc:
            c["campos"] = f"no se pudieron leer: {exc}"


# --- OGC API - Features --------------------------------------------------------------------------

def raiz_ogcapi(url: str) -> str:
    """La raíz de la API (lo que va antes de /collections)."""
    u = base(url).rstrip("/")
    return u.split("/collections")[0] if "/collections" in u else u


def capacidades_ogcapi(url: str) -> dict[str, Any]:
    def producir() -> dict[str, Any]:
        r = pedir(f"{raiz_ogcapi(url)}/collections", {"f": "json"})
        try:
            datos = r.json()
        except ValueError as exc:
            raise ErrorOgc("no es una OGC API (la respuesta no es JSON)") from exc
        if not isinstance(datos, dict) or not isinstance(datos.get("collections"), list):
            raise ErrorOgc("no es una OGC API - Features (sin `collections`)")
        capas = []
        for c in datos["collections"]:
            bb = (((c.get("extent") or {}).get("spatial") or {}).get("bbox") or [None])[0]
            capas.append({"nombre": c.get("id"), "titulo": c.get("title"), "resumen": _corto(c.get("description")),
                          "bbox_4326": bb if isinstance(bb, list) and len(bb) == 4 else None})
        return {"tipo": "OGC API - Features", "url": raiz_ogcapi(url), "titulo": datos.get("title"),
                "entidad": None, "capas": capas}

    return _en_cache(("OGCAPI", raiz_ogcapi(url)), producir)


def describir(url: str, tipo: str | None = None) -> dict[str, Any]:
    """Las capacidades del servicio. Sin `tipo`, se prueba lo que la URL declara y, si no, WMS/WFS/OGC API."""
    lectores = {"wms": capacidades_wms, "wfs": capacidades_wfs, "ogcapi": capacidades_ogcapi}
    if tipo:
        if tipo.lower() not in lectores:
            raise ErrorOgc("tipo debe ser wms, wfs u ogcapi")
        return lectores[tipo.lower()](url)
    u = url.lower()
    orden = ["wfs", "wms", "ogcapi"] if ("wfs" in u) else ["ogcapi", "wms", "wfs"] if "collections" in u \
        else ["wms", "wfs", "ogcapi"]
    fallos = []
    for t in orden:
        try:
            return lectores[t](url)
        except ErrorOgc as exc:
            fallos.append(f"{t}: {exc}")
    raise ErrorOgc("el servicio no respondió como WMS, WFS ni OGC API - Features (" + "; ".join(fallos) + ")")


def resumir(caps: dict[str, Any], filtro: str | None = None) -> dict[str, Any]:
    """Hechos para el LLM: las capas (filtradas por texto si son muchas), sin campos internos."""
    capas = caps["capas"]
    if filtro:
        f = filtro.lower()
        capas = [c for c in capas if f in (c.get("nombre") or "").lower() or f in (c.get("titulo") or "").lower()]
    visibles = [{k: v for k, v in c.items() if not k.startswith("_") and v not in (None, [], "")}
                for c in capas[:MAX_CAPAS]]
    hechos = {k: v for k, v in caps.items() if k != "capas" and v not in (None, [], "")}
    hechos.update({"total_capas": len(caps["capas"]), "capas": visibles})
    if filtro:
        hechos["coinciden_con_filtro"] = len(capas)
    if len(capas) > MAX_CAPAS:
        hechos["aviso"] = (f"se muestran {MAX_CAPAS} de {len(capas)} capas: usa `filtro` con una palabra del "
                           "nombre o título para ver las demás")
    return hechos
