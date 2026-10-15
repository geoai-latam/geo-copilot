"""Elementos de una capa vectorial OGC (WFS GetFeature / OGC API - Features items) en EPSG:4326.

El filtro va AL servicio (bbox, número máximo) y los hechos dicen cuántos hay allí y cuántos vinieron.

Medido contra servicios reales (2026-10-15, PNN GeoServer e IGAC ArcGIS Server 11.3):
- el GeoJSON sale en lon/lat pidiendo `srsName=EPSG:4326` (la norma GeoJSON); CRS84 NO lo aceptan;
- el `bbox` de WFS 2.0 con `urn:ogc:def:crs:EPSG::4326` va en orden lat/lon (PNN, RUNAP en
  -74.5,4,-73.5,5: 169 con lat/lon, 0 con lon/lat; IGAC curvas: 2319 contra 0).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ogc_mcp import ows

MAX_ELEMENTOS = 50_000
PAGINA = 1000
PAGINA_MINIMA = 60
MAX_BYTES_TOTAL = 80_000_000


def _bbox_valido(bbox: list[float] | None) -> list[float] | None:
    if bbox is None:
        return None
    if len(bbox) != 4:
        raise ows.ErrorOgc("bbox debe ser [minx, miny, maxx, maxy] en EPSG:4326")
    x1, y1, x2, y2 = (float(v) for v in bbox)
    if not (-180 <= x1 < x2 <= 180 and -90 <= y1 < y2 <= 90):
        raise ows.ErrorOgc("bbox fuera de rango o invertido (lon -180..180, lat -90..90, min < max)")
    return [x1, y1, x2, y2]


def _capa_wfs(caps: dict, capa: str) -> dict:
    exacta = next((c for c in caps["capas"] if c["nombre"] == capa), None)
    if exacta:
        return exacta
    # «runap» por «pnn:runap»: el prefijo del espacio de nombres es parte del nombre en el servicio
    sufijo = [c for c in caps["capas"] if (c["nombre"] or "").split(":")[-1] == capa.split(":")[-1]]
    if len(sufijo) == 1:
        return sufijo[0]
    nombres = ", ".join(c["nombre"] for c in caps["capas"][:30])
    raise ows.ErrorOgc(f"la capa «{capa}» no está en el servicio; las que hay: {nombres}")


def _contar_wfs(url: str, capa: str, bbox_param: str | None) -> int | None:
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": capa,
              "resultType": "hits"}
    if bbox_param:
        params["bbox"] = bbox_param
    try:
        r = ows.pedir(url, params)
    except ows.ErrorOgc:
        return None  # contar es un hecho de cortesía; sin él, «vinieron N» sigue siendo cierto
    m = re.search(r'numberMatched="(\d+)"', r.text)
    return int(m.group(1)) if m else None


def _pagina_wfs(url: str, capa: str, formato: str, bbox_param: str | None, inicio: int,
                n: int) -> tuple[list[dict], int]:
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": capa, "count": n,
              "startIndex": inicio, "outputFormat": formato, "srsName": "EPSG:4326"}
    if bbox_param:
        params["bbox"] = bbox_param
    r = ows.pedir(url, params)
    try:
        datos = r.json()
    except ValueError as exc:
        raise ows.ErrorOgc(f"el WFS no devolvió GeoJSON: {r.text[:200]}") from exc
    if not isinstance(datos, dict) or not isinstance(datos.get("features"), list):
        raise ows.ErrorOgc("el WFS devolvió un GeoJSON sin `features`")
    return datos["features"], len(r.content)


def _paginar_wfs(url: str, capa: str, formato: str, bbox_param: str | None, maximo: int) -> tuple[list, int | None]:
    """(elementos, bytes al cortar por peso o None). Una página demasiado pesada se pide en mitades.

    V5 (ogc-mcp): 1000 polígonos del RUNAP pasaban de 40 MB en UNA página y la herramienta fallaba entera;
    el país completo pesa ~110 MB. Se pide en páginas menores y se para al llegar a MAX_BYTES_TOTAL
    (la memoria del servidor): lo que vino se dice incompleto, no se inventa.
    """
    features: list[dict] = []
    peso, n_pagina = 0, PAGINA
    while len(features) < maximo:
        n = min(n_pagina, maximo - len(features))
        try:
            pagina, bytes_pagina = _pagina_wfs(url, capa, formato, bbox_param, len(features), n)
        except ows.RespuestaGrande as exc:
            if n_pagina <= PAGINA_MINIMA:
                raise ows.RespuestaGrande(
                    f"{exc}: ni {n_pagina} elementos caben (geometrías muy detalladas). Acota con `area` "
                    "(p. ej. el límite del lugar) o un `bbox` menor") from exc
            n_pagina //= 2
            continue
        features += pagina
        peso += bytes_pagina
        if len(pagina) < n:
            return features, None
        if peso > MAX_BYTES_TOTAL:
            return features, peso
    return features, None


def traer_wfs(url: str, capa: str, bbox: list[float] | None, maximo: int) -> tuple[dict, dict[str, Any]]:
    caps = ows.capacidades_wfs(url)
    ft = _capa_wfs(caps, capa)
    formato = caps.get("formato_geojson")
    if not formato:
        raise ows.ErrorOgc("este WFS no ofrece GeoJSON; no se puede traer como capa")
    bb = _bbox_valido(bbox)
    # WFS 2.0 + urn EPSG:4326 = orden lat/lon (medido: ver el docstring)
    bbox_param = f"{bb[1]},{bb[0]},{bb[3]},{bb[2]},urn:ogc:def:crs:EPSG::4326" if bb else None
    maximo = max(1, min(int(maximo), MAX_ELEMENTOS))
    total = _contar_wfs(caps["url"], ft["nombre"], bbox_param)
    features, corte_mb = _paginar_wfs(caps["url"], ft["nombre"], formato, bbox_param, maximo)
    # V5 (ogc-mcp): «¿cuántos departamentos tiene Colombia?» se filtró por la zona visible (2); el total
    # de la capa entera es el hecho que responde esa pregunta, y cuesta un conteo.
    sin_filtro = _contar_wfs(caps["url"], ft["nombre"], None) if bbox_param else None
    hechos = _hechos(ft, caps, features, total, maximo, bb, sin_filtro)
    if corte_mb is not None:  # 0 MB también es un corte (V5: `if corte_mb` lo perdía)
        hechos["corte_por_peso"] = (f"se paró al llegar a {corte_mb / 1e6:.1f} MB (límite del servidor): para el resto, "
                                    "acota con `area` o `bbox`")
        # subir max_elementos no sirve si el límite fue el peso (V5: el aviso lo sugería igual)
        hechos["aviso"] = (f"vinieron {hechos['elementos']} de {total if total is not None else '?'}: la capa pesa "
                           "demasiado para traerla entera. No es la capa completa: acota con `area` (p. ej. el "
                           "límite del lugar) o `bbox`")
    return {"type": "FeatureCollection", "features": features[:maximo]}, hechos


def _siguiente(datos: dict, raiz: str) -> str | None:
    """El enlace `next` de una página de OGC API, solo si es del mismo host (no se sigue a otro)."""
    for enlace in datos.get("links") or []:
        if isinstance(enlace, dict) and enlace.get("rel") == "next" and enlace.get("href"):
            if urlparse(enlace["href"]).hostname == urlparse(raiz).hostname:
                return str(enlace["href"])
    return None


def traer_ogcapi(url: str, capa: str, bbox: list[float] | None, maximo: int) -> tuple[dict, dict[str, Any]]:
    caps = ows.capacidades_ogcapi(url)
    col = next((c for c in caps["capas"] if c["nombre"] == capa), None)
    if col is None:
        nombres = ", ".join(str(c["nombre"]) for c in caps["capas"][:30])
        raise ows.ErrorOgc(f"la colección «{capa}» no está en el servicio; las que hay: {nombres}")
    bb = _bbox_valido(bbox)
    maximo = max(1, min(int(maximo), MAX_ELEMENTOS))
    params: dict[str, Any] | None = {"f": "json", "limit": min(PAGINA, maximo)}
    if bb and params is not None:
        params["bbox"] = ",".join(str(v) for v in bb)  # OGC API: CRS84 (lon/lat) por norma
    siguiente: str | None = f"{caps['url']}/collections/{capa}/items"
    features: list[dict] = []
    total = None
    while siguiente and len(features) < maximo:
        datos = ows.pedir(siguiente, params).json()
        if total is None and isinstance(datos.get("numberMatched"), int):
            total = datos["numberMatched"]
        features += datos.get("features") or []
        siguiente, params = _siguiente(datos, caps["url"]), None  # el `next` ya lleva sus parámetros
    hechos = _hechos(col, caps, features, total, maximo, bb)
    return {"type": "FeatureCollection", "features": features[:maximo]}, hechos


def _hechos(capa: dict, caps: dict, features: list, total: int | None, maximo: int,
            bbox: list[float] | None, sin_filtro: int | None = None) -> dict[str, Any]:
    vinieron = min(len(features), maximo)
    hechos: dict[str, Any] = {"capa": capa.get("titulo") or capa["nombre"], "nombre_en_servicio": capa["nombre"],
                              "servicio": caps.get("titulo"), "entidad": caps.get("entidad"),
                              "protocolo": caps["tipo"], "elementos": vinieron}
    if bbox:
        # con filtro, «completa» es del recorte, no de la capa: el nombre lo dice (V5: el agente leyó
        # «completa» con 2 departamentos de la zona visible)
        hechos["filtro_bbox_4326"] = bbox
        if total is not None:
            hechos["en_el_servicio_dentro_del_filtro"] = total
            hechos["completa_dentro_del_filtro"] = vinieron >= total
        if sin_filtro is not None:
            hechos["en_el_servicio_sin_filtro"] = sin_filtro
    elif total is not None:
        hechos["en_el_servicio"] = total
        hechos["completa"] = vinieron >= total
    if total is not None and vinieron < total:
        hechos["aviso"] = (f"vinieron {vinieron} de {total}: no es la capa completa. Para más, sube "
                           f"`max_elementos` (hasta {MAX_ELEMENTOS}) o acota con `bbox`")
    # sin lista de campos: ya van en las filas que ve el LLM, y ocupaban la observación (V5: con el tope de
    # 3 000 caracteres se veían menos filas y el agente inventó nombres)
    return hechos


def _geometria_del_area(area: dict) -> Any:
    """La unión de las geometrías de un FeatureCollection (lo que el núcleo entrega por una capa)."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    feats = area.get("features") if isinstance(area, dict) else None
    geoms = [shape(f["geometry"]) for f in feats or [] if isinstance(f, dict) and f.get("geometry")]
    if not geoms:
        raise ows.ErrorOgc("`area` no trae geometrías: usa una capa de la sesión (p. ej. un límite)")
    return unary_union([g if g.is_valid else g.buffer(0) for g in geoms])


def _recortar_al_area(fc: dict, hechos: dict, geom: Any) -> None:
    """Deja solo los elementos que INTERSECAN el área (el servicio filtró por su rectángulo).

    V5 (ogc-mcp): «áreas protegidas en Bogotá» filtró por el rectángulo del límite y contó Tibaitatá
    (Mosquera) como de Bogotá. Un rectángulo no es el área.
    """
    from shapely.geometry import shape
    from shapely.prepared import prep

    area = prep(geom)
    en_rectangulo = len(fc["features"])
    fc["features"] = [f for f in fc["features"] if f.get("geometry") and area.intersects(shape(f["geometry"]))]
    hechos["elementos"] = len(fc["features"])
    hechos["filtro_area"] = {"en_su_rectangulo": en_rectangulo, "intersecan_el_area": len(fc["features"]),
                             "nota": "solo los que tocan el área (no su rectángulo); un elemento que la toca "
                                     "puede extenderse fuera de ella"}


def _solo_campos(fc: dict, campos: list[str]) -> list[str]:
    """Deja en cada elemento solo los atributos pedidos (sin distinguir mayúsculas); devuelve los usados.

    V5 (ogc-mcp): cada área del RUNAP trae 23 atributos y en la observación del agente cabían 2 de 13;
    con solo los que el agente necesita (nombre, categoría) ve muchas más filas. Lo decide el LLM.
    """
    disponibles = sorted({k for f in fc["features"][:200] for k in (f.get("properties") or {})})
    por_minuscula = {k.lower(): k for k in disponibles}
    faltan = [c for c in campos if c.lower() not in por_minuscula]
    if faltan and fc["features"]:
        raise ows.ErrorOgc(f"campos que la capa no tiene: {', '.join(faltan)}; los suyos: {', '.join(disponibles)}")
    usados = [por_minuscula[c.lower()] for c in campos if c.lower() in por_minuscula]
    for f in fc["features"]:
        props = f.get("properties") or {}
        f["properties"] = {k: props.get(k) for k in usados}
    return usados


def traer(url: str, capa: str, bbox: list[float] | None, maximo: int, tipo: str | None,
          area: dict | None = None, campos: list[str] | None = None) -> tuple[dict, dict]:
    """WFS u OGC API según lo que el servicio sea (por `tipo` o por lo que responda); con `area`, el
    servicio filtra por su rectángulo y aquí se dejan los que la intersecan."""
    t = (tipo or "").lower()
    if not t:
        t = "ogcapi" if "/collections" in url else "wfs"
    if t not in ("wfs", "ogcapi"):
        raise ows.ErrorOgc("los elementos se traen de un WFS o de una OGC API - Features (tipo wfs u ogcapi); "
                           "un WMS es solo imagen: usa ogc_ver_mapa")
    geom = _geometria_del_area(area) if area else None
    if geom is not None:
        bbox = list(geom.bounds)
    fc, hechos = (traer_wfs if t == "wfs" else traer_ogcapi)(url, capa, bbox, maximo)
    if geom is not None:
        _recortar_al_area(fc, hechos, geom)
    if campos:
        hechos["campos_traidos"] = _solo_campos(fc, campos)
    return fc, hechos
