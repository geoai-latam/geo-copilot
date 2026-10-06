"""Búsqueda de datasets públicos de ArcGIS: el Hub (API v3) y ArcGIS Online (sharing/rest).

Se consultan LOS DOS y se fusionan sin duplicados: ninguno basta solo (medido con búsquedas reales).
ArcGIS Online filtra por zona EN EL SERVIDOR y trae muchos más candidatos, incluidas las fuentes
oficiales (Movilidad, IDECA, RUNAP, ANT…). El Hub v3 ignora `filter[extent]` (aquí se aplica el bbox
sobre los primeros resultados mundiales, que a veces dejan casi nada), pero con palabras cortas
encuentra cuentas institucionales que ArcGIS Online deja abajo.

Reglas del Hub v3 aprendidas en producción:
- `q=` solo es ruidoso: conviene combinarlo con tags/owner/tipos (lo decide quien busca).
- `filter[extent]` lo IGNORA la API: el bbox se aplica aquí sobre el `extent` de cada item.
- `page[size]` máximo 20: se pagina hasta `max_results`.

Aquí no se ordena por autoridad ni se decide qué es «oficial»: cada item lleva sus HECHOS (créditos,
dueño, vistas, completitud de los metadatos, fecha, si es de una sola capa, qué buscador lo halló) y
quien elige es el cliente (el LLM, o la persona en el panel).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from arcgis_mcp.red import cliente

logger = logging.getLogger("arcgis_mcp.hub")

HUB_SEARCH_URL = "https://opendata.arcgis.com/api/v3/search"
HUB_DOMINIOS = ["opendata.arcgis.com"]
HUB_PAGE_SIZE_MAX = 20
ONLINE_SEARCH_URL = "https://www.arcgis.com/sharing/rest/search"
ONLINE_DOMINIOS = ["www.arcgis.com"]
ONLINE_PAGE_SIZE_MAX = 100
TIPOS_DE_SERVICIO = ("Feature Service", "Feature Layer", "Map Service", "Image Service")
# en ArcGIS Online una «Feature Layer» del Hub es un item de tipo «Feature Service»
_TIPO_ONLINE = {"Feature Service": "Feature Service", "Feature Layer": "Feature Service",
                "Map Service": "Map Service", "Image Service": "Image Service"}


def tipo_de_servicio(type_raw: str, url: str) -> str:
    """FeatureServer | MapServer | ImageServer | Other (la URL manda cuando es contundente)."""
    u = (url or "").lower()
    for clave, tipo in (("featureserver", "FeatureServer"), ("imageserver", "ImageServer"),
                        ("mapserver", "MapServer")):
        if clave in u:
            return tipo
    t = (type_raw or "").lower()
    if any(k in t for k in ("web map", "web mapping", "dashboard", "story map", "application", "package",
                            "document", "pdf")):
        return "Other"
    if "feature" in t:
        return "FeatureServer"
    if "image" in t:
        return "ImageServer"
    if "map" in t:
        return "MapServer"
    return "Other"


def bbox_intersecta(extent: list[float] | None, bbox: list[float]) -> bool:
    """¿El item es DE esa zona? Sin extent, no se sabe (fuera); global o continental, tampoco.

    El umbral 200× deja pasar items de nivel país (Colombia ≈ 66× Cundinamarca) y descarta los
    continentales (EE. UU. ≈ 375×).
    """
    if not extent or len(extent) != 4:
        return False
    ix0, iy0, ix1, iy1 = extent
    bx0, by0, bx1, by1 = bbox
    if ix1 < bx0 or ix0 > bx1 or iy1 < by0 or iy0 > by1:
        return False
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    bw, bh = max(0.001, bx1 - bx0), max(0.001, by1 - by0)
    if (iw > 180 or ih > 90) and (bw < 60 and bh < 60):
        return False
    return not (iw * ih > bw * bh * 200)


def _extent(attrs: dict[str, Any]) -> list[float] | None:
    ext = attrs.get("extent")
    if isinstance(ext, dict):
        coords = ext.get("coordinates")
        if isinstance(coords, list) and len(coords) == 2:
            try:
                (x0, y0), (x1, y1) = coords
                return [float(x0), float(y0), float(x1), float(y1)]
            except (TypeError, ValueError):
                return None
    if isinstance(ext, list) and len(ext) == 4:
        try:
            return [float(v) for v in ext]
        except (TypeError, ValueError):
            return None
    return None


def _id(attrs: dict[str, Any], url: str) -> str:
    servidor = (attrs.get("serverURL") or attrs.get("url") or url or "").strip()
    capa = attrs.get("layerId")
    crudo = f"{servidor}#{capa}" if capa is not None else servidor
    return hashlib.sha1((crudo or str(attrs)).encode("utf-8")).hexdigest()[:16]


def texto_plano(html: str | None, maximo: int = 1200) -> str:
    """La descripción del Hub viene en HTML con estilos en línea: en las tarjetas se veía el marcado
    («<p><span style="font-family:&quot;Avenir…») y al LLM le llegaba CSS en vez de la descripción
    (auditoría pre-producción, V5). Texto plano, entidades resueltas, espacios colapsados."""
    import html as _html
    import re as _re

    sin_tags = _re.sub(r"<(script|style)\b.*?</\1>|<[^>]+>", " ", html or "", flags=_re.S | _re.I)
    return " ".join(_html.unescape(sin_tags).split())[:maximo]


def normalizar(item: dict[str, Any]) -> dict[str, Any] | None:
    """Un item del Hub en el formato del cliente; None si no tiene URL utilizable."""
    attrs = item.get("attributes") or {}
    url = (attrs.get("url") or attrs.get("serverURL") or "").strip()
    if not url:
        return None
    type_raw = attrs.get("type") or ""
    try:
        capa = int(attrs["layerId"]) if attrs.get("layerId") is not None else None
    except (TypeError, ValueError):
        capa = None
    owner = attrs.get("owner") or ""
    fuente = attrs.get("source") or ""
    tags = attrs.get("tags") if isinstance(attrs.get("tags"), list) else []
    item_id = attrs.get("itemId") or item.get("id")
    return {
        "id": _id(attrs, url),
        "source": "hub",
        "org": fuente or owner or "Desconocido",
        "title": attrs.get("name") or attrs.get("title") or "(sin nombre)",
        "description": texto_plano(attrs.get("description") or attrs.get("snippet")),
        "service_type": tipo_de_servicio(type_raw, url),
        "service_url": url,
        "layer_id": capa,
        "owner": owner,
        "source_field": fuente,
        "tags": [str(t) for t in tags],
        "type_raw": type_raw,
        # el Hub v3 da a veces la fecha como epoch en ms: en la aprobación salía «1784823664»
        "modified": _fecha_hub(attrs.get("modified")),
        "created": _fecha_hub(attrs.get("created")),
        "thumbnail_url": (f"https://www.arcgis.com/sharing/rest/content/items/{item_id}/info/{attrs['thumbnail']}"
                          if item_id and attrs.get("thumbnail") else None),
        "extent": _extent(attrs),
        "hub_url": f"https://www.arcgis.com/home/item.html?id={item_id}" if item_id else None,
        "sources": ["hub"],
    }


def _fecha_hub(valor: Any) -> str | None:
    """ISO tal cual; epoch (ms) a fecha ISO."""
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
        return _fecha_iso(valor)
    return valor or None


def _fecha_iso(ms: Any) -> str | None:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def normalizar_online(x: dict[str, Any]) -> dict[str, Any] | None:
    """Un resultado de ArcGIS Online (sharing/rest/search) en el mismo formato, con sus hechos."""
    url = (x.get("url") or "").strip()
    if not url:
        return None
    ultimo = url.rstrip("/").rsplit("/", 1)[-1]
    capa = int(ultimo) if ultimo.isdigit() else None
    extent = None
    ext = x.get("extent")
    if isinstance(ext, list) and len(ext) == 2:
        try:
            (x0, y0), (x1, y1) = ext
            extent = [float(x0), float(y0), float(x1), float(y1)]
        except (TypeError, ValueError):
            extent = None
    creditos = texto_plano(x.get("accessInformation"), 160)
    owner = x.get("owner") or ""
    item_id = x.get("id")
    claves = x.get("typeKeywords") if isinstance(x.get("typeKeywords"), list) else []
    vistas = x.get("numViews")
    completitud = x.get("scoreCompleteness")
    return {
        "id": _id({"url": url, "layerId": capa}, url),
        "source": "arcgis_online",
        "org": creditos or owner or "Desconocido",
        "title": x.get("title") or x.get("name") or "(sin nombre)",
        "description": texto_plano(x.get("snippet") or x.get("description")),
        "service_type": tipo_de_servicio(x.get("type") or "", url),
        "service_url": url,
        "layer_id": capa,
        "owner": owner,
        "source_field": "",
        "tags": [str(t) for t in (x.get("tags") or [])][:15],
        "type_raw": x.get("type") or "",
        "modified": _fecha_iso(x.get("modified")),
        "created": _fecha_iso(x.get("created")),
        "thumbnail_url": (f"https://www.arcgis.com/sharing/rest/content/items/{item_id}/info/{x['thumbnail']}"
                          if item_id and x.get("thumbnail") else None),
        "extent": extent,
        "hub_url": f"https://www.arcgis.com/home/item.html?id={item_id}" if item_id else None,
        "sources": ["arcgis_online"],
        # hechos para juzgar la fuente (los juzga quien elige, no este servidor)
        "credits": creditos,
        "views": vistas if isinstance(vistas, int) else None,
        "completeness": completitud if isinstance(completitud, int) else None,
        "single_layer": ("Singlelayer" in claves) if claves else None,
    }


def sin_tildes(texto: str) -> str:
    """El texto sin diacríticos (á→a, ñ→n): ArcGIS Online los compara literalmente."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def consulta_online(*, text_query: str | None, tags_all: list[str] | None, tags_any: list[str] | None,
                    owner_any: list[str] | None, service_types: list[str] | None,
                    modified_after: str | None, only_loadable: bool) -> str | None:
    """La `q` de ArcGIS Online (sintaxis de campos); None si no hay con qué acotar el catálogo mundial.

    `source_any` (la «fuente» del Hub) no existe en ArcGIS Online: esa parte la cubre solo el Hub."""
    partes: list[str] = []
    if text_query:
        # ArcGIS Online distingue tildes (medido): «Malla Vial Bogotá» no encuentra la Malla Vial de la
        # Secretaría de Movilidad, titulada «Bogota D_C» (60 mil vistas); sin tilde sale primera. Se
        # buscan las dos formas.
        llano = sin_tildes(text_query)
        partes.append(f"(({text_query}) OR ({llano}))" if llano != text_query else text_query)
    if tags_all:
        partes.append(" AND ".join(f'tags:"{t}"' for t in tags_all))
    elif tags_any:
        partes.append("(" + " OR ".join(f'tags:"{t}"' for t in tags_any) + ")")
    if owner_any:
        partes.append("(" + " OR ".join(f'owner:"{o}"' for o in owner_any) + ")")
    if not partes:
        return None
    if service_types:
        tipos = sorted({_TIPO_ONLINE[t] for t in service_types})
    else:
        tipos = sorted(set(_TIPO_ONLINE.values())) if only_loadable else []
    if tipos:
        partes.append("(" + " OR ".join(f'type:"{t}"' for t in tipos) + ")")
    if modified_after:
        from datetime import datetime

        try:
            desde = int(datetime.fromisoformat(modified_after).timestamp() * 1000)
            partes.append(f"modified:[{desde:013d} TO 9999999999999]")
        except ValueError:
            pass
    return " ".join(partes)


def parametros(*, text_query: str | None, tags_all: list[str] | None, tags_any: list[str] | None,
               owner_any: list[str] | None, source_any: list[str] | None, service_types: list[str] | None,
               modified_after: str | None, sort: str, pagina: int) -> list[tuple[str, str]]:
    """Parámetros de la API (claves repetibles). Sin `sort` el Hub ordena por relevancia."""
    p: list[tuple[str, str]] = [("page[size]", str(HUB_PAGE_SIZE_MAX)), ("page[number]", str(pagina))]
    if sort:
        p.append(("sort", sort))
    if text_query:
        p.append(("q", text_query))
    if tags_all:
        p.append(("filter[tags]", f"all({','.join(tags_all)})"))
    elif tags_any:
        p.append(("filter[tags]", f"any({','.join(tags_any)})"))
    if owner_any:
        p.append(("filter[owner]", ",".join(owner_any)))
    if source_any:
        p.append(("filter[source]", ",".join(source_any)))
    if service_types:
        p.append(("filter[type]", ",".join(service_types)))
    if modified_after:
        p.append(("filter[modified]", f">{modified_after}"))
    return p


class BusquedaInvalida(ValueError):
    """La búsqueda no se puede hacer así (el mensaje dice cómo corregirla)."""


def _raiz(it: dict[str, Any]) -> str:
    """El servicio (sin el índice de capa ni barra final, en minúsculas)."""
    url = (it.get("service_url") or "").rstrip("/").lower()
    base, _, ultimo = url.rpartition("/")
    return base if ultimo.isdigit() else url


def _mismo(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """El mismo dataset visto por los dos buscadores: el Hub suele dar la capa (…/FeatureServer/0) y
    ArcGIS Online el servicio; si los dos dicen capa y son distintas, son datasets distintos."""
    ca, cb = a.get("layer_id"), b.get("layer_id")
    return _raiz(a) == _raiz(b) and (ca is None or cb is None or ca == cb)


def _fusionar(online: list[dict[str, Any]], hub_v3: list[dict[str, Any]], tope: int) -> list[dict[str, Any]]:
    """Intercala los dos rankings (cada API ordena por su relevancia) sin duplicados. Si un servicio
    sale en los dos, queda una vez, con los hechos de ambos y `sources` con los dos buscadores."""
    salida: list[dict[str, Any]] = []
    por_raiz: dict[str, list[dict[str, Any]]] = {}
    for i in range(max(len(online), len(hub_v3))):
        for lista in (online, hub_v3):
            if i >= len(lista):
                continue
            it = lista[i]
            previo = next((p for p in por_raiz.get(_raiz(it), []) if _mismo(p, it)), None)
            if previo is not None:
                previo["sources"] += [f for f in it.get("sources") or [] if f not in previo["sources"]]
                for campo, valor in it.items():
                    if previo.get(campo) in (None, "", []) and valor not in (None, "", []):
                        previo[campo] = valor
                continue
            if len(salida) < tope:
                copia = dict(it, sources=list(it.get("sources") or []))
                por_raiz.setdefault(_raiz(it), []).append(copia)
                salida.append(copia)
    return salida


def buscar(*, text_query: str | None = None, tags_all: list[str] | None = None,
           tags_any: list[str] | None = None, owner_any: list[str] | None = None,
           source_any: list[str] | None = None, service_types: list[str] | None = None,
           modified_after: str | None = None, bbox: list[float] | None = None, only_loadable: bool = True,
           max_results: int = 50, sort: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """(items, avisos) de los DOS buscadores, fusionados. Una lista corta no es «no hay más»: si un
    buscador cae quedan los resultados del otro, y el aviso lo dice."""
    if not any([text_query, tags_all, tags_any, owner_any, source_any, service_types]):
        raise BusquedaInvalida("pon al menos un filtro (texto, tags, owner, fuente o tipo de servicio): "
                               "sin filtros el Hub devuelve el catálogo mundial entero")
    if service_types:
        malos = [t for t in service_types if t not in TIPOS_DE_SERVICIO]
        if malos:
            raise BusquedaInvalida(f"tipos de servicio no válidos: {malos}; usa {list(TIPOS_DE_SERVICIO)}")
    if bbox is not None and len(bbox) != 4:
        raise BusquedaInvalida("bbox debe ser [minx, miny, maxx, maxy] en EPSG:4326")
    max_results = max(1, min(int(max_results), 200))
    comunes: dict[str, Any] = {"text_query": text_query, "tags_all": tags_all, "tags_any": tags_any,
                               "owner_any": owner_any, "service_types": service_types,
                               "modified_after": modified_after, "bbox": bbox, "only_loadable": only_loadable,
                               "max_results": max_results, "sort": sort}
    # a la vez: un Hub lento (15 s de plazo) no se suma a lo que tarda ArcGIS Online
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="arcgis-buscar") as pool:
        f_online = pool.submit(buscar_online, **comunes)
        f_hub = pool.submit(buscar_hub_v3, **comunes, source_any=source_any)
        online, avisos_online = f_online.result()
        hub_v3, avisos_hub = f_hub.result()
    return _fusionar(online, hub_v3, max_results), avisos_online + avisos_hub


def buscar_online(*, text_query: str | None = None, tags_all: list[str] | None = None,  # noqa: C901
                  tags_any: list[str] | None = None, owner_any: list[str] | None = None,
                  service_types: list[str] | None = None, modified_after: str | None = None,
                  bbox: list[float] | None = None, only_loadable: bool = True, max_results: int = 50,
                  sort: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """ArcGIS Online. El bbox lo aplica el SERVIDOR; aquí, además, se descartan los items globales o
    continentales (como en el Hub: «DE esa zona»)."""
    q = consulta_online(text_query=text_query, tags_all=tags_all, tags_any=tags_any, owner_any=owner_any,
                        service_types=service_types, modified_after=modified_after, only_loadable=only_loadable)
    if q is None:
        return [], []
    params: dict[str, Any] = {"q": q, "num": min(max_results, ONLINE_PAGE_SIZE_MAX), "start": 1, "f": "json"}
    if bbox:
        params["bbox"] = ",".join(str(v) for v in bbox)
    if sort and sort.lstrip("-") == "modified":
        params.update(sortField="modified", sortOrder="desc" if sort.startswith("-") else "asc")
    items: list[dict[str, Any]] = []
    vistos: set[str] = set()
    try:
        with cliente(ONLINE_SEARCH_URL, dominios=ONLINE_DOMINIOS, timeout=15) as http:
            for _ in range(3):  # hasta 3 páginas: con el bbox en el servidor, más es ruido
                r = http.get(ONLINE_SEARCH_URL, params=params)
                r.raise_for_status()
                datos = r.json()
                if isinstance(datos, dict) and "error" in datos:
                    raise ValueError(str((datos.get("error") or {}).get("message") or datos["error"]))
                for crudo in datos.get("results") or []:
                    it = normalizar_online(crudo) if isinstance(crudo, dict) else None
                    if it is None or (only_loadable and it["service_type"] == "Other"):
                        continue
                    if bbox and not bbox_intersecta(it["extent"], bbox):
                        continue
                    if it["id"] in vistos:
                        continue
                    vistos.add(it["id"])
                    items.append(it)
                    if len(items) >= max_results:
                        return items, []
                siguiente = datos.get("nextStart")
                if not isinstance(siguiente, int) or siguiente <= 0:
                    break
                params["start"] = siguiente
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.warning("búsqueda en ArcGIS Online falló: %s", exc)
        return items, [f"la búsqueda en ArcGIS Online falló ({type(exc).__name__}): "
                       "los resultados pueden venir solo del Hub"]
    return items, []


def buscar_hub_v3(*, text_query: str | None = None, tags_all: list[str] | None = None,
                  tags_any: list[str] | None = None, owner_any: list[str] | None = None,
                  source_any: list[str] | None = None, service_types: list[str] | None = None,
                  modified_after: str | None = None, bbox: list[float] | None = None, only_loadable: bool = True,
                  max_results: int = 50, sort: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """El Hub v3. Los avisos dicen qué páginas fallaron: una lista corta no es «no hay más»."""
    if not any([text_query, tags_all, tags_any, owner_any, source_any, service_types]):
        return [], []
    if sort is None:
        sort = "" if text_query else "-modified"
    max_results = max(1, min(int(max_results), 200))
    items: list[dict[str, Any]] = []
    vistos: set[str] = set()
    avisos: list[str] = []
    with cliente(HUB_SEARCH_URL, dominios=HUB_DOMINIOS, timeout=15) as http:
        for pagina in range(1, (max_results + HUB_PAGE_SIZE_MAX - 1) // HUB_PAGE_SIZE_MAX + 1):
            try:
                r = http.get(HUB_SEARCH_URL, params=parametros(
                    text_query=text_query, tags_all=tags_all, tags_any=tags_any, owner_any=owner_any,
                    source_any=source_any, service_types=service_types, modified_after=modified_after,
                    sort=sort, pagina=pagina))
                r.raise_for_status()
                datos = r.json().get("data") or []
            except (httpx.HTTPError, ValueError, AttributeError) as exc:
                logger.warning("página %s del Hub falló: %s", pagina, exc)
                avisos.append(f"la página {pagina} del Hub falló ({type(exc).__name__}): puede haber más resultados")
                break
            for crudo in datos:
                it = normalizar(crudo) if isinstance(crudo, dict) else None
                if it is None or (only_loadable and it["service_type"] == "Other"):
                    continue
                if bbox and not bbox_intersecta(it["extent"], bbox):
                    continue
                if it["id"] in vistos:
                    continue
                vistos.add(it["id"])
                items.append(it)
                if len(items) >= max_results:
                    return items, avisos
            if len(datos) < HUB_PAGE_SIZE_MAX:
                break
    return items, avisos
