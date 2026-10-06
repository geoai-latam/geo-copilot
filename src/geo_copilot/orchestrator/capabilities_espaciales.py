"""Capacidades espaciales deterministas del núcleo (S2.5) para el bucle ReAct.

Envuelven `platform.workspace.ops`: el LLM elige la operación y sus parámetros;
la geometría la calcula PostGIS sobre los datasets del workspace de la sesión.
El resultado es un dataset nuevo (su LayerRef sale del turno en
`result_layer_ref`, así /query no lo vuelve a materializar) más las cifras
para narrar.

Solo se ofrecen si hay workspace (BD conectada).
"""

from __future__ import annotations

import json
from typing import Any

from geo_copilot.core.logging import get_logger

# F4: las operaciones de las capacidades y el bloque del workspace viven en sus módulos.
from geo_copilot.orchestrator.bloque_workspace import bloque_workspace  # noqa: F401
from geo_copilot.orchestrator.capacidades_operaciones import (
    _agregar,
    _agregar_medida,
    _autocorrelacion,
    _buffer,
    _join,
    _medir,
    _overlay,
)
from geo_copilot.orchestrator.layer_resolution import capa_de_dataset, copia_de_capa_actualizada
from geo_copilot.platform.capabilities import Capability, ToolOutcome
from geo_copilot.platform.workspace.context import store_actual

logger = get_logger(__name__)

#: Por encima de esto el resultado no se hidrata como GeoJSON en el turno: se
#: entrega por teselas (S2.3) desde su LayerRef.
_MAX_HIDRATAR = 5_000

_DATASET = {
    "type": "string",
    "description": "`activa` (la capa en foco del turno, p. ej. la que acabas de traer), "
    "`seleccion` (solo lo seleccionado en el mapa), `punto` (el punto marcado AHORA; si el mapa no tiene "
    "PUNTO MARCADO, el «aquí» del usuario no está en el mapa: pídelo con `request_map_input`), `viewport` (la zona "
    "visible), `dibujo` (lo último que DIBUJÓ el usuario), el id de un dataset del workspace (`ds_…` de 'DATASETS DEL WORKSPACE') "
    "o el [id] de la capa en 'CAPAS EN EL MAPA' si trae dataset.",
}


def _params(properties: dict, required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def _hay_workspace(_graph: Any) -> bool:
    return store_actual() is not None


def _dataset_de_capa(layer_id: Any, working: dict) -> str | None:
    for lyr in ((working.get("map_context") or {}).get("layers") or []):
        if lyr.get("id") == layer_id and lyr.get("dataset_id"):
            return str(lyr["dataset_id"])
    return None


async def _resolver(valor: Any, working: dict) -> str | None:
    """El dataset al que apunta una referencia (ver `_resolver_crudo`) y, si su capa
    del mapa está FILTRADA (FH.5), el subconjunto que el mapa muestra."""
    ds = await _resolver_crudo(valor, working)
    store, session_id = store_actual(), working.get("session_id") or ""
    if not ds or store is None or not session_id:
        return ds
    from geo_copilot.platform.seleccion import dataset_efectivo

    return await dataset_efectivo(store, session_id, ds, working)


async def _resolver_crudo(valor: Any, working: dict) -> str | None:
    """`ds_…` tal cual; un id de capa del mapa → su dataset_id (si tiene);
    `activa` → la capa en foco del turno, con la MISMA regla que el hub MCP y la
    simbología (`resolver_capa`).

    V5 F4: `query_database` dice «refiérete a ella como `activa`», pero aquí se
    rechazaba y la capa recién traída no tenía `ds_` (se materializa al final del
    turno): el agente midió y coloreó datasets viejos del mapa. Si la capa en foco
    aún no está en el workspace, se guarda ahora y queda como `result_layer_ref`
    del turno (/query la reutiliza en vez de materializarla otra vez).
    """
    v = str(valor or "").strip().strip("[]")
    if v == "dibujo":
        from geo_copilot.orchestrator.layer_resolution import capa_del_ultimo_dibujo

        v = capa_del_ultimo_dibujo(working) or v
    if v.startswith("ds_"):
        return v
    if v == "seleccion":
        # FH.2: lo seleccionado se materializa como dataset (SELECT parametrizado).
        from geo_copilot.platform.seleccion import dataset_de_seleccion, seleccion_actual

        sel = seleccion_actual(working)
        store = store_actual()
        if sel is None or store is None or not working.get("session_id"):
            return None
        return await dataset_de_seleccion(store, working["session_id"], sel, working)
    if v in ("punto", "viewport"):
        # FH.12 (bench de deixis): el LLM usa `punto` / `viewport` también aquí (como en el hub
        # MCP); son figuras del mapa, no datasets: se materializan (una vez por turno).
        return await _dataset_de_figura(v, working)
    if v != "activa":
        return _dataset_de_capa(v, working)
    ref = working.get("result_layer_ref") or {}
    if ref.get("id"):
        return str(ref["id"])
    from geo_copilot.orchestrator.layer_resolution import resolver_capa

    capa = resolver_capa(working, con_features=True)
    if capa is None:
        return None
    if capa.layer_id:
        return _dataset_de_capa(capa.layer_id, working)
    store, session_id = store_actual(), working.get("session_id") or ""
    if store is None or not session_id:
        return None
    from datetime import UTC, datetime

    from geo_copilot.platform.contracts import Provenance

    nueva = await store.ingest_features(
        session_id, str(working.get("layer_name") or capa.name or "Capa activa")[:60], capa.geojson,
        crs="EPSG:4326",
        provenance=Provenance(capability=f"core.{working.get('intent') or 'query_data'}",
                              produced_at=datetime.now(UTC), sql=working.get("sql") or None),
    )
    working["result_layer_ref"] = nueva.model_dump(mode="json")
    return nueva.id


async def _ejecutar(working: dict, args: dict, operar, entradas: tuple[str, ...] = ()) -> ToolOutcome:
    from geo_copilot.platform.workspace import WorkspaceError

    store = store_actual()
    session_id = working.get("session_id") or ""
    if store is None or not session_id:
        return ToolOutcome("no hay workspace en esta sesión", success=False)
    try:
        res = await operar(store, session_id)
    except WorkspaceError as exc:
        # Parámetro inválido o dataset ajeno/inexistente: el LLM puede corregir.
        disponibles = await _listado(session_id)
        return ToolOutcome(f"no se pudo: {exc}. {disponibles}", success=False)

    obs: dict[str, Any] = {"hechos": res.hechos}
    if subconjuntos := await _subconjuntos(store, session_id, entradas):
        # Junto a la cifra, donde se usa: en el listado del workspace solo, el LLM contó la capa
        # «Construcciones Manzana X» (9) y respondió «9 construcciones del catastro» (8/8 corridas)
        obs["ojo_subconjuntos"] = subconjuntos
    if nota := _nota_seleccion(working, args):
        obs["seleccion_en_el_mapa"] = nota
    delta: dict[str, Any] = {}
    misma = bool(res.hechos.get("misma_capa"))
    if res.ref is not None and misma:
        obs["dataset_actualizado"] = {"id": res.ref.id, "nombre": res.ref.name,
                                      "campos": [f.name for f in res.ref.fields]}
    elif res.ref is not None:
        obs["nuevo_dataset"] = {"id": res.ref.id, "nombre": res.ref.name,
                                "elementos": res.ref.feature_count}
    if res.ref is not None:
        geojson = None
        if (res.ref.feature_count or 0) <= _MAX_HIDRATAR and res.ref.storage.kind == "workspace-table":
            geojson = await store.to_geojson(session_id, res.ref.id, limit=_MAX_HIDRATAR)
        # Pizarra limpia de las salidas de pasos previos (capa nueva o la misma con un campo más).
        delta = {
            "geojson": geojson, "result_layer_ref": res.ref.model_dump(mode="json"),
            "layer_name": res.ref.name, "active_data_source": "internal",
            # H18 (V5 F2): la capa NUEVA pasa a ser el foco. Con el target del
            # router aún puesto, la simbología siguiente estilizó la capa de
            # entrada (con sus campos) y no la de clusters. Si es la MISMA capa
            # (se le añadió un campo), sigue siendo el objetivo.
            "target_layer_id": capa_de_dataset(working, res.ref.id) if misma else None,
            "symbology": None, "raw_data": None, "sql": None, "error": None,
            "data": None, "visualization": None, "python_code": None,
        }
        if misma and geojson is not None and (copia := copia_de_capa_actualizada(working, res.ref.id, geojson)):
            delta["map_layers"] = copia
    return ToolOutcome(json.dumps(obs, ensure_ascii=False, default=str), success=True, delta=delta)


def _nota_seleccion(working: dict, args: dict) -> str | None:
    """FH.2 (V5): la herramienta operó sobre la capa ENTERA de la que el usuario tiene
    elementos seleccionados. Es un hecho para narrar el alcance (o usar `seleccion`)."""
    from geo_copilot.platform.seleccion import SELECCION, seleccion_actual

    sel = seleccion_actual(working)
    if sel is None:
        return None
    usados = {str(v).strip().strip("[]") for v in args.values() if isinstance(v, str)}
    if SELECCION in usados:
        return None
    capa: dict[str, Any] = next((c for c in ((working.get("map_context") or {}).get("layers") or [])
                 if c.get("id") == sel.layer_id), {})
    propios = {sel.layer_id, sel.dataset_id} | ({"activa"} if capa.get("is_active") else set())
    if not usados & propios:
        return None
    n = sel.count if sel.count is not None else len(sel.ids or ())
    nota = (f"esto operó sobre la capa ENTERA «{sel.layer_name}»; el usuario tiene {n} elemento(s) "
            f"SELECCIONADOS en ella (por {sel.origin}) — `seleccion` es solo esos")
    if (working.get("map_context") or {}).get("alcance_seleccion"):
        # V3 FH.14: con el chip puesto y tras «Se encontraron 27 lotes», «¿cuántas hectáreas suman?»
        # se respondía con los 27. Solo aquí (cuando ya se usó la capa entera) se recuerda el chip:
        # como pista general junto al mensaje hacía usar la selección hasta para «la capa azul».
        nota += (f". Al enviar el mensaje tenía puesto el chip «{n} seleccionados» (su alcance): si el "
                 f"pedido no nombró la capa entera ni otra cosa, repítelo con `seleccion`")
    return nota


async def _listado(session_id: str) -> str:
    store = store_actual()
    if store is None:
        return ""
    refs = await store.list_datasets(session_id)
    if not refs:
        return "La sesión no tiene datasets en el workspace."
    return "Datasets de la sesión: " + "; ".join(f"{r.id} «{r.name}»" for r in refs)


async def _dataset_de_figura(cual: str, working: dict) -> str | None:
    """El punto marcado o la zona visible como dataset del workspace (para las ws_*)."""
    cache = working.setdefault("_figuras", {})
    if cual in cache:
        return str(cache[cual])
    mc = working.get("map_context") or {}
    store, session_id = store_actual(), working.get("session_id") or ""
    if store is None or not session_id:
        return None
    if cual == "punto":
        pt = mc.get("clicked_point") or {}
        if pt.get("lon") is None or pt.get("lat") is None:
            return None
        geom = {"type": "Point", "coordinates": [pt["lon"], pt["lat"]]}
        nombre = f"Punto marcado ({pt['lon']:.5f}, {pt['lat']:.5f})"
    else:
        b = (mc.get("viewport") or {}).get("bbox") or []
        if len(b) != 4:
            return None
        geom = {"type": "Polygon", "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}
        nombre = "Zona visible del mapa"
    from datetime import UTC, datetime

    from geo_copilot.platform.contracts import Provenance

    ref = await store.ingest_features(
        session_id, nombre, {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geom}]},
        crs="EPSG:4326", provenance=Provenance(capability=f"core.{cual}", produced_at=datetime.now(UTC)))
    cache[cual] = ref.id
    return ref.id


# ---------------------------------------------------------------------------
# Ejecutores
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

#: FH.8: qué reciben del mapa (una capa del workspace, lo seleccionado o la activa).
_CAPA: dict[str, Any] = {"accepts": ["dataset"]}
_CON_TAMANO: dict[str, Any] = {"accepts": ["dataset"],
                               "geometry_types": ["Polygon", "MultiPolygon", "LineString", "MultiLineString"]}

ESPACIALES: tuple[Capability, ...] = (
    Capability(
        id="core.measure", tool_name="ws_measure",
        description=(
            "Mide un dataset del workspace con precisión geodésica: número de elementos, "
            "área total (m² y ha, sin contar dos veces los solapes) y longitud total "
            "(m y km). Úsala para 'área total', 'cuántas hectáreas', 'longitud de las vías'."
        ),
        parameters=_params({"dataset": _DATASET}, ["dataset"]),
        executor=_medir, available=_hay_workspace, geo_inputs={"dataset": _CAPA},
        blurb="área total (m²/ha) / longitud (m/km) / conteo de un dataset, exacto.",
        risk="read", step=("agent_loop", "Midiendo en el workspace"),
    ),
    Capability(
        id="core.add_measure", tool_name="ws_add_measure",
        description=(
            "Añade a CADA elemento de un dataset del workspace su medida geodésica exacta como un "
            "campo más de la MISMA capa: `area` → area_m2, `longitud` → longitud_m, `perimetro` → "
            "perimetro_m. La capa del mapa se actualiza en su sitio (no crea otra). Úsala para "
            "'el área de cada lote', o antes de colorear, filtrar o seleccionar por área."
        ),
        parameters=_params({
            "dataset": _DATASET,
            "measure": {"type": "string", "enum": ["area", "longitud", "perimetro"]},
        }, ["dataset", "measure"]),
        executor=_agregar_medida, available=_hay_workspace, geo_inputs={"dataset": _CON_TAMANO},
        blurb="área / longitud / perímetro de CADA elemento como campo de la misma capa, exacto.",
        risk="compute", step=("agent_loop", "Midiendo cada elemento"),
    ),
    Capability(
        id="core.buffer", tool_name="ws_buffer",
        description=(
            "Buffer MÉTRICO exacto (geodésico) de un dataset del workspace; crea un dataset "
            "nuevo y devuelve su área total. `dissolve=true` une las zonas que se solapan "
            "(para 'el área cubierta'); false mantiene un buffer por elemento. Lo que CRECE es "
            "`dataset`: para «qué X hay a N m de aquí» crece el `punto` (o usa `ws_spatial_join` "
            "`dwithin` N contra `punto`), no la capa X que se quiere contar."
        ),
        parameters=_params({
            "dataset": _DATASET,
            "meters": {"type": "number", "description": "Distancia en metros (0 < m ≤ 100000)."},
            "dissolve": {"type": "boolean", "description": "Unir los buffers solapados."},
        }, ["dataset", "meters", "dissolve"]),
        executor=_buffer, available=_hay_workspace, geo_inputs={"dataset": _CAPA},
        blurb="buffer de N metros exacto sobre un dataset (+ área total).",
        risk="compute", step=("agent_loop", "Buffer métrico en el workspace"),
    ),
    Capability(
        id="core.overlay", tool_name="ws_overlay",
        description=(
            "Superposición de dos datasets del workspace: `intersection` (la parte común, "
            "con atributos de ambos como a_*/b_*) o `difference` (A menos lo que cubre B). "
            "Crea un dataset nuevo y devuelve su área."
        ),
        parameters=_params({
            "dataset_a": _DATASET, "dataset_b": _DATASET,
            "mode": {"type": "string", "enum": ["intersection", "difference"]},
        }, ["dataset_a", "dataset_b", "mode"]),
        executor=_overlay, available=_hay_workspace, geo_inputs={"dataset_a": _CAPA, "dataset_b": _CAPA},
        blurb="intersección / diferencia entre dos datasets.",
        risk="compute", step=("agent_loop", "Superposición en el workspace"),
    ),
    Capability(
        id="core.spatial_join", tool_name="ws_spatial_join",
        description=(
            "Unión espacial: cada elemento de A con los atributos de los elementos de B que "
            "cumplen el predicado (`intersects`, `within` = A dentro de B, `contains` = A "
            "contiene a B, `dwithin` = a menos de `meters` metros DEL BORDE de B). Una fila por "
            "pareja; devuelve cuántos elementos de A tienen pareja. Para 'qué X caen en Y' cuando "
            "A y B YA son datasets del workspace; con una tabla de la BD usa `query_database`. "
            "«A menos de N m de aquí» = B `punto` con `dwithin` y N metros (las distancias se "
            "SUMAN: `dwithin` N m sobre un círculo o buffer de N m ya creado mide 2·N desde el "
            "centro; sobre un buffer usa `intersects`)."
        ),
        parameters=_params({
            "dataset_a": _DATASET, "dataset_b": _DATASET,
            "predicate": {"type": "string", "enum": ["intersects", "within", "contains", "dwithin"]},
            "meters": {"type": ["number", "null"],
                       "description": "Solo para dwithin: distancia al borde de B (no al centro)."},
        }, ["dataset_a", "dataset_b", "predicate", "meters"]),
        executor=_join, available=_hay_workspace, geo_inputs={"dataset_a": _CAPA, "dataset_b": _CAPA},
        blurb="qué elementos de A caen en/cerca de B, con atributos de ambos.",
        risk="compute", step=("agent_loop", "Unión espacial en el workspace"),
    ),
    Capability(
        id="core.aggregate", tool_name="ws_aggregate_by_zone",
        description=(
            "Agregación por zonas: para cada polígono de `zones`, cuántos elementos de `data` "
            "lo intersecan (`count`) o la suma/media/mín/máx de un campo numérico de `data`. "
            "Las zonas sin datos quedan en el resultado. Para 'cuántos X por Y', 'total por barrio'."
        ),
        parameters=_params({
            "zones": _DATASET, "data": _DATASET,
            "statistic": {"type": "string", "enum": ["count", "sum", "mean", "min", "max"]},
            "field": {"type": ["string", "null"], "description": "Campo numérico de `data` (no para count)."},
        }, ["zones", "data", "statistic", "field"]),
        executor=_agregar, available=_hay_workspace, geo_inputs={"zones": _CAPA, "data": _CAPA},
        blurb="conteo / suma / media de un dataset por las zonas de otro.",
        risk="compute", step=("agent_loop", "Agregación por zonas"),
    ),
    Capability(
        id="core.autocorrelation", tool_name="ws_spatial_autocorrelation",
        description=(
            "Autocorrelación espacial de un campo numérico: Moran global y, por elemento, "
            "`lisa` (clusters HH/LL y atípicos HL/LH) o `gi` (Getis-Ord Gi*: puntos calientes/"
            "fríos). p por 999 permutaciones, significancia p<0.05. Crea un dataset con la "
            "clase de cada elemento (lista para simbolizar)."
        ),
        parameters=_params({
            "dataset": _DATASET,
            "field": {"type": "string", "description": "Campo numérico a analizar."},
            "method": {"type": "string", "enum": ["lisa", "gi"]},
        }, ["dataset", "field", "method"]),
        executor=_autocorrelacion, available=_hay_workspace, geo_inputs={"dataset": _CAPA},
        blurb="clusters LISA / hotspots Gi* de un campo numérico.",
        risk="compute", cost="medium", step=("agent_loop", "Autocorrelación espacial"),
    ),
)


def _subconjunto_de_la_bd(sql: str | None) -> str | None:
    """Las tablas de las que una capa es un SUBCONJUNTO filtrado (su SQL tiene WHERE), o None.

    Es un hecho de su procedencia, no una deducción: sin él, el agente contó sobre la capa
    cargada y lo narró como el total de la tabla.
    """
    if not sql:
        return None
    try:
        import sqlglot
        from sqlglot import exp

        arbol = sqlglot.parse_one(sql, read="postgres")
    except Exception:  # noqa: BLE001 — un SQL que no se entiende no aporta el hecho (no se inventa)
        return None
    if arbol is None or arbol.find(exp.Where) is None:
        return None
    tablas = sorted({".".join(p for p in (t.db, t.name) if p) for t in arbol.find_all(exp.Table) if t.name})
    return ", ".join(tablas) or None


async def _subconjuntos(store: Any, session_id: str, entradas: tuple[str, ...]) -> list[str]:
    """Qué entradas de la operación son SUBCONJUNTOS filtrados de una tabla de la BD (hecho)."""
    salida = []
    for ds in entradas:
        if not isinstance(ds, str) or not ds.startswith("ds_"):
            continue
        try:
            ref = await store.get(session_id, ds)
        except Exception:  # noqa: BLE001 — sin la procedencia no se afirma nada
            continue
        tablas = _subconjunto_de_la_bd(getattr(getattr(ref, "provenance", None), "sql", None)) if ref else None
        if tablas:
            salida.append(f"«{ref.name}» es solo un SUBCONJUNTO de {tablas} (su consulta tenía filtro): este "
                          "resultado NO cuenta la tabla completa; para la tabla, consúltala con query_database")
    return salida
