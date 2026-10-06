"""Selección compartida (FH.2): la referencia `seleccion` para todas las herramientas.

El usuario selecciona en el mapa (clic, caja, lazo, tabla) o el agente con una
orden `select`. La selección vive en la capa (`map_context.layers[].seleccion`)
y viaja al backend como IDS (pocos) o como PREDICADO (muchos: nunca geometrías).

Cualquier herramienta que reciba una capa puede recibir `seleccion`:
  - capas en memoria del turno (`map_layers`): se filtra su GeoJSON;
  - datasets del workspace (teselas, grandes): se materializa un dataset nuevo
    con un SELECT parametrizado (el campo, validado contra los del dataset; el
    valor, como parámetro — nunca texto del LLM concatenado).

Identidad de un elemento: su `id` de Feature si lo trae (el `fid` del workspace:
GeoJSON de un dataset, teselas); si no, su índice en la FeatureCollection (el
`generateId` de MapLibre). Nunca la posición cuando hay id: los datasets hechos
por SQL numeran su `fid` desde 1 y en otro orden.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: El nombre de la referencia, igual en todas las herramientas.
SELECCION = "seleccion"


@dataclass(frozen=True)
class Seleccion:
    layer_id: str
    layer_name: str
    ids: tuple[int, ...] | None
    where: dict[str, Any] | None
    count: int | None
    origin: str
    dataset_id: str | None


def seleccion_actual(working: dict) -> Seleccion | None:
    """La selección vigente: la que hizo el agente EN ESTE TURNO (si la hizo) o la del mapa."""
    turno = working.get("seleccion_turno")
    if isinstance(turno, dict) and turno.get("cleared"):
        return None  # el agente la limpió en este turno
    if isinstance(turno, dict) and turno.get("layer_id"):
        return _desde(turno, working)
    for capa in ((working.get("map_context") or {}).get("layers") or []):
        sel = capa.get("seleccion")
        if isinstance(sel, dict) and (sel.get("ids") or sel.get("where")):
            return _desde({**sel, "layer_id": capa.get("id")}, working)
    return None


def _desde(sel: dict, working: dict) -> Seleccion:
    capas = {c.get("id"): c for c in ((working.get("map_context") or {}).get("layers") or [])}
    capa = capas.get(sel["layer_id"]) or {}
    ids = sel.get("ids")
    return Seleccion(
        layer_id=str(sel["layer_id"]),
        layer_name=str(capa.get("name") or sel.get("layer_name") or sel["layer_id"]),
        ids=tuple(int(i) for i in ids) if ids else None,
        where=sel.get("where") or None,
        count=sel.get("count"),
        origin=str(sel.get("origin") or "agent"),
        dataset_id=capa.get("dataset_id"),
    )


# ---------------------------------------------------------------------------
# Predicado sobre propiedades (capas en memoria)
# ---------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cumple(where: dict[str, Any], props: dict[str, Any]) -> bool:
    """¿Estas propiedades cumplen el predicado? Comparación numérica si ambos lados lo son."""
    campo, op, valor = where.get("field"), where.get("op"), where.get("value")
    if campo not in props:
        return False
    x = props[campo]
    if op == "in":
        opciones = valor if isinstance(valor, list) else [valor]
        return any(str(x) == str(o) for o in opciones)
    if op == "contains":
        return str(valor).lower() in str(x).lower()
    a, b = _num(x), _num(valor)
    if a is not None and b is not None:
        return {"=": a == b, "!=": a != b, ">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}.get(str(op), False)
    return {"=": str(x) == str(valor), "!=": str(x) != str(valor)}.get(str(op), False)


def identidad(feature: dict, indice: int) -> int:
    """El id con el que el mapa se refiere a este elemento (ver el docstring del módulo)."""
    fid = feature.get("id")
    return fid if isinstance(fid, int) and not isinstance(fid, bool) else indice


def cumple_todas(condiciones: list[dict[str, Any]], props: dict[str, Any]) -> bool:
    """FH.5: ¿cumple todas las condiciones de un filtro (AND)?"""
    return all(cumple(c, props) for c in condiciones)


def filtrar_capa(fc: dict, condiciones: list[dict[str, Any]]) -> dict:
    """FH.5: la FeatureCollection con solo lo que el filtro de la capa deja ver.

    Cada elemento conserva su identidad del mapa: un Feature sin `id` se identifica por su
    índice en la capa ENTERA, que filtrando cambiaría (FH.7: un enlace `#id` apuntaría a otro).
    """
    feats = [f if "id" in f else {**f, "id": i} for i, f in enumerate(fc.get("features") or [])
             if isinstance(f, dict) and cumple_todas(condiciones, f.get("properties") or {})]
    return {"type": "FeatureCollection", "features": feats}


def filtrar(fc: dict, sel: Seleccion) -> dict:
    """La FeatureCollection con solo los elementos seleccionados (por índice o predicado)."""
    feats = [f for f in (fc.get("features") or []) if isinstance(f, dict)]
    if sel.ids is not None:
        elegidos = set(sel.ids)
        feats = [f for i, f in enumerate(feats) if identidad(f, i) in elegidos]
    elif sel.where:
        feats = [f for f in feats if cumple(sel.where, f.get("properties") or {})]
    return {"type": "FeatureCollection", "features": feats}


# ---------------------------------------------------------------------------
# Predicado en SQL (datasets del workspace)
# ---------------------------------------------------------------------------


class PredicadoInvalido(ValueError):
    pass


def sql_where(where: dict[str, Any] | None, ids: tuple[int, ...] | None, campos: list[str],
              alias: str = "s") -> tuple[str, tuple[Any, ...]]:
    """(condición, parámetros) para `WHERE`. El campo debe ser uno del dataset."""
    if ids is not None:
        return f"{alias}.fid = ANY($1::int[])", (list(ids),)
    if not where:
        raise PredicadoInvalido("la selección no tiene ids ni condición")
    return _condicion(where, campos, alias, 1)


def sql_filtro(condiciones: list[dict[str, Any]], campos: list[str],
               alias: str = "s") -> tuple[str, tuple[Any, ...]]:
    """FH.5: (condición, parámetros) de un filtro de capa: todas las condiciones (AND)."""
    if not condiciones:
        raise PredicadoInvalido("el filtro no tiene condiciones")
    partes: list[str] = []
    params: list[Any] = []
    for c in condiciones:
        cond, ps = _condicion(c, campos, alias, len(params) + 1)
        partes.append(f"({cond})")
        params.extend(ps)
    return " AND ".join(partes), tuple(params)


def _condicion(where: dict[str, Any], campos: list[str], alias: str, n: int) -> tuple[str, tuple[Any, ...]]:
    """Una condición con su parámetro `$n` (el campo, validado; el valor, como parámetro)."""
    campo, op, valor = where.get("field"), where.get("op"), where.get("value")
    if campo not in campos:
        raise PredicadoInvalido(f"el campo «{campo}» no existe en la capa; campos: {', '.join(campos[:20])}")
    col = f'{alias}."{campo}"'
    if op == "in":
        opciones = [str(o) for o in (valor if isinstance(valor, list) else [valor])]
        return f"{col}::text = ANY(${n}::text[])", (opciones,)
    if op == "contains":
        return f"{col}::text ILIKE '%' || ${n} || '%'", (str(valor),)
    if op not in ("=", "!=", ">", ">=", "<", "<="):
        raise PredicadoInvalido(f"operador no soportado: {op}")
    num = _num(valor)
    if num is not None and not isinstance(valor, bool):
        return f"({col})::double precision {op} ${n}", (num,)
    return f"{col}::text {op} ${n}", (str(valor),)


async def dataset_de_seleccion(store: Any, session_id: str, sel: Seleccion,
                               working: dict) -> str | None:
    """Materializa la selección como dataset del workspace; devuelve su id.

    Dataset de origen: sus filas que cumplen (SELECT parametrizado). Capa solo en
    memoria: sus elementos seleccionados se ingieren.
    """
    from datetime import UTC, datetime

    from geo_copilot.platform.contracts import Provenance

    prov = Provenance(capability="core.seleccion", produced_at=datetime.now(UTC),
                      arguments={"layer": sel.layer_id, "ids": len(sel.ids or ()), "where": sel.where})
    nombre = f"Selección de {sel.layer_name}"[:60]
    if sel.dataset_id:
        ref = await store.get(session_id, sel.dataset_id)
        if ref is not None:
            from geo_copilot.platform.workspace.ops import _props, _tabla

            cond, params = sql_where(sel.where, sel.ids, [f.name for f in ref.fields])
            cols = ", ".join([*_props(ref, "s"), "s.geom"])
            sql = f"SELECT {cols} FROM {_tabla(ref)} s WHERE {cond} ORDER BY s.fid"
            nuevo = await store.crear_desde_sql(session_id, nombre, sql, params, provenance=prov)
            return str(nuevo.id)
    datos = ((working.get("map_layers") or {}).get(sel.layer_id) or {}).get("data")
    if isinstance(datos, dict):
        sub = filtrar(datos, sel)
        if sub["features"]:
            nuevo = await store.ingest_features(session_id, nombre, sub, crs="EPSG:4326", provenance=prov)
            return str(nuevo.id)
    return None


def capa_virtual(map_layers: dict[str, dict], map_context: dict) -> dict | None:
    """La entrada `seleccion` para `map_layers`: el subconjunto seleccionado de una capa en memoria."""
    sel = seleccion_actual({"map_context": map_context})
    if sel is None:
        return None
    datos = (map_layers.get(sel.layer_id) or {}).get("data")
    if not isinstance(datos, dict):
        return None
    sub = filtrar(datos, sel)
    return {"data": sub, "name": f"Selección de {sel.layer_name}"} if sub["features"] else None


# ---------------------------------------------------------------------------
# FH.5: capas filtradas (una «definition query»: la capa ES su subconjunto)
# ---------------------------------------------------------------------------


def filtro_de_dataset(ds_id: str, working: dict) -> list[dict[str, Any]] | None:
    """El filtro de la capa del mapa que lleva este dataset, si lo tiene."""
    for capa in ((working.get("map_context") or {}).get("layers") or []):
        if capa.get("dataset_id") == ds_id and capa.get("filtro"):
            return [c for c in capa["filtro"] if isinstance(c, dict)]
    return None


async def dataset_efectivo(store: Any, session_id: str, ds_id: str, working: dict) -> str:
    """El dataset que una herramienta debe usar para `ds_id`: él mismo o, si su capa está
    filtrada, el subconjunto (materializado una vez por turno, SELECT parametrizado)."""
    filtro = filtro_de_dataset(ds_id, working)
    if not filtro:
        return ds_id
    cache = working.setdefault("_filtrados", {})
    if ds_id in cache:
        return str(cache[ds_id])
    ref = await store.get(session_id, ds_id)
    if ref is None:
        return ds_id
    from datetime import UTC, datetime

    from geo_copilot.core.formatters import describir_filtro
    from geo_copilot.platform.contracts import Provenance
    from geo_copilot.platform.workspace.ops import _props, _tabla

    cond, params = sql_filtro(filtro, [f.name for f in ref.fields])
    cols = ", ".join([*_props(ref, "s"), "s.geom"])
    nuevo = await store.crear_desde_sql(
        # el nombre lleva la condición VIGENTE: la narración sale de aquí (V5 FH.5: dijo
        # «mayor a 100» con el filtro ya retocado a 130)
        session_id, f"{ref.name} (filtrada: {describir_filtro(filtro)})"[:120],
        f"SELECT {cols} FROM {_tabla(ref)} s WHERE {cond} ORDER BY s.fid", params,
        provenance=Provenance(capability="core.filtro", produced_at=datetime.now(UTC),
                              arguments={"dataset": ds_id, "filtro": describir_filtro(filtro)}))
    cache[ds_id] = str(nuevo.id)
    return str(nuevo.id)
