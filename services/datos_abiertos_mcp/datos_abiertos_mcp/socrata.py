"""Socrata (datos.gov.co): buscar datasets, describirlos y consultarlos con SoQL filtrando en el portal.

Medido el 2026-10-15 contra www.datos.gov.co:
- el catálogo (api.us.socrata.com/api/catalog/v1) trae nombre, entidad, columnas y sus tipos por dataset;
- `/resource/<id>.geojson` sale en lon/lat; `$select=count(*)` cuenta con el mismo `$where`;
- `within_box(col, lat_norte, lon_oeste, lat_sur, lon_este)` filtra una columna de ubicación (Envigado,
  instituciones educativas: 82 en total, 70 en [-75.62, 6.14, -75.55, 6.20]);
- la mayoría de datasets NO tiene columna de ubicación; algunos traen latitud/longitud como texto.
"""

from __future__ import annotations

import re
from typing import Any

from geo_mcp_kit import red

from datos_abiertos_mcp import filas

CATALOGO = "https://api.us.socrata.com/api/catalog/v1"
TIPOS_GEO = {"point", "location", "multipoint", "line", "multiline", "polygon", "multipolygon"}
PAGINA = 5000
MAX_FILAS = 50_000
USER_AGENT = "geo-copilot-datos-abiertos-mcp/1"
_ID = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")


class ErrorSocrata(red.ErrorRed):
    pass


def _get(url: str, params: dict | None = None):
    return red.get(url, params, timeout=90, user_agent=USER_AGENT)


def _corto(texto: Any, n: int = 240) -> str | None:
    t = " ".join(str(texto or "").split())
    return (t if len(t) <= n else t[: n - 1] + "…") or None


def buscar(texto: str, dominio: str, limite: int = 10) -> list[dict[str, Any]]:
    """Datasets del portal que responden a `texto`, con los hechos para elegir (entidad, columnas, fecha)."""
    r = _get(CATALOGO, {"domains": dominio, "q": texto, "only": "dataset", "limit": max(1, min(limite, 25))})
    salida = []
    for item in r.json().get("results") or []:
        res = item.get("resource") or {}
        cols = list(zip(res.get("columns_field_name") or [], res.get("columns_datatype") or [], strict=False))
        salida.append({
            "id": res.get("id"), "nombre": res.get("name"), "entidad": res.get("attribution"),
            "categoria": (item.get("classification") or {}).get("domain_category"),
            "descripcion": _corto(res.get("description")), "actualizado": str(res.get("data_updated_at") or "")[:10],
            "vistas": (res.get("page_views") or {}).get("page_views_total"),
            "columnas_de_ubicacion": [f"{n} ({t})" for n, t in cols if t.lower() in TIPOS_GEO],
            "columnas": [f"{n} ({t})" for n, t in cols][:40],
        })
    return salida


def _id_valido(ident: str) -> str:
    ident = (ident or "").strip().lower()
    if not _ID.match(ident):
        raise ErrorSocrata("id de dataset no válido: es de la forma «abcd-1234» (de datos_buscar)")
    return ident


def describir(dominio: str, ident: str) -> dict[str, Any]:
    """Columnas con su tipo, cuántas filas tiene y de quién es."""
    ident = _id_valido(ident)
    meta = _get(f"https://{dominio}/api/views/{ident}.json").json()
    total = _contar(dominio, ident, None)
    cols = [(c.get("fieldName"), c.get("dataTypeName")) for c in meta.get("columns") or []
            if not str(c.get("fieldName") or "").startswith(":@")]
    return {"id": ident, "nombre": meta.get("name"), "entidad": meta.get("attribution"),
            "descripcion": _corto(meta.get("description"), 600), "filas": total,
            "columnas": [f"{n} ({t})" for n, t in cols],
            "columnas_de_ubicacion": [n for n, t in cols if str(t).lower() in TIPOS_GEO],
            "valores_de_columnas": valores_de_texto(dominio, ident, cols),
            "actualizado": meta.get("rowsUpdatedAt"), "licencia": (meta.get("license") or {}).get("name")}


MAX_VALORES = 12
MAX_COLUMNAS_CON_VALORES = 20


def _valores(dominio: str, ident: str, columna: str) -> dict[str, int] | None:
    """{valor: filas} si la columna tiene pocos valores distintos (≤ MAX_VALORES); si no, None."""
    try:
        filas = _get(f"https://{dominio}/resource/{ident}.json", {
            "$select": f"{columna}, count(*) AS n", "$group": columna, "$order": "n DESC",
            "$limit": MAX_VALORES + 1}).json()
    except red.ErrorRed:
        return None
    if len(filas) > MAX_VALORES:
        return None
    # sin valor: «(vacío)», no «None» (en SoQL se filtra con `IS NULL`, no con un texto)
    return {("(vacío)" if f.get(columna) is None else str(f[columna])): int(f.get("n") or 0) for f in filas}


def valores_de_texto(dominio: str, ident: str, columnas: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Los valores EXACTOS de las columnas de texto con pocos valores distintos, con cuántas filas tiene cada uno.

    V5 (datos-abiertos-mcp): sin conocerlos, el agente filtró `tipo_ie like '%oficial%'` (también atrapa
    «No Oficial»: dijo 82 oficiales, son 23) o `= 'OFICIAL'` (0). Con los valores a la vista filtra bien.
    """
    from concurrent.futures import ThreadPoolExecutor

    texto = [n for n, t in columnas if str(t).lower() == "text"][:MAX_COLUMNAS_CON_VALORES]
    with ThreadPoolExecutor(6) as ex:
        hallados = dict(zip(texto, ex.map(lambda c: _valores(dominio, ident, c), texto), strict=True))
    return {c: v for c, v in hallados.items() if v}


def _contar(dominio: str, ident: str, where: str | None) -> int | None:
    params = {"$select": "count(*)"}
    if where:
        params["$where"] = where
    try:
        filas = _get(f"https://{dominio}/resource/{ident}.json", params).json()
        return int(filas[0][next(iter(filas[0]))])
    except (red.ErrorRed, ValueError, KeyError, IndexError, StopIteration, TypeError):
        return None  # contar es un hecho de cortesía; sin él, «vinieron N» sigue siendo cierto


def filtro_bbox(bbox: list[float], columna_geo: str | None, lat: str | None, lon: str | None) -> str:
    """El `$where` de un rectángulo: `within_box` sobre la columna de ubicación o rangos lat/lon (texto → número)."""
    x1, y1, x2, y2 = bbox
    if columna_geo:
        return f"within_box({columna_geo}, {y2}, {x1}, {y1}, {x2})"
    if lat and lon:
        return f"{lat}::number between {y1} and {y2} AND {lon}::number between {x1} and {x2}"
    raise ErrorSocrata("para filtrar por zona hace falta `columna_geo` o `lat` y `lon` (ver datos_describir)")


def consultar(dominio: str, ident: str, *, select: str | None, where: str | None, order: str | None,
              maximo: int, geojson: bool, group: str | None = None,
              having: str | None = None, previo_select: str | None = None,
              previo_group: str | None = None) -> tuple[list[dict], int | None]:
    """(filas o features, total que cumple el filtro). Paginado; SoQL lo ejecuta el portal.

    `group`/`having` (Terranova F5, G1): agrupar exige un `select` con agregados; sin `order`, se
    ordena por las columnas del grupo para que las páginas sean estables.

    `previo_select`/`previo_group` (Terranova, spec 07): una etapa antes del agregado, para fuentes
    que repiten registros. El ICFES trae dos veces a cada estudiante en algunos periodos: se agrupa
    primero por estudiante y luego se cuenta y promedia sobre esos grupos (`… |> SELECT …`).
    """
    ident = _id_valido(ident)
    if previo_select or previo_group:
        return _consultar_en_dos_etapas(dominio, ident, select=select, where=where, order=order,
                                        maximo=maximo, group=group, having=having,
                                        previo_select=previo_select, previo_group=previo_group)
    if (group or having) and not filas.es_agregado(select):
        raise ErrorSocrata("`group` y `having` necesitan un `select` con agregados (count, sum, avg…)")
    if having and not group:
        raise ErrorSocrata("`having` filtra grupos: hace falta `group`")
    maximo = max(1, min(int(maximo), MAX_FILAS))
    extension = "geojson" if geojson else "json"
    salida: list[dict] = []
    while len(salida) < maximo:
        params: dict[str, Any] = {"$limit": min(PAGINA, maximo - len(salida)), "$offset": len(salida)}
        # sin orden, las páginas de SODA no son estables (filas repetidas o saltadas): `:id` por defecto.
        # Con agregados no: `:id` no está en el group by y SODA responde 400 (Terranova, canario SECOP).
        orden = order or group or (None if filas.es_agregado(select) else ":id")
        params.update({k: v for k, v in (("$select", select), ("$where", where), ("$group", group),
                                         ("$having", having), ("$order", orden)) if v})
        datos = _get(f"https://{dominio}/resource/{ident}.{extension}", params).json()
        pagina = datos.get("features", []) if geojson else datos
        salida += pagina
        if len(pagina) < params["$limit"]:
            break
    return salida[:maximo], _contar(dominio, ident, where)


def _consultar_en_dos_etapas(dominio: str, ident: str, *, select: str | None, where: str | None,
                             order: str | None, maximo: int, group: str | None, having: str | None,
                             previo_select: str | None,
                             previo_group: str | None) -> tuple[list[dict], int | None]:
    """`$query` encadenado: la etapa previa agrupa (deduplica) y la segunda agrega sobre sus grupos."""
    if not (previo_select and previo_group):
        raise ErrorSocrata("la etapa previa necesita `previo_select` y `previo_group` juntos")
    if not (group and filas.es_agregado(select)):
        raise ErrorSocrata("tras la etapa previa va un agregado: hacen falta `select` con agregados y `group`")
    previa = f"SELECT {previo_select}" + (f" WHERE {where}" if where else "") + f" GROUP BY {previo_group}"
    final = f"SELECT {select} GROUP BY {group}" + (f" HAVING {having}" if having else "")
    final += f" ORDER BY {order or group}"
    maximo = max(1, min(int(maximo), MAX_FILAS))
    salida: list[dict] = []
    while len(salida) < maximo:
        limite = min(PAGINA, maximo - len(salida))
        consulta = f"{previa} |> {final} LIMIT {limite} OFFSET {len(salida)}"
        pagina = _get(f"https://{dominio}/resource/{ident}.json", {"$query": consulta}).json()
        salida += pagina
        if len(pagina) < limite:
            break
    return salida[:maximo], _contar(dominio, ident, where)
