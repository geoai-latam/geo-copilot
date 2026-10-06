"""Capacidad `core.map_command` (FH.1): el agente opera el mapa compartido.

Cada gesto manual del panel de capas (subir/bajar, ocultar, opacidad, etiquetas,
encuadrar, quitar) tiene aquí su orden equivalente, y las dos pasan por el MISMO
reducer del frontend: quedan en el mismo registro y se deshacen igual.

El LLM decide qué operación y con qué argumentos; el código solo comprueba
HECHOS: que la orden cumple el contrato (`MapCommand`) y que la capa existe en el
mapa. Una orden inválida vuelve al LLM con el motivo, para que la corrija.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

from geo_copilot.platform.capabilities import Capability, ToolOutcome
from geo_copilot.platform.contracts import MapCommand, RequestInput

_ORDEN: TypeAdapter[Any] = TypeAdapter(MapCommand)

#: `activa` = la capa en foco: la que trajo este turno o, si no, la activa del mapa.
ACTIVA = "activa"


def _capas_del_mapa(working: dict) -> dict[str, str]:
    capas = ((working.get("map_context") or {}).get("layers") or [])
    return {str(c.get("id")): str(c.get("name") or "") for c in capas if c.get("id")}


def _id(valor: Any) -> Any:
    """`[layer-1]` → `layer-1`: el listado del prompt muestra los ids entre corchetes."""
    return valor.strip().strip("[]").strip() if isinstance(valor, str) else valor


def _capa_activa(working: dict) -> str:
    """Nombre de la capa en foco, para narrar (la del turno o la activa del mapa)."""
    if working.get("layer_name"):
        return str(working["layer_name"])
    capas = ((working.get("map_context") or {}).get("layers") or [])
    activa = next((c for c in capas if c.get("is_active")), capas[-1] if capas else {})
    return str(activa.get("name") or ACTIVA)


def _capa_de(valor: Any, working: dict) -> Any:
    """El id de capa para una referencia: `[id]`, o un dataset `ds_…` (como en las demás
    herramientas): la capa del mapa que lo lleva o, si es el que produjo ESTE turno
    (aún no está en el mapa), `activa` (V5 FH.3: «zoom_to ds_…» se rechazaba)."""
    v = _id(valor)
    if not (isinstance(v, str) and v.startswith("ds_")):
        return v
    for c in ((working.get("map_context") or {}).get("layers") or []):
        if c.get("dataset_id") == v:
            return c.get("id")
    if (working.get("result_layer_ref") or {}).get("id") == v:
        return ACTIVA
    return v


#: FH.10: órdenes de la vista (marcador, cortina, tiempo), sin capa objetivo.
_DE_VISTA = frozenset({"save_view", "compare", "end_compare", "set_time"})


async def _map_command(graph: Any, working: dict, args: dict) -> ToolOutcome:  # noqa: C901, PLR0912, PLR0915
    orden = {k: v for k, v in args.items() if v is not None}
    if "layer_id" in orden:
        orden["layer_id"] = _capa_de(orden["layer_id"], working)
    if isinstance(orden.get("args"), dict) and "relative_to" in orden["args"]:
        orden["args"] = {**orden["args"], "relative_to": _capa_de(orden["args"]["relative_to"], working)}
    orden.setdefault("args", {})
    if orden.get("op") in _DE_VISTA:
        # FH.10: no actúan sobre UNA capa (V5 LLM: `compare` con layer_id = un raster de este turno
        # se rechazaba por «no está en el mapa»)
        orden.pop("layer_id", None)
    try:
        cmd = _ORDEN.validate_python(orden)
    except ValidationError as exc:
        errores = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:4])
        return ToolOutcome(f"orden al mapa inválida ({errores}); revisa la operación y sus argumentos",
                           success=False)
    capas = _capas_del_mapa(working)
    referidas = [cmd.layer_id]
    if cmd.op == "reorder":
        referidas.append(cmd.args.relative_to)
    for lid in referidas:
        if lid and lid != ACTIVA and lid not in capas:
            validas = ", ".join(f"[{i}] «{n}»" for i, n in capas.items()) or "(el mapa no tiene capas)"
            return ToolOutcome(f"la capa [{lid}] no está en el mapa. Capas: {validas}; o `{ACTIVA}`",
                               success=False)
    if cmd.op == "compare":
        # FH.10: cada lado es una capa del mapa ([id] o nombre exacto) o un raster de ESTE turno
        del_turno = [str(i.get("name")) for i in [*(working.get("imagery_previas") or []),
                                                   working.get("external_imagery") or {}] if i.get("name")]
        nombres = {n.lower() for n in [*capas.values(), *del_turno]}
        for lado in (cmd.args.left, cmd.args.right):
            if lado != ACTIVA and lado not in capas and lado.lower() not in nombres:
                validas = ", ".join([*(f"[{i}] «{n}»" for i, n in capas.items()), *(f"«{n}» (de este turno)" for n in del_turno)])
                return ToolOutcome(f"`{lado}` no es una capa del mapa ni de este turno. Capas: {validas or '(ninguna)'}",
                                   success=False)
    if cmd.op not in ("zoom_to", "clear_selection", *_DE_VISTA) and not cmd.layer_id:
        return ToolOutcome(f"`{cmd.op}` necesita `layer_id` (una capa del mapa o `{ACTIVA}`)", success=False)
    if cmd.op == "zoom_to" and not cmd.layer_id and cmd.args.bbox is None:
        return ToolOutcome("`zoom_to` necesita `layer_id` o `args.bbox`", success=False)

    extra: dict[str, Any] = {}
    if cmd.op == "select":
        conteo = await _contar_seleccion(working, cmd)
        if isinstance(conteo, str):
            return ToolOutcome(conteo, success=False)
        cmd = cmd.model_copy(update={"args": cmd.args.model_copy(update={"count": conteo})})
        extra["seleccion_turno"] = {"layer_id": cmd.layer_id, "ids": cmd.args.ids, "count": conteo,
                                    "where": cmd.args.where.model_dump() if cmd.args.where else None,
                                    "origin": "agent"}
        # En este mismo turno, `seleccion` ya es una capa para las demás herramientas.
        from geo_copilot.platform.seleccion import SELECCION, capa_virtual

        mc = {**(working.get("map_context") or {})}
        mc["layers"] = [{**c, "seleccion": extra["seleccion_turno"]} if c.get("id") == cmd.layer_id
                        else {k: v for k, v in c.items() if k != "seleccion"}
                        for c in (mc.get("layers") or [])]
        virtual = capa_virtual(working.get("map_layers") or {}, mc)
        capas_turno = {k: v for k, v in (working.get("map_layers") or {}).items() if k != SELECCION}
        extra["map_layers"] = {**capas_turno, **({SELECCION: virtual} if virtual else {})}
        if conteo == 0:
            rango = await _valores_del_campo(working, cmd)
            return ToolOutcome(f"ningún elemento de «{capas.get(cmd.layer_id or '', cmd.layer_id)}» cumple "
                               f"esa condición: no se seleccionó nada{rango}{_REPITE}", success=True)
    elif cmd.op == "set_filter":
        condiciones = [c.model_dump() for c in cmd.args.where]
        conteo = await _contar_filtro(working, cmd.layer_id, condiciones)
        if isinstance(conteo, str):
            return ToolOutcome(conteo, success=False)
        cmd = cmd.model_copy(update={"args": cmd.args.model_copy(update={"count": conteo})})
        # En este mismo turno la capa ya es su subconjunto para las demás herramientas.
        capa_id = cmd.layer_id if cmd.layer_id != ACTIVA else _id_activa(working)
        from geo_copilot.platform.seleccion import filtrar_capa

        mc = {**(working.get("map_context") or {})}
        mc["layers"] = [{**c, "filtro": condiciones or None, "filtro_count": conteo} if c.get("id") == capa_id
                        else c for c in (mc.get("layers") or [])]
        extra["map_context"] = mc
        entrada = (working.get("map_layers") or {}).get(capa_id)
        if isinstance(entrada, dict) and isinstance(entrada.get("completa") or entrada.get("data"), dict):
            completa = entrada.get("completa") or entrada["data"]
            nueva = {**entrada, "completa": completa,
                     "data": filtrar_capa(completa, condiciones) if condiciones else completa}
            extra["map_layers"] = {**(working.get("map_layers") or {}), capa_id: nueva}
        extra["_filtrados"] = {}  # el subconjunto materializado del filtro anterior ya no vale
        if condiciones and conteo == 0:
            valores = "".join([await _valores_del_campo(working, cmd, c["field"]) for c in condiciones])
            return ToolOutcome(f"ningún elemento de «{capas.get(capa_id or '', capa_id)}» cumple ese filtro: "
                               f"no se aplicó (el mapa quedaría vacío){valores}{_REPITE}", success=True)
    elif cmd.op == "clear_selection":
        extra["seleccion_turno"] = {"cleared": True}
        extra["map_layers"] = {k: v for k, v in (working.get("map_layers") or {}).items() if k != "seleccion"}

    hecho = cmd.model_dump(mode="json")
    nombre = _capa_activa(working) if cmd.layer_id == ACTIVA else capas.get(cmd.layer_id or "", cmd.layer_id or "")
    return ToolOutcome(
        f"orden aplicada al mapa del usuario: {cmd.op} sobre «{nombre}» "
        f"{hecho['args'] or ''}. El usuario la ve y puede deshacerla (Ctrl+Z).",
        success=True,
        delta={"map_commands": [*(working.get("map_commands") or []), hecho], **extra},
    )


async def _contar_filtro(working: dict, layer_id: str | None, condiciones: list[dict]) -> int | str:
    """Cuántos elementos de la capa ENTERA cumplen el filtro, o el motivo por el que no se puede."""
    from geo_copilot.platform.seleccion import PredicadoInvalido, cumple_todas, sql_filtro

    capa_id = layer_id if layer_id != ACTIVA else _id_activa(working)
    capa: dict[str, Any] = next((c for c in ((working.get("map_context") or {}).get("layers") or [])
                                 if c.get("id") == capa_id), {})
    campos = [str(f) for f in (capa.get("fields") or [])]
    faltan = [c["field"] for c in condiciones if campos and c["field"] not in campos]
    if faltan:
        return f"el campo «{faltan[0]}» no existe en «{capa.get('name')}»; campos: {', '.join(campos[:20])}"
    entrada = (working.get("map_layers") or {}).get(capa_id) or {}
    datos = entrada.get("completa") or entrada.get("data")
    if not condiciones:
        return int(capa.get("feature_count") or 0)
    if isinstance(datos, dict):
        return sum(1 for f in (datos.get("features") or []) if isinstance(f, dict)
                   and cumple_todas(condiciones, f.get("properties") or {}))
    from geo_copilot.platform.workspace.context import store_actual

    store, ds = store_actual(), capa.get("dataset_id")
    if not ds or store is None or not working.get("session_id"):
        return "no hay datos de esa capa para evaluar el filtro"
    ref = await store.get(working["session_id"], ds)
    if ref is None:
        return "el dataset de esa capa no está en esta sesión"
    from geo_copilot.platform.workspace.ops import _tabla

    try:
        cond, params = sql_filtro(condiciones, [f.name for f in ref.fields])
    except PredicadoInvalido as exc:
        return str(exc)
    hechos = await store.hechos(working["session_id"], f"SELECT count(*) AS n FROM {_tabla(ref)} s WHERE {cond}", params)
    return int(hechos.get("n") or 0)


async def _contar_seleccion(working: dict, cmd: Any) -> int | str:
    """Cuántos elementos quedan seleccionados, o el motivo por el que no se puede (hecho)."""
    from geo_copilot.platform.seleccion import PredicadoInvalido, Seleccion, filtrar, sql_where

    if cmd.args.ids is not None:
        return len(set(cmd.args.ids))
    if cmd.args.where is None:
        return "`select` necesita `args.ids` o `args.where`"
    where = cmd.args.where.model_dump()
    capa_id = cmd.layer_id if cmd.layer_id != ACTIVA else _id_activa(working)
    datos = ((working.get("map_layers") or {}).get(capa_id) or {}).get("data")
    if isinstance(datos, dict):
        sel = Seleccion(capa_id or "", "", None, where, None, "agent", None)
        return len(filtrar(datos, sel)["features"])
    capa: dict[str, Any] = next((c for c in ((working.get("map_context") or {}).get("layers") or []) if c.get("id") == capa_id), {})
    ds = capa.get("dataset_id")
    from geo_copilot.platform.workspace.context import store_actual

    store = store_actual()
    if not ds or store is None or not working.get("session_id"):
        return "no hay datos de esa capa para evaluar la condición"
    ref = await store.get(working["session_id"], ds)
    if ref is None:
        return "el dataset de esa capa no está en esta sesión"
    from geo_copilot.platform.workspace.ops import _tabla

    try:
        cond, params = sql_where(where, None, [f.name for f in ref.fields])
    except PredicadoInvalido as exc:
        return str(exc)
    hechos = await store.hechos(working["session_id"], f"SELECT count(*) AS n FROM {_tabla(ref)} s WHERE {cond}", params)
    return int(hechos.get("n") or 0)


#: Modelo gpt-5.4 (2026-10-05): con «uso = residencial» y el dato en código (`res`) respondía «si quieres, lo
#: filtro por res» en vez de hacerlo. Va en el resultado (solo en este caso), no como regla general del
#: prompt: una regla general rompía «seleccionar ≠ filtrar» con gpt-4.1-mini (1/3, sin ella 3/3).
_REPITE = (". Si uno de esos valores es sin duda el que se pidió (p. ej. el código de la palabra), repite "
           "la orden con él tú mismo; no le preguntes al usuario si quiere que lo hagas")


async def _valores_del_campo(working: dict, cmd: Any, campo: str | None = None) -> str:
    """Qué valores toma el campo de la condición (hecho para juzgar un «no se seleccionó nada»)."""
    if campo is None:
        where = getattr(cmd.args, "where", None)
        campo = where.field if where is not None and not isinstance(where, list) else None
    if not campo:
        return ""
    capa_id = cmd.layer_id if cmd.layer_id != ACTIVA else _id_activa(working)
    entrada = (working.get("map_layers") or {}).get(capa_id) or {}
    datos = entrada.get("completa") or entrada.get("data")
    valores: list[Any] = []
    if isinstance(datos, dict):
        valores = [(f.get("properties") or {}).get(campo) for f in (datos.get("features") or [])
                   if isinstance(f, dict)]
    else:
        from geo_copilot.platform.workspace.context import store_actual

        capa: dict[str, Any] = next((c for c in ((working.get("map_context") or {}).get("layers") or [])
                     if c.get("id") == capa_id), {})
        store, ds = store_actual(), capa.get("dataset_id")
        if not ds or store is None or not working.get("session_id"):
            return ""
        ref = await store.get(working["session_id"], ds)
        if ref is None or campo not in [f.name for f in ref.fields]:
            return ""
        from geo_copilot.platform.workspace.ops import _tabla
        from geo_copilot.platform.workspace.store import _qi

        filas = await store.filas(working["session_id"],
                                  f"SELECT DISTINCT {_qi(campo)} AS v FROM {_tabla(ref)} LIMIT 200")
        valores = [f.get("v") for f in filas]
    valores = [v for v in valores if v is not None]
    if not valores:
        return f"; «{campo}» está vacío en esa capa"
    try:
        nums = [float(v) for v in valores if not isinstance(v, bool)]
    except (TypeError, ValueError):
        nums = []
    if nums and len(nums) == len(valores):
        return f"; «{campo}» va de {min(nums):g} a {max(nums):g}"
    distintos = list(dict.fromkeys(str(v) for v in valores))
    return f"; «{campo}» toma valores como: {', '.join(distintos[:8])}" + ("…" if len(distintos) > 8 else "")


def _id_activa(working: dict) -> str | None:
    capas = ((working.get("map_context") or {}).get("layers") or [])
    activa = next((c for c in capas if c.get("is_active")), capas[-1] if capas else {})
    return activa.get("id")


_PEDIDO = TypeAdapter(RequestInput)


async def _pedir_en_el_mapa(graph: Any, working: dict, args: dict) -> ToolOutcome:
    """FH.9: termina el turno pidiendo algo en el mapa; la respuesta llega en el turno siguiente."""
    orden = {"op": "request_input", "args": {"mode": args.get("mode"), "prompt": args.get("prompt")}}
    if args.get("layer_id"):
        orden["layer_id"] = _capa_de(args["layer_id"], working)
    try:
        cmd = _PEDIDO.validate_python(orden)
    except ValidationError as exc:
        errores = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:4])
        return ToolOutcome(f"pedido inválido ({errores})", success=False)
    if cmd.args.mode in ("pick_layer", "pick_features") and not _capas_del_mapa(working):
        # V5: con el mapa vacío pidió «elige una capa»: no hay nada que elegir
        return ToolOutcome("el mapa no tiene capas: no hay nada que elegir ahí. Pregunta con `answer` o "
                           "pide un punto/un área", success=False)
    if cmd.layer_id and cmd.layer_id != ACTIVA and cmd.layer_id not in _capas_del_mapa(working):
        return ToolOutcome(f"la capa [{cmd.layer_id}] no está en el mapa", success=False)
    hecho = cmd.model_dump(mode="json")
    return ToolOutcome(
        f"pedido al usuario en el mapa: {cmd.args.mode}", success=True, is_final=True,
        final_text=cmd.args.prompt,
        delta={"map_commands": [*(working.get("map_commands") or []), hecho]},
    )


MAPA = (
    Capability(
        id="core.map_command", tool_name="map_command",
        description=(
            "Opera el MAPA que el usuario ve, igual que él con el panel de capas: encuadrar una capa o "
            "una zona (zoom_to), mostrarla u ocultarla (set_visibility), su transparencia (set_opacity), "
            "ponerla encima o debajo de otra (reorder), escribir un campo como etiqueta (set_label) o "
            "quitarla del mapa (remove_layer), seleccionar elementos por una condición (select) o quitar "
            "la selección (clear_selection), o filtrar la capa (set_filter): mostrar solo los elementos "
            "que cumplen unas condiciones; la capa filtrada ES ese subconjunto también para las demás "
            "herramientas, y el usuario puede editar el filtro; guardar la vista como marcador (save_view); "
            "comparar dos capas con una cortina (compare / end_compare); o mover el control de tiempo de una "
            "serie con fecha (set_time). No cambia los datos. Para colorear/clasificar usa "
            "apply_symbology. Cada orden queda en el registro del mapa y el usuario puede deshacerla."
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["zoom_to", "set_visibility", "set_opacity", "reorder",
                                                    "set_label", "remove_layer", "select", "clear_selection",
                                                    "set_filter", "save_view", "compare", "end_compare",
                                                    "set_time"]},
                "layer_id": {"type": ["string", "null"],
                             "description": "[id] de 'CAPAS EN EL MAPA', o `activa` (la capa de este turno "
                             "o la activa). zoom_to puede omitirlo si da args.bbox."},
                "args": {
                    "type": "object",
                    "description": "Según op — set_visibility: {visible: bool}; set_opacity: {opacity: 0..1}; "
                    "reorder: {to: top|bottom|above|below, relative_to: [id] si above/below}; "
                    "set_label: {field: nombre de campo o null para quitar}; "
                    "zoom_to: {bbox: [minx, miny, maxx, maxy] en EPSG:4326} o {}; remove_layer: {}; "
                    "select: {where: {field, op: = != > >= < <= in contains, value}} (o {ids: [...]}) — marca "
                    "elementos para que el usuario los vea y para operar luego sobre `seleccion`; `field` es "
                    "un CAMPO de la capa (los de 'CAPAS EN EL MAPA'): el área o la longitud de la geometría no "
                    "son campos si la capa no los trae; "
                    "clear_selection: {} (sin layer_id: todas las capas); "
                    "set_filter: {where: [{field, op, value}, …]} (todas se cumplen; [] quita el filtro); "
                    "save_view: {nombre} (guarda lo que se ve como marcador; ir a uno: zoom_to con su bbox); "
                    "compare: {left, right} (cortina entre dos capas: [id], nombre exacto o un raster de este "
                    "turno, p. ej. el NDVI de dos fechas); end_compare: {}; "
                    "set_time: {time: fecha ISO de la serie, play: true para animarla} (capas con fecha).",
                },
                "reason": {"type": "string", "description": "Por qué, en una frase (se muestra en el registro)."},
            },
            "required": ["op"],
            "additionalProperties": False,
        },
        executor=_map_command,
        blurb="opera el mapa del usuario: encuadrar, mostrar/ocultar, opacidad, orden, etiquetas, quitar capa.",
        step=("agent_loop", "Operando el mapa"),
    ),
    Capability(
        id="core.request_map_input", tool_name="request_map_input",
        description=(
            "Pide al usuario que SEÑALE algo en el mapa y TERMINA el turno: marcar un punto "
            "(pick_point), dibujar un área (draw_area), elegir una capa (pick_layer) o seleccionar "
            "elementos (pick_features). Úsala cuando para responder te falta DÓNDE o SOBRE QUÉ y no "
            "está en el mapa (sin punto marcado, dibujo, selección ni capa que lo resuelva). Un lugar "
            "que el usuario NOMBRÓ (una dirección, un sitio, un barrio) ya es el DÓNDE: si tienes una "
            "herramienta que convierta un nombre de lugar en coordenadas o geometría (un geocodificador, "
            "o una que acepte el lugar por su nombre), úsala antes; si no la tienes, no lo busques en "
            "tablas que no son de lugares: pídelo en el mapa. Cuando el usuario responda, la consulta "
            "vuelve con su respuesta en el mapa. Pedirlo solo con texto («márcalo en el mapa») NO pone el "
            "mapa en modo de marcar: para eso es esta herramienta."
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["pick_point", "draw_area", "pick_layer", "pick_features"]},
                "prompt": {"type": "string", "description": "La pregunta al usuario, corta y concreta."},
                "layer_id": {"type": ["string", "null"],
                             "description": "Solo pick_features: la capa donde elegir ([id] del mapa)."},
            },
            "required": ["mode", "prompt"],
            "additionalProperties": False,
        },
        executor=_pedir_en_el_mapa,
        blurb="pide al usuario un punto / un área / una capa / elementos en el mapa (termina el turno).",
        step=("agent_loop", "Pidiéndote algo en el mapa"),
    ),
)
