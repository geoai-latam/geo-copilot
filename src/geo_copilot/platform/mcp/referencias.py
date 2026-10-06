"""Las REFERENCIAS geo de las herramientas MCP: el LLM pasa `activa`, `ds_…`, una capa del mapa,
`seleccion`, `viewport`, `dibujo` o `punto`, y el núcleo pone la geometría (nunca la inventa el
LLM). También los topes de lo que viaja (a un servicio y de vuelta al LLM).

Salió de `hub.py` (F4 del plan de calidad: 1.097 líneas), tal cual.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.platform.mcp.hub import EstadoTool

logger = get_logger(__name__)


_MAX_OBS = 3000  # caracteres de salida externa que ve el LLM por llamada (MCP_OBS_MAX_CHARS)


_MAX_GEO_ENTRADA = 5000  # elementos de una capa enviada a un servicio (MCP_GEO_MAX_FEATURES)


def _max_obs() -> int:
    from geo_copilot.core.config import get_settings

    v = getattr(get_settings(), "mcp_obs_max_chars", _MAX_OBS)
    return v if isinstance(v, int) and v > 0 else _MAX_OBS


def _max_geo() -> int:
    from geo_copilot.core.config import get_settings

    v = getattr(get_settings(), "mcp_geo_max_features", _MAX_GEO_ENTRADA)
    return v if isinstance(v, int) and v > 0 else _MAX_GEO_ENTRADA


class _CapaDemasiadoGrande(Exception):
    def __init__(self, n: int, maximo: int, bbox: Any = None) -> None:
        super().__init__(f"{n}>{maximo}")
        self.n, self.maximo, self.bbox = n, maximo, bbox


_REFERENCIA_GEO = {
    "type": "string",
    "description": (
        "Referencia a una capa, NO geometría escrita a mano: `activa` (la capa de trabajo "
        "actual, p. ej. lo que acabas de consultar), el id `ds_…` de un dataset del "
        "workspace, el [id] (o el nombre exacto) de una capa de 'CAPAS EN EL MAPA', `seleccion` (solo lo "
        "SELECCIONADO en el mapa), `viewport` (la zona visible del mapa), `dibujo` (lo último que "
        "DIBUJÓ el usuario) o `punto` (el PUNTO MARCADO por el usuario: su «aquí»)."
    ),
}


def _admite_geometria(prop: Any) -> bool:
    """¿El tipo del servidor puede llevar una geometría? (un número nunca lo es)."""
    if not isinstance(prop, dict):
        return False
    tipos = {prop.get("type")} | {p.get("type") for p in prop.get("anyOf") or [] if isinstance(p, dict)}
    return bool(tipos & {"object", "array", "string", None}) and not tipos <= {"number", "integer", "null"}


def _esquema_con_referencias(esquema: dict, geo_in: dict) -> dict:
    """El esquema que ve el LLM: los argumentos geo se piden como referencia.

    Con el esquema del servidor (`aoi: object`) el LLM inventaba la geometría (V5
    F3). La geometría real la pone `_resolver_geo`; el servidor recibe lo suyo.
    """
    if not geo_in:
        return esquema
    props = dict(esquema.get("properties") or {})
    for nombre in geo_in:
        if nombre in props and _admite_geometria(props[nombre]):
            props[nombre] = dict(_REFERENCIA_GEO)
    return {**esquema, "properties": props}


def _limitar(texto: str) -> str:
    maximo = _max_obs()
    if len(texto) <= maximo:
        return texto
    return (texto[:maximo] + f"…(RECORTADO: ves {maximo} de {len(texto)} caracteres; el resto NO lo has "
            "visto — no afirmes nada sobre él)")


def _id_por_nombre(valor: str, working: dict) -> str:
    """El [id] de la capa del mapa que se llama EXACTAMENTE así (sin distinguir mayúsculas).

    El LLM nombra la capa como la nombra el usuario («Área 1», V4 FH.3): un nombre que
    identifica una sola capa es una referencia tan inequívoca como su id. Si hay
    dos capas con ese nombre, no se elige: sigue siendo un valor sin resolver.
    """
    v = valor.strip().strip("[]").strip()
    capas = (working.get("map_context") or {}).get("layers") or []
    if any(c.get("id") == v for c in capas):
        return v
    iguales = [c.get("id") for c in capas if str(c.get("name") or "").strip().casefold() == v.casefold()]
    return str(iguales[0]) if len(iguales) == 1 and iguales[0] else v


def _area_del_mapa(valor: str, working: dict) -> dict[str, Any] | None:
    """`punto` (el punto marcado) o `viewport` (la zona visible) como FeatureCollection."""
    if valor == "punto":
        pt = (working.get("map_context") or {}).get("clicked_point") or {}
        if pt.get("lon") is None or pt.get("lat") is None:
            return None
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                "geometry": {"type": "Point", "coordinates": [pt["lon"], pt["lat"]]}}]}
    bbox = (((working.get("map_context") or {}).get("viewport") or {}).get("bbox"))
    if not (bbox and len(bbox) == 4):
        return None
    x0, y0, x1, y1 = bbox
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1],
                                                              [x0, y1], [x0, y0]]]}}]}


async def _seleccion(working: dict) -> tuple[dict[str, Any] | None, str | None]:
    """FH.2: lo seleccionado en el mapa (o por el agente en este turno): (en memoria, o su dataset)."""
    from geo_copilot.platform.seleccion import filtrar, seleccion_actual
    from geo_copilot.platform.workspace.context import store_actual

    sel = seleccion_actual(working)
    if sel is None:
        return None, None
    datos = ((working.get("map_layers") or {}).get(sel.layer_id) or {}).get("data")
    if isinstance(datos, dict):
        sub = filtrar(datos, sel)
        return (sub if sub["features"] else None), None
    store = store_actual()
    if store is None or not working.get("session_id"):
        return None, None
    from geo_copilot.platform.seleccion import dataset_de_seleccion

    return None, await dataset_de_seleccion(store, working["session_id"], sel, working)


async def _desde_workspace(ds_id: str | None, working: dict) -> dict[str, Any] | None:
    """Un dataset del workspace (FH.5: su subconjunto si está filtrado), sin cortar en silencio."""
    from geo_copilot.platform.workspace.context import store_actual

    store = store_actual()
    if not ds_id or store is None or not working.get("session_id"):
        return None
    try:
        from geo_copilot.platform.seleccion import dataset_efectivo

        # FH.5: una capa filtrada es su subconjunto también para los servicios conectados
        ds_id = await dataset_efectivo(store, working["session_id"], ds_id, working)
        maximo = _max_geo()
        fc = await store.to_geojson(working["session_id"], ds_id, limit=maximo + 1)
    except Exception:  # noqa: BLE001 — id inexistente o ajeno: el llamador lo informa
        return None
    if len(fc.get("features") or []) > maximo:
        # Antes se cortaba en silencio a 5000: un NDVI por lote de 20.000 lotes se calculaba
        # sobre la cuarta parte y se narraba como el total.
        try:
            ref = await store.get(working["session_id"], ds_id)
        except Exception:  # noqa: BLE001 — sin la extensión, un bbox tampoco se puede dar
            ref = None
        raise _CapaDemasiadoGrande(len(fc["features"]), maximo, getattr(ref, "bbox", None))
    return fc


async def _geojson_de_referencia(valor: str, working: dict) -> dict[str, Any] | None:
    """La FeatureCollection a la que apunta una referencia de capa, o None.

    `activa` (la misma regla de capa en foco que simbología y python_agent, S1.4),
    `viewport` (la zona visible), `punto` (el punto marcado), el [id] de una capa del mapa o un `ds_…` del
    workspace — incluidas las capas demasiado grandes para ir inline.
    """
    ds_id: str | None = None
    if valor == "dibujo":
        from geo_copilot.orchestrator.layer_resolution import capa_del_ultimo_dibujo

        valor = capa_del_ultimo_dibujo(working) or valor
    if valor == "activa":
        from geo_copilot.orchestrator.layer_resolution import resolver_capa

        foco = resolver_capa({**working, "target_layer_id": None}, con_features=True)
        if foco is not None:
            return foco.geojson
        ds_id = (working.get("result_layer_ref") or {}).get("id")
    elif valor == "seleccion":
        en_memoria, ds_id = await _seleccion(working)
        if en_memoria is not None or ds_id is None:
            return en_memoria
    elif valor in ("punto", "viewport"):
        return _area_del_mapa(valor, working)
    else:
        valor = _id_por_nombre(valor, working)
        capa = (working.get("map_layers") or {}).get(valor) or {}
        if isinstance(capa.get("data"), dict) and capa["data"]:
            return cast(dict[str, Any], capa["data"])
        ds_id = valor if valor.startswith("ds_") else next(
            (lyr.get("dataset_id") for lyr in ((working.get("map_context") or {}).get("layers") or [])
             if lyr.get("id") == valor and lyr.get("dataset_id")), None)
    return await _desde_workspace(ds_id, working)


def _referencias_validas(working: dict) -> str:
    """Las referencias geo que HOY resuelven en esta sesión: el hecho que el agente
    necesita tras un rechazo (V5 F4: escribió el punto a mano, se le rechazó sin
    decirle que existía `punto`, y concluyó que «un punto no se puede medir»)."""
    mc = working.get("map_context") or {}
    refs = ["`activa`"]
    from geo_copilot.platform.seleccion import seleccion_actual

    if (sel := seleccion_actual(working)) is not None:
        refs.append(f"`seleccion` ({sel.count if sel.count is not None else len(sel.ids or ())} de «{sel.layer_name}»)")
    if (mc.get("viewport") or {}).get("bbox"):
        refs.append("`viewport`")
    from geo_copilot.orchestrator.layer_resolution import capa_del_ultimo_dibujo

    if (dib := capa_del_ultimo_dibujo(working)) is not None:
        refs.append(f"`dibujo` ([{dib}], lo último que dibujó el usuario)")
    pt = mc.get("clicked_point") or {}
    if pt.get("lon") is not None and pt.get("lat") is not None:
        refs.append(f"`punto` (marcado en lon {pt['lon']:.5f}, lat {pt['lat']:.5f})")
    capas = [f"`{lyr['id']}` ({lyr.get('name', '')})" for lyr in (mc.get("layers") or [])
             if lyr.get("id") and str(lyr.get("kind") or "vector").startswith("vector")]
    refs += capas[:8]
    return ", ".join(refs) + ". No escribas geometría a mano."


async def _resolver_geo(est: EstadoTool, working: dict, args: dict) -> tuple[dict, str | None]:
    """Sustituye referencias de capa por su geometría/bbox en los argumentos geo declarados."""
    entradas = (est.geo.get("inputs") or {}) if est.geo else {}
    salida = dict(args)
    for nombre, spec in entradas.items():
        valor = salida.get(nombre)
        if not isinstance(valor, str):
            continue
        acepta = set((spec or {}).get("accepts") or [])
        try:
            fc = await _geojson_de_referencia(valor, working)
        except _CapaDemasiadoGrande as exc:
            if "bbox" in acepta and "geometry" not in acepta and exc.bbox:
                # para un bbox no hacen falta los elementos: la extensión de TODO el dataset
                salida[nombre] = list(exc.bbox)
                continue
            else:
                return args, (f"`{valor}` tiene más de {exc.maximo} elementos: el servicio recibiría solo una "
                              "parte y el resultado no sería de toda la capa. No se ejecutó. Acótala "
                              "(`seleccion`, un filtro, `viewport`) o dilo al usuario.")
        if fc is None:
            if acepta & {"geometry", "bbox", "layer_ref"}:
                return args, (f"`{valor}` no es una capa de esta sesión para el argumento `{nombre}`. "
                              f"Referencias válidas ahora: {_referencias_validas(working)}")
            continue
        if "geometry" in acepta:
            salida[nombre] = fc
        elif "bbox" in acepta:
            from shapely.geometry import shape

            geoms = [shape(f["geometry"]) for f in fc.get("features", []) if f.get("geometry")]
            if geoms:
                xs = [g.bounds for g in geoms]
                salida[nombre] = [min(b[0] for b in xs), min(b[1] for b in xs),
                                  max(b[2] for b in xs), max(b[3] for b in xs)]
    return salida, None
