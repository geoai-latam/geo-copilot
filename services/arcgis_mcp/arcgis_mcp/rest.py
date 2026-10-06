"""Servicios ArcGIS REST: describir y consultar (portado de `connectors/arcgis.py` y `extent_utils`).

- `describir(url)`: una capa (…/FeatureServer/0), un servicio con capas o un ImageServer, con
  su extensión reproyectada a EPSG:4326 y, si es imagen (Map/ImageServer), el descriptor que el
  mapa monta (las teselas las sirve el propio ArcGIS).
- `consultar(url, …)`: los elementos de una capa con el filtro EMPUJADO al servicio (`where`,
  `bbox`, campos), paginados de forma estable (por el campo OID) y respetando el
  `maxRecordCount` del servicio. Si no se trae todo lo que hay, se dice.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from arcgis_mcp.hub import texto_plano
from arcgis_mcp.red import cliente

logger = logging.getLogger("arcgis_mcp.rest")

TAMANO_PAGINA = 2000
# páginas pedidas a la vez cuando se sabe cuántos hay (malla vial de Bogotá: 137 mil líneas en 137
# páginas una tras otra = 91 s). Pocas: es un servicio público ajeno.
PAGINAS_EN_PARALELO = 6
# E2.1 (V3 F5): una capa de 41.033 puntos llegaba cortada a 10.000. Lo grande viaja como archivo
# (server.py), así que el tope es el de una capa entera razonable, no el de una respuesta.
MAX_PAGINAS = 400
MAX_ELEMENTOS = 200_000


class ErrorArcGIS(Exception):
    """Un error que quien llama puede entender o corregir (servicio caído, where inválido…)."""


def tipo_de_url(url: str) -> str:
    u = url.lower()
    if "featureserver" in u:
        return "FeatureServer"
    if "imageserver" in u:
        return "ImageServer"
    if "mapserver" in u:
        return "MapServer"
    return "Unknown"


def es_capa(url: str) -> bool:
    return url.rstrip("/").split("/")[-1].isdigit()


def url_de_capa(url: str) -> str:
    """…/FeatureServer → …/FeatureServer/0 (capa por defecto); con índice, igual."""
    u = url.rstrip("/")
    if es_capa(u):
        return u
    if u.split("/")[-1].lower() in ("featureserver", "mapserver"):
        return f"{u}/0"
    return u


class TiempoAgotado(ErrorArcGIS):
    """El servicio no terminó a tiempo (no es que no exista): con páginas menores suele bastar."""


PAGINA_MINIMA = 50
TIMEOUT_CONSULTA_S = 45.0


def _json(http: httpx.Client, url: str, params: dict | None = None) -> dict:
    try:
        r = http.get(url, params={"f": "json", **(params or {})})
        r.raise_for_status()
        datos = r.json()
    except httpx.HTTPStatusError as exc:
        raise ErrorArcGIS(f"el servicio respondió HTTP {exc.response.status_code}") from exc
    except httpx.TimeoutException as exc:
        # contactó, pero no terminó a tiempo (página pesada): quien pagina puede pedir páginas menores
        raise TiempoAgotado(f"el servicio no respondió a tiempo ({type(exc).__name__})") from exc
    except httpx.HTTPError as exc:
        raise ErrorArcGIS(f"no se pudo contactar el servicio ({type(exc).__name__})") from exc
    except ValueError as exc:
        raise ErrorArcGIS("el servicio no devolvió JSON") from exc
    if not isinstance(datos, dict):
        raise ErrorArcGIS("el servicio devolvió una respuesta inesperada")
    if isinstance(datos.get("error"), dict):
        e = datos["error"]
        detalle = "; ".join(str(d) for d in (e.get("details") or []) if d)[:300]
        raise ErrorArcGIS(f"ArcGIS: {e.get('message', 'error')}" + (f" ({detalle})" if detalle else ""))
    return datos


# ---------------------------------------------------------------------------
# Extensiones → EPSG:4326
# ---------------------------------------------------------------------------


def extent_4326(ext: dict | None) -> list[float] | None:
    """[xmin, ymin, xmax, ymax] en lon/lat, o None si no se puede reproyectar HONESTAMENTE.

    Sin referencia espacial declarada no se adivina la proyección (coords pequeñas de un UTM
    parecían lon/lat y el mapa volaba a otro continente).
    """
    if not isinstance(ext, dict) or ext.get("xmin") is None:
        return None
    sr = ext.get("spatialReference") or ext.get("spatial_reference") or {}
    wkid = (sr.get("latestWkid") or sr.get("wkid")) if isinstance(sr, dict) else sr
    try:
        xmin, ymin, xmax, ymax = (float(ext[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
    except (KeyError, TypeError, ValueError):
        return None
    if not wkid:
        return None
    if int(wkid) == 4326:
        caja = [xmin, ymin, xmax, ymax]
    else:
        try:
            from pyproj import Transformer
            from pyproj.exceptions import CRSError

            try:
                t = Transformer.from_crs(f"EPSG:{wkid}", "EPSG:4326", always_xy=True)
            except CRSError:
                t = Transformer.from_crs(f"ESRI:{wkid}", "EPSG:4326", always_xy=True)
            (x0, y0), (x1, y1) = t.transform(xmin, ymin), t.transform(xmax, ymax)
            caja = [x0, y0, x1, y1]
        except Exception:  # SRID desconocido o coords basura del servicio: sin extensión
            logger.warning("no se reproyectó la extensión (wkid=%s)", wkid, exc_info=True)
            return None
    return caja if extent_valido(caja) else None


def extent_valido(caja: Any) -> bool:
    if not isinstance(caja, (list, tuple)) or len(caja) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(v) for v in caja)
    except (TypeError, ValueError):
        return False
    return -180 <= x0 <= 180 and -180 <= x1 <= 180 and -90 <= y0 <= 90 and -90 <= y1 <= 90


def _union(cajas: list[list[float]]) -> list[float] | None:
    if not cajas:
        return None
    return [min(c[0] for c in cajas), min(c[1] for c in cajas), max(c[2] for c in cajas), max(c[3] for c in cajas)]


# ---------------------------------------------------------------------------
# Describir
# ---------------------------------------------------------------------------


def descriptor_imagen(url: str, extent: list[float] | None) -> dict[str, Any]:
    """Lo que el mapa necesita para montar un Map/ImageServer como capa de imagen."""
    limpia = url.rstrip("/")
    return {
        "type": "imagery",
        "service_url": limpia,
        "export_url": f"{limpia}/exportImage" if "imageserver" in limpia.lower() else None,
        "extent": ({"xmin": extent[0], "ymin": extent[1], "xmax": extent[2], "ymax": extent[3]}
                   if extent else None),
    }


def _contar(http: httpx.Client, capa: str, where: str = "1=1", bbox: list[float] | None = None) -> int | None:
    try:
        return int(_json(http, f"{capa}/query", {"where": where, "returnCountOnly": "true",
                                                 **_filtro_bbox(bbox)}).get("count"))
    except (ErrorArcGIS, TypeError, ValueError):
        return None


def _campos(meta: dict) -> list[dict[str, Any]]:
    return [{"name": f.get("name"), "alias": f.get("alias"), "type": str(f.get("type") or "").replace("esriFieldType", "")}
            for f in (meta.get("fields") or []) if isinstance(f, dict) and f.get("name")][:80]


def describir(url: str) -> dict[str, Any]:
    tipo = tipo_de_url(url)
    limpia = url.rstrip("/")
    with cliente(limpia) as http:
        if tipo == "ImageServer":
            m = _json(http, limpia)
            ext = extent_4326(m.get("extent") or m.get("fullExtent"))
            return {"tipo": "ImageServer", "url": limpia, "nombre": m.get("name") or "ImageServer",
                    "descripcion": texto_plano(m.get("description"), 600), "tipo_pixel": m.get("pixelType"),
                    "bandas": m.get("bandCount"), "tamano_pixel": [m.get("pixelSizeX"), m.get("pixelSizeY")],
                    "extent_4326": ext, "imagen": descriptor_imagen(limpia, ext)}
        if es_capa(limpia):
            m = _json(http, limpia)
            caps = str(m.get("capabilities") or "")
            consultable = "query" in caps.lower() and bool(m.get("geometryType"))
            return {"tipo": tipo, "url": limpia, "es_capa": True, "nombre": m.get("name") or "",
                    "descripcion": texto_plano(m.get("description"), 600),
                    "tipo_geometria": str(m.get("geometryType") or "").replace("esriGeometry", "") or None,
                    "campos": _campos(m), "elementos": _contar(http, limpia) if consultable else None,
                    "extent_4326": extent_4326(m.get("extent")), "consultable": consultable,
                    "max_por_peticion": m.get("maxRecordCount")}
        m = _json(http, limpia)
        capas = [{"id": c.get("id"), "nombre": c.get("name"),
                  "tipo_geometria": str(c.get("geometryType") or "").replace("esriGeometry", "") or None}
                 for c in (m.get("layers") or []) if isinstance(c, dict)]
        ext = extent_4326(m.get("fullExtent") or m.get("initialExtent"))
        if ext is None and tipo == "MapServer":
            # algunos MapServer no publican extensión en la raíz: la de sus primeras capas
            cajas = []
            for c in capas[:3]:
                try:
                    e = extent_4326(_json(http, f"{limpia}/{c['id']}").get("extent"))
                except ErrorArcGIS:
                    continue
                if e:
                    cajas.append(e)
            ext = _union(cajas)
        out: dict[str, Any] = {"tipo": tipo, "url": limpia, "es_capa": False,
                               "nombre": m.get("mapName") or limpia.split("/")[-2],
                               "descripcion": (m.get("serviceDescription") or m.get("description") or "")[:600],
                               "capas": capas[:100], "extent_4326": ext}
        if tipo == "MapServer":
            out["imagen"] = descriptor_imagen(limpia, ext)
        return out


# ---------------------------------------------------------------------------
# Consultar
# ---------------------------------------------------------------------------


def _filtro_bbox(bbox: list[float] | None) -> dict[str, str]:
    if not bbox:
        return {}
    x0, y0, x1, y1 = bbox
    return {"geometry": f"{x0},{y0},{x1},{y1}", "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects"}


def _area_firmada(anillo: list) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(anillo, anillo[1:], strict=False)) / 2.0


def _contiene(anillo: list, punto: list) -> bool:
    x, y = punto[0], punto[1]
    dentro = False
    for (x0, y0), (x1, y1) in zip((p[:2] for p in anillo), (p[:2] for p in anillo[1:]), strict=False):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / ((y1 - y0) or 1e-300) + x0:
            dentro = not dentro
    return dentro


def _poligono(anillos: list) -> dict | None:
    """Anillos esriJSON → Polygon/MultiPolygon GeoJSON.

    En ArcGIS los anillos EXTERIORES van en sentido horario y los huecos en antihorario; un
    registro con varios exteriores es un multipolígono (antes se tomaba todo como un polígono
    con huecos y las islas se dibujaban como agujeros). Salida con la orientación de RFC 7946.
    """
    anillos = [a for a in anillos if isinstance(a, list) and len(a) >= 4]
    if not anillos:
        return None
    exteriores: list[list[list]] = []
    huecos = []
    for a in anillos:
        if _area_firmada(a) < 0:
            exteriores.append([a[::-1]])
        else:
            huecos.append(a)
    if not exteriores:  # orientación no estándar: el primer anillo es el exterior
        return {"type": "Polygon", "coordinates": anillos}
    for h in huecos:
        destino = next((p for p in exteriores if _contiene(p[0], h[0])), exteriores[-1])
        destino.append(h[::-1])
    if len(exteriores) == 1:
        return {"type": "Polygon", "coordinates": exteriores[0]}
    return {"type": "MultiPolygon", "coordinates": exteriores}


def geometria_geojson(g: dict | None) -> dict | None:
    if not isinstance(g, dict) or not g:
        return None
    if "x" in g and "y" in g:
        if g["x"] is None or g["y"] is None:
            return None
        return {"type": "Point", "coordinates": [g["x"], g["y"]]}
    if "points" in g:
        return {"type": "MultiPoint", "coordinates": g["points"]}
    if "paths" in g:
        p = g["paths"]
        return {"type": "LineString", "coordinates": p[0]} if len(p) == 1 else {"type": "MultiLineString",
                                                                                 "coordinates": p}
    if "rings" in g:
        return _poligono(g["rings"])
    return None


def _oid(meta: dict) -> str | None:
    if meta.get("objectIdField"):
        return str(meta["objectIdField"])
    return next((f["name"] for f in meta.get("fields") or []
                 if isinstance(f, dict) and f.get("type") == "esriFieldTypeOID" and f.get("name")), None)


def _paginas_en_paralelo(http: httpx.Client, capa: str, base: dict, objetivo: int,
                         por_pagina: int) -> tuple[list[dict], bool]:
    """Los `objetivo` primeros elementos (orden estable por OBJECTID), pidiendo varias páginas a la vez.

    Cada tramo [inicio, inicio+n) se completa: si el servicio devuelve menos (límite de transferencia
    con geometrías pesadas) se pide lo que falta; si no responde a tiempo, se parte en dos."""
    from concurrent.futures import ThreadPoolExecutor

    def tramo(inicio: int, n: int) -> list[dict]:
        try:
            datos = _json(http, f"{capa}/query", {**base, "resultOffset": inicio, "resultRecordCount": n})
        except TiempoAgotado:
            if n <= PAGINA_MINIMA:
                raise
            mitad = n // 2
            logger.warning("página lenta en %s: se parte en dos (%s + %s)", capa, mitad, n - mitad)
            return tramo(inicio, mitad) + tramo(inicio + mitad, n - mitad)
        feats = datos.get("features") or []
        if feats and len(feats) < n:
            feats = feats + tramo(inicio + len(feats), n - len(feats))
        return feats

    # al menos tantos tramos como se piden a la vez: el RUNAP (1882 polígonos = 110 MB) cabía en UNA
    # página de 2000, no se paralelizaba nada y esa página enorme no llegaba a tiempo (139 s)
    por_pagina = max(PAGINA_MINIMA, min(por_pagina, -(-objetivo // PAGINAS_EN_PARALELO)))
    tramos = [(i, min(por_pagina, objetivo - i)) for i in range(0, objetivo, por_pagina)]
    cortado = len(tramos) > MAX_PAGINAS
    tramos = tramos[:MAX_PAGINAS]
    with ThreadPoolExecutor(max_workers=PAGINAS_EN_PARALELO, thread_name_prefix="arcgis-pagina") as pool:
        resultados = list(pool.map(lambda t: tramo(*t), tramos))
    return [f for pagina in resultados for f in pagina], cortado


def consultar(url: str, *, where: str | None = None, bbox: list[float] | None = None,  # noqa: C901, PLR0912, PLR0915
              out_fields: list[str] | None = None, max_features: int = 2000) -> tuple[dict, dict]:
    """(FeatureCollection EPSG:4326, hechos). Los hechos dicen cuántos hay y cuántos vinieron."""
    if tipo_de_url(url) == "ImageServer":
        raise ErrorArcGIS("un ImageServer es imagen: no tiene elementos que consultar (descríbelo)")
    if bbox is not None and (len(bbox) != 4 or not extent_valido(bbox)):
        raise ErrorArcGIS("bbox debe ser [minx, miny, maxx, maxy] en EPSG:4326")
    capa = url_de_capa(url)
    tope = max(1, min(int(max_features), MAX_ELEMENTOS))
    filtro = (where or "").strip() or "1=1"
    with cliente(capa, timeout=TIMEOUT_CONSULTA_S) as http:
        meta = _json(http, capa)
        if not meta.get("geometryType"):
            raise ErrorArcGIS("esa capa no tiene geometría (¿es una tabla o un grupo de capas?); "
                              "describe el servicio para ver sus capas")
        total = _contar(http, capa, filtro, bbox)
        por_pagina = min(TAMANO_PAGINA, int(meta.get("maxRecordCount") or TAMANO_PAGINA))
        # 7 decimales en grados ≈ 1 cm: sin esto ArcGIS manda 15 y una capa de 116 municipios pesaba 15 MB
        base = {"where": filtro, "outFields": ",".join(out_fields) if out_fields else "*", "outSR": "4326",
                "returnGeometry": "true", "geometryPrecision": "7", **_filtro_bbox(bbox)}
        # orden estable: sin él ArcGIS no garantiza el mismo orden entre páginas (duplicados y huecos)
        oid = _oid(meta)
        if oid and (total is None or total > por_pagina):
            base["orderByFields"] = oid
        crudos: list[dict] = []
        cortado_por_paginas = False
        if total is not None and oid:
            # se sabe cuántos hay y el orden es estable: las páginas se piden a la vez (mismo resultado,
            # exacto, sin generalizar geometrías: los vectores enteros, que es con lo que trabaja Python)
            crudos, cortado_por_paginas = _paginas_en_paralelo(http, capa, base, min(total, tope), por_pagina)
        else:  # sin total o sin orden estable: una tras otra, siguiendo exceededTransferLimit
            for _ in range(MAX_PAGINAS):
                # Rama arcgis-busqueda (V5): los polígonos del RUNAP no cabían en 20 s por página de 1000 y la
                # carga fallaba entera («ReadTimeout»). Si una página no llega a tiempo, la misma, a la mitad.
                while True:
                    try:
                        datos = _json(http, f"{capa}/query", {**base, "resultOffset": len(crudos),
                                                              "resultRecordCount": min(por_pagina, tope - len(crudos))})
                        break
                    except TiempoAgotado:
                        if por_pagina <= PAGINA_MINIMA:
                            raise
                        por_pagina = max(PAGINA_MINIMA, por_pagina // 2)
                        logger.warning("página lenta en %s: se reintenta con %s elementos", capa, por_pagina)
                pagina = datos.get("features") or []
                crudos.extend(pagina)
                if not pagina or len(crudos) >= tope:
                    break
                if not datos.get("exceededTransferLimit") and len(pagina) < por_pagina:
                    break
            else:
                cortado_por_paginas = True
                logger.warning("se alcanzó el máximo de %s páginas en %s", MAX_PAGINAS, capa)
    crudos = crudos[:tope]
    feats, sin_geometria = [], 0
    for f in crudos:
        g = geometria_geojson(f.get("geometry"))
        if g is None:
            sin_geometria += 1
            continue
        feats.append({"type": "Feature", "geometry": g, "properties": f.get("attributes") or {}})
    completo = total is not None and len(crudos) >= total and not cortado_por_paginas
    hechos: dict[str, Any] = {
        "capa": meta.get("name") or capa.rsplit("/", 2)[-2], "url": capa, "filtro": filtro,
        **({"bbox": bbox} if bbox else {}), "total_en_servicio": total, "traidos": len(feats),
        "completo": completo,
        "tipo_geometria": str(meta.get("geometryType")).replace("esriGeometry", ""),
    }
    if sin_geometria:
        hechos["sin_geometria"] = sin_geometria
    if not completo:
        hechos["aviso"] = (f"es una MUESTRA: vinieron {len(feats)} de {total if total is not None else '¿?'}; "
                           "acota con where/bbox o sube max_features")
    return {"type": "FeatureCollection", "features": feats}, hechos
