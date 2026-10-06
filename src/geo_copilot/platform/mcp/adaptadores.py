"""Adaptadores G0 → G1 (plan §3.5, T5.4): MCP de terceros que no hablan geo.

Un servidor tabular (Snowflake, BigQuery, un Postgres genérico…) devuelve FILAS, no capas. El
adaptador `tabular_geo` convierte esas filas en una tabla con geometría que el núcleo
materializa en el workspace (y se dibuja y se cruza como cualquier capa).

Sin heurísticas: qué columna es la geometría, su codificación y su CRS los DECLARA el LLM —que
escribió la consulta y sabe qué pidió, p. ej. `ST_ASWKT(geom) AS geom_wkt`— en el argumento
`geometria_resultado`, o la configuración del servidor (`adapter_options.geometry`). El núcleo
lo VALIDA: la columna existe, cada geometría se parsea y, en EPSG:4326, cae en rango lon/lat. Si
algo no cuadra se dice qué (y con qué columnas) y no se dibuja nada inventado.
"""

from __future__ import annotations

import json
from typing import Any

ARG = "geometria_resultado"

ESQUEMA_ARG: dict[str, Any] = {
    "type": "object",
    "description": (
        "SOLO cuando la consulta DEVUELVE una columna de geometría y quieres verla en el mapa (o "
        "cruzarla); no en conteos ni agregados sin geometría. Qué columna del RESULTADO es la geometría. `encoding`: wkt | wkb | wkb_hex | geojson | latlon (con latlon, "
        "`column` es la longitud y `lat_column` la latitud). `crs` el de esas coordenadas (p. ej. "
        "EPSG:4326). Pide la geometría como texto en la consulta (p. ej. ST_ASWKT(geom) AS geom_wkt)."
    ),
    "properties": {
        "column": {"type": "string"},
        "encoding": {"type": "string", "enum": ["wkt", "wkb", "wkb_hex", "geojson", "latlon"]},
        "crs": {"type": "string"},
        "lat_column": {"type": "string"},
        "nombre": {"type": "string", "description": "Nombre de la capa resultante (lo que ES, p. ej. «Sedes "
                                                      "educativas de Soacha»); con él la verás y la citarás."},
    },
    "required": ["column", "encoding", "crs"],
}


class AdaptacionFallida(ValueError):
    """La declaración no cuadra con el resultado (el mensaje dice por qué y qué hay)."""


class ColumnaAusente(AdaptacionFallida):
    """El resultado no trae la columna declarada (p. ej. un conteo): las filas valen como tabla."""


def filas_de(sc: Any, textos: list[str]) -> list[dict[str, Any]] | None:
    """Las filas de un resultado G0: `structuredContent` o texto JSON con una lista de objetos.

    No se interpreta contenido: solo se busca la forma «lista de objetos» (tal cual, bajo una
    única clave que la contenga, o como texto JSON). None si el resultado no es tabular.
    """
    candidatos: list[Any] = []
    if sc is not None:
        candidatos.append(sc)
    for t in textos:
        try:
            candidatos.append(json.loads(t))
        except (TypeError, ValueError):
            continue
    for c in candidatos:
        filas = _lista_de_objetos(c)
        if filas is not None:
            return filas
    return None


def _lista_de_objetos(v: Any) -> list[dict[str, Any]] | None:
    """La lista de filas (puede estar VACÍA: «0 filas» también es un resultado)."""
    if isinstance(v, list) and all(isinstance(x, dict) for x in v):
        return v
    if isinstance(v, dict):
        listas = [x for x in v.values() if isinstance(x, list) and all(isinstance(y, dict) for y in x)]
        if len(listas) == 1:
            return listas[0]
    return None


def _geometria(valor: Any, encoding: str) -> Any:
    from shapely import wkb, wkt
    from shapely.geometry import shape

    if valor is None:
        return None
    if encoding == "wkt":
        return wkt.loads(str(valor))
    if encoding == "wkb_hex" or (encoding == "wkb" and isinstance(valor, str)):
        return wkb.loads(str(valor), hex=True)
    if encoding == "wkb":
        return wkb.loads(bytes(valor))
    if encoding == "geojson":
        return shape(json.loads(valor) if isinstance(valor, str) else valor)
    raise AdaptacionFallida(f"codificación no soportada: {encoding}")


def validar(filas: list[dict[str, Any]], decl: dict[str, Any]) -> dict[str, Any]:  # noqa: C901, PLR0912
    """La declaración normalizada si cuadra con las filas; si no, `AdaptacionFallida` con el motivo."""
    column, encoding, crs = str(decl.get("column") or ""), str(decl.get("encoding") or ""), str(decl.get("crs") or "")
    lat = str(decl.get("lat_column") or "")
    columnas = list(filas[0].keys())
    if encoding == "latlon" and not lat:
        raise AdaptacionFallida("con encoding latlon hace falta `lat_column` (column = longitud)")
    faltan = [c for c in ([column] + ([lat] if encoding == "latlon" else [])) if c not in columnas]
    if faltan:
        raise ColumnaAusente(f"el resultado no tiene la(s) columna(s) {faltan}; sus columnas: {columnas}")
    if not crs.upper().startswith("EPSG:"):
        raise AdaptacionFallida(f"crs debe ser un código EPSG (p. ej. EPSG:4326), no {crs!r}")
    malas, fuera, validas = 0, 0, 0
    for f in filas:
        try:
            if encoding == "latlon":
                x, y = f.get(column), f.get(lat)
                if x is None or y is None:
                    continue
                x, y = float(x), float(y)
                cajas = [(x, y, x, y)]
            else:
                g = _geometria(f.get(column), encoding)
                if g is None or g.is_empty:
                    continue
                cajas = [g.bounds]
        except Exception:  # noqa: BLE001 — cualquier fallo de parseo cuenta como geometría inválida
            malas += 1
            continue
        validas += 1
        if crs.upper() == "EPSG:4326":
            fuera += sum(1 for (x0, y0, x1, y1) in cajas
                         if not (-180 <= x0 <= 180 and -180 <= x1 <= 180 and -90 <= y0 <= 90 and -90 <= y1 <= 90))
    if malas:
        raise AdaptacionFallida(f"{malas} de {len(filas)} valores de «{column}» no son {encoding} válido")
    if fuera:
        raise AdaptacionFallida(f"{fuera} geometrías están fuera del rango lon/lat: ¿de verdad son {crs}? "
                                "(declara el CRS real de esas coordenadas)")
    if not validas:
        raise AdaptacionFallida(f"ninguna fila trae geometría en «{column}»")
    out = {"column": column, "encoding": encoding, "crs": crs.upper()}
    if encoding == "latlon":
        out["lat_column"] = lat
    return out


def adaptar_tabular(sc: Any, textos: list[str], decl: dict[str, Any] | None, *, nombre: str) -> dict | None:
    """Un `GeoResult` con la tabla (y su geometría declarada y validada), o None si no hay filas."""
    filas = filas_de(sc, textos)
    if filas is None:
        return None
    if not filas:
        # sin filas no hay geometría que validar: el hecho es «0 filas» (antes llegaba un `[]` crudo)
        return {"geo_result": "1", "artifacts": [], "facts": {
            "filas": 0, "aviso": ("la consulta no devolvió filas. Si filtraste por un texto, puede que el "
                                  "valor se escriba distinto (mayúsculas, tildes): compara con UPPER()/ILIKE "
                                  "o mira los valores reales con SELECT DISTINCT")}}
    nombre = str((decl or {}).get("nombre") or "").strip()[:120] or nombre
    art: dict[str, Any] = {"kind": "table", "name": nombre, "columns": list(filas[0].keys()), "rows": filas}
    hechos: dict[str, Any] = {"filas": len(filas), "columnas": list(filas[0].keys())}
    if decl:
        try:
            art["geometry"] = validar(filas, decl)
            hechos["geometria"] = art["geometry"]["column"]
        except ColumnaAusente as exc:
            # un conteo o un agregado no trae geometría: las filas son la respuesta (sin mapa)
            hechos["aviso"] = (f"se declaró geometría pero {exc}: se entrega como TABLA, sin mapa. Si "
                               "querías verla en el mapa, pide la geometría como texto en la consulta")
    return {"geo_result": "1", "artifacts": [art], "facts": hechos}
