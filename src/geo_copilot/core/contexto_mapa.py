"""El CONTEXTO DEL MAPA que lee el LLM: capas (con su detalle raster, filtros y procedencia),
selección, menciones del usuario, acciones hechas en el mapa y la respuesta pendiente.

Salió de `formatters.py` (F4 del plan de calidad: formatters.py tenía 620 líneas), tal cual.
"""

from geo_sql_guard.crs import (  # noqa: F401
    _METRICOS_QUE_DEFORMAN,
    _describe_crs,
    check_measurable_crs,
    is_metric_crs,
)


def _detalle_raster(lyr: dict) -> str:
    """Hechos de una capa raster para el prompt: procedencia, extensión, rampa, teselas."""
    partes: list[str] = []
    origen = lyr.get("origin") or {}
    if origen.get("capability"):
        args = origen.get("arguments") or {}
        arg_txt = ", ".join(f"{k}={v}" for k, v in list(args.items())[:6])
        partes.append(f"origen {origen['capability']}" + (f"({arg_txt})" if arg_txt else ""))
    if lyr.get("bbox"):
        partes.append("bbox [" + ", ".join(f"{x:.4f}" for x in lyr["bbox"]) + "]")
    ley = lyr.get("legend") or {}
    if ley.get("min") is not None and ley.get("max") is not None:
        partes.append(f"rampa {ley.get('field') or 'valor'} {ley['min']}–{ley['max']}")
    if lyr.get("url"):
        partes.append(f"teselas {lyr['url']}")
    return ("; " + "; ".join(partes)) if partes else ""


def describir_filtro(condiciones: list[dict]) -> str:
    """«estrato = 3 y uso en [res, com]»: un filtro de capa (FH.5) en palabras."""
    def una(c: dict) -> str:
        v = c.get("value")
        if c.get("op") == "in":
            return f"{c.get('field')} en [{', '.join(map(str, v if isinstance(v, list) else [v]))}]"
        if c.get("op") == "contains":
            return f"{c.get('field')} contiene «{v}»"
        return f"{c.get('field')} {c.get('op')} {v}"
    return " y ".join(una(c) for c in condiciones)


def _aspecto(lyr: dict) -> str:
    """Lo que el usuario VE de una capa además de sus datos (FH.1): opacidad, estilo, etiquetas."""
    partes: list[str] = []
    op = lyr.get("opacity")
    if isinstance(op, (int, float)) and op < 1:
        partes.append(f"opacidad {round(op * 100)} %")
    est = lyr.get("style") or {}
    tipo = est.get("symbology_type")
    if tipo:
        campo = est.get("classification_field")
        clases = [c for c in (est.get("class_breaks") or []) if isinstance(c, dict)]
        txt = f"estilo {tipo}" + (f" por «{campo}»" if campo else "")
        if clases:
            txt += f", {len(clases)} clase(s): " + ", ".join(
                f"{c.get('label')}={c.get('color')}" for c in clases[:6]) + ("…" if len(clases) > 6 else "")
        elif est.get("fill_color"):
            txt += f", color {est['fill_color']}"
        detalles = [f"{n} {est[k]}" for k, n in (("classification_method", "método"), ("color_scheme", "rampa"))
                    if est.get(k)]
        if detalles:
            txt += " (" + ", ".join(detalles) + ")"
        fijados = [c for c in (est.get("pinned") or []) if isinstance(c, str)]
        if fijados:
            # FH.6: lo que el usuario fijó a mano en el editor (un hecho; conservarlo lo decide el LLM)
            txt += "; el USUARIO FIJÓ A MANO: " + ", ".join(f"{c}={est.get(c)!r}" for c in fijados)
        partes.append(txt)
    if lyr.get("label_field"):
        partes.append(f"etiquetas con «{lyr['label_field']}»")
    filtro = [c for c in (lyr.get("filtro") or []) if isinstance(c, dict)]
    if filtro:
        cuantos = (f" ({lyr['filtro_count']} de {lyr.get('feature_count')})"
                   if lyr.get("filtro_count") is not None else "")
        partes.append(f"FILTRADA: solo muestra los que cumplen {describir_filtro(filtro)}{cuantos}; "
                      "para las herramientas la capa es ese subconjunto")
    sel = lyr.get("seleccion") or {}
    if sel.get("ids") or sel.get("where"):
        n = sel.get("count") if sel.get("count") is not None else len(sel.get("ids") or [])
        como = (f"los que cumplen {sel['where'].get('field')} {sel['where'].get('op')} {sel['where'].get('value')}"
                if sel.get("where") else "")
        partes.append(f"SELECCIONADOS {n} elemento(s){' — ' + como if como else ''} (por {sel.get('origin', '?')})")
    return ("; " + "; ".join(partes)) if partes else ""


_OPERACION = {
    "add_layer": "añadió la capa", "remove_layer": "quitó la capa", "set_style": "cambió el estilo de",
    "set_visibility": "cambió la visibilidad de", "set_opacity": "cambió la opacidad de",
    "reorder": "cambió el orden de dibujo de", "set_label": "cambió las etiquetas de", "zoom_to": "encuadró",
    "select": "seleccionó elementos de", "clear_selection": "quitó la selección de",
    "rename_layer": "renombró la capa", "edit_geometry": "editó los vértices de",
    "set_filter": "cambió el filtro de",
}


#: Procedencias que tienen nombre para una persona (el resto se muestra tal cual).
_PROCEDENCIA = {"user.sketch": "DIBUJADA por el usuario"}


#: Capas con detalle completo en el prompt (las demás van en una línea: id, nombre, estado) y campos
#: que se nombran por capa (el resto se cuenta: «+N»).
_CAPAS_CON_DETALLE = 40


_CAMPOS_POR_CAPA = 25


def _accion(a: dict) -> str:
    quien = "el USUARIO" if a.get("author") == "user" else "TÚ (el agente)"
    args = a.get("args") or {}
    que = _OPERACION.get(str(a.get("op")), str(a.get("op")))
    if a.get("op") == "add_layer" and args.get("dibujo"):
        que = "dibujó"
    capa = f"«{a.get('layer_name') or a.get('layer_id') or 'el mapa'}»"
    detalle = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3] if not isinstance(v, (dict, list)))
    txt = f"  - {quien} {que} {capa}" + (f" ({detalle})" if detalle else "")
    if a.get("undone"):
        txt += (" — el USUARIO lo DESHIZO después (Ctrl+Z): ese cambio YA NO está en el mapa (consta en el "
                "registro: es seguro, no una suposición)")
        if args.get("antes"):
            txt += f"; esa capa volvió a su estilo anterior ({args['antes']})"
    return txt


def _mencion(m: dict, layers: list[dict]) -> str:
    """Una mención `@…` resuelta a la capa (o campo, o selección) exacta del mapa."""
    texto = f"«{m.get('texto') or '@?'}»"
    capa = next((lyr for lyr in layers if lyr.get("id") == m.get("layer_id")), None)
    if capa is None:
        return f"  - {texto} → (esa capa ya no está en el mapa)"
    ds = f" (dataset {capa['dataset_id']})" if capa.get("dataset_id") else ""
    nombre = f"[{capa.get('id')}] \"{capa.get('name', '')}\"{ds}"
    if m.get("tipo") == "seleccion":
        sel = capa.get("seleccion") or {}
        if not (sel.get("ids") or sel.get("where")):
            return f"  - {texto} → (ya no hay nada seleccionado en {nombre})"
        n = sel.get("count") if sel.get("count") is not None else len(sel.get("ids") or [])
        return f"  - {texto} → [seleccion]: los {n} elemento(s) seleccionados en {nombre}"
    if m.get("tipo") == "campo":
        return f"  - {texto} → el campo «{m.get('campo')}» de {nombre}"
    return f"  - {texto} → la capa {nombre}"


def _respuesta_mapa(resp: dict, layers: list[dict], pt: dict | None) -> str:
    """FH.9: el usuario respondió EN el mapa a lo que pediste (request_map_input)."""
    pedido = f"a tu pedido «{resp.get('pedido', '')}»"
    if resp.get("cancelado"):
        return (f"RESPUESTA DEL USUARIO EN EL MAPA {pedido}: CANCELÓ (no quiso señalarlo). No lo vuelvas a "
                "pedir: responde con lo que hay o di qué falta para poder hacerlo.")
    lid = resp.get("layer_id")
    capa = next((c for c in layers if c.get("id") == lid), {}) if lid else {}
    nombre = f"[{lid}] «{capa.get('name', lid)}»" if lid else ""
    modo = resp.get("modo")
    if modo == "pick_point":
        donde = (f"lon {pt['lon']:.6f}, lat {pt['lat']:.6f} (es el PUNTO MARCADO, referencia `punto`)"
                 if pt and pt.get("lon") is not None else "(no llegó el punto)")
        hecho = f"marcó un punto: {donde}"
    elif modo == "draw_area":
        hecho = f"dibujó un área: la capa {nombre}"
    elif modo == "pick_layer":
        hecho = f"eligió la capa {nombre}"
    else:
        sel = capa.get("seleccion") or {}
        hecho = f"seleccionó {sel.get('count', '?')} elemento(s) en {nombre} (referencia `seleccion`)"
    return (f"RESPUESTA DEL USUARIO EN EL MAPA {pedido}: {hecho}. Este mensaje es su consulta original; "
            "continúa con esa respuesta.")


def format_map_context(map_context: dict | None) -> str:  # noqa: C901, PLR0912, PLR0915
    """Formato del estado del MAPA que el usuario tiene en pantalla (Fase A).

    A diferencia de ``format_active_layer_context`` (que se reconstruye
    desde la sesión del backend y solo conoce lo que pasó por ``/query``),
    esto refleja lo que el frontend reporta como cargado AHORA: capas de
    Discovery, capa enfocada, feature seleccionada y la zona visible. Es lo
    que permite al LLM resolver "esta capa", "el predio seleccionado",
    "en esta zona".

    Returns:
        Bloque de texto para el prompt, o ``""`` si no hay map_context.
    """
    if not map_context:
        return ""

    lines: list[str] = []
    layers = map_context.get("layers") or []
    if layers:
        lines.append("CAPAS EN EL MAPA (el usuario las tiene cargadas AHORA; en orden de dibujo, "
                     "la primera queda ABAJO y la última ENCIMA):")
        # Auditoría pre-producción: antes `layers[:10]` sin aviso — con 12 capas, la undécima no
        # existía para el agente («colorea la capa X» fallaba). Todas, con detalle hasta un tope y en
        # una línea corta las demás.
        for i, lyr in enumerate(layers):
            mark = ">> ACTIVA" if lyr.get("is_active") else ("visible" if lyr.get("visible", True) else "oculta")
            if i >= _CAPAS_CON_DETALLE:
                lines.append(f"  - [{lyr.get('id')}] \"{lyr.get('name', '')}\" — {mark}")
                continue
            geom = lyr.get("geometry_type") or "?"
            fc = lyr.get("feature_count", 0)
            fields = lyr.get("fields") or []
            fsample = ", ".join(fields[:_CAMPOS_POR_CAPA])
            fmore = f" (+{len(fields) - _CAMPOS_POR_CAPA})" if len(fields) > _CAMPOS_POR_CAPA else ""
            ds = f" (dataset {lyr['dataset_id']})" if lyr.get("dataset_id") else ""
            kind = str(lyr.get("kind") or "")
            if kind and not kind.startswith("vector"):
                # S4.4: un raster no tiene features ni campos; lo que el agente
                # necesita para razonar sobre él es qué es y de dónde salió.
                fecha = f"; retrata {lyr['fecha']}" if lyr.get("fecha") else ""
                lines.append(f"  - [{lyr.get('id')}] \"{lyr.get('name', '')}\" — {mark}; RASTER ({kind}){fecha}"
                             + _detalle_raster(lyr) + _aspecto(lyr))
                continue
            origen = (lyr.get("origin") or {}).get("capability")
            procedencia = f"; {_PROCEDENCIA.get(origen, f'origen {origen}')}" if origen else ""
            # V5 EH.9: «Lotes … · spatial_operation» no dice QUÉ tiene; el agente la describió
            # con la pregunta equivocada. La pregunta que la produjo es un hecho de la capa.
            pedido = ((lyr.get("origin") or {}).get("arguments") or {}).get("query")
            if isinstance(pedido, str) and pedido.strip():
                procedencia += f" (resultado de «{pedido.strip()[:140]}»)"
            lines.append(
                f"  - [{lyr.get('id')}] \"{lyr.get('name', '')}\"{ds} — {mark}; "
                f"{geom}; {fc} features; campos: {fsample or '—'}{fmore}{procedencia}" + _aspecto(lyr)
            )

    for lyr in layers:
        # FH.2: lo seleccionado es una capa más, con su propio [id]: «estos»,
        # «los seleccionados», «¿cuánto suman?» se resuelven eligiéndola.
        sel = lyr.get("seleccion") or {}
        if not (sel.get("ids") or sel.get("where")):
            continue
        n = sel.get("count") if sel.get("count") is not None else len(sel.get("ids") or [])
        # Con la MISMA forma que cualquier capa (id, dataset, geometría, campos):
        # las herramientas piden un [id] o un dataset, y este es uno más.
        lines.append(
            f"  - [seleccion] \"Selección de {lyr.get('name', '')}\" (dataset seleccion) — los {n} "
            f"elemento(s) SELECCIONADOS por el usuario en [{lyr.get('id')}] (por {sel.get('origin', '?')}); "
            f"{lyr.get('geometry_type') or '?'}; mismos campos.")
        break

    from geo_copilot.core.alcance import alcance_del_mensaje

    lines.extend(alcance_del_mensaje(map_context, layers))

    menciones = [m for m in (map_context.get("menciones") or []) if isinstance(m, dict)]
    if menciones:
        # FH.4: el usuario ELIGIÓ estas referencias de una lista (`@`): no hay nada que
        # adivinar sobre a qué capa o campo se refiere; qué hacer con ellas lo decides tú.
        lines.append("MENCIONES DEL USUARIO EN ESTE MENSAJE (las eligió con @ de lo que hay en el mapa):")
        lines.extend(_mencion(m, layers) for m in menciones[:20])

    acciones = [a for a in (map_context.get("acciones") or []) if isinstance(a, dict)]
    if acciones:
        # FH.1: el mapa es compartido; lo que el usuario (o tú) hizo desde tu última
        # respuesta es un HECHO que cambia lo que hay en pantalla.
        # V5 FH.7: tras «el mayor de los 27 es 059» el usuario filtró (quedan 3, sin él) y el
        # seguimiento repitió 059. Hecho temporal: lo dicho antes describe el mapa SIN esto.
        lines.append("ACCIONES EN EL MAPA DESDE TU ÚLTIMA RESPUESTA (en orden; tus respuestas anteriores "
                     "describen el mapa de ANTES de estas acciones):")
        if len(acciones) > 15:
            lines.append(f"  (… {len(acciones) - 15} acciones anteriores no se listan; estas son las últimas 15)")
        lines.extend(_accion(a) for a in acciones[-15:])

    sel = map_context.get("selected_feature")
    if sel and sel.get("properties"):
        props = sel["properties"]
        sample = ", ".join(f"{k}={v}" for k, v in list(props.items())[:8])
        lines.append(
            f"FEATURE SELECCIONADA (capa {sel.get('layer_id')}): {sample}"
        )

    pt = map_context.get("clicked_point")
    if pt and pt.get("lon") is not None and pt.get("lat") is not None:
        lines.append(
            f"PUNTO MARCADO por el usuario en el mapa (su «aquí»): "
            f"lon {pt['lon']:.6f}, lat {pt['lat']:.6f} (EPSG:4326)"
        )


    resp = map_context.get("respuesta_mapa")
    if isinstance(resp, dict):
        lines.append(_respuesta_mapa(resp, layers, pt))

    # FH.10: vistas guardadas, comparación con cortina y control de tiempo (lo que el usuario ve)
    vistas = [v for v in (map_context.get("vistas") or []) if isinstance(v, dict)]
    if vistas:
        lines.append("VISTAS GUARDADAS (marcadores del usuario; para ir a una: zoom_to con su bbox): " + "; ".join(
            f"«{v.get('nombre')}» bbox [{', '.join(f'{x:.5f}' for x in v.get('bbox') or [])}]" for v in vistas[:20])
            + (f"; (+{len(vistas) - 20} vistas más)" if len(vistas) > 20 else ""))
    comp = map_context.get("comparacion")
    if isinstance(comp, dict):
        nombre = {c.get("id"): c.get("name") for c in layers}
        lines.append(f"COMPARACIÓN CON CORTINA abierta: a la izquierda [{comp.get('left')}] «{nombre.get(comp.get('left'), '')}», "
                     f"a la derecha [{comp.get('right')}] «{nombre.get(comp.get('right'), '')}» (end_compare la cierra)")
    serie = map_context.get("serie_tiempo")
    if isinstance(serie, dict) and serie.get("fechas"):
        mas = f" (+{len(serie['fechas']) - 30} fechas más)" if len(serie["fechas"]) > 30 else ""
        lines.append(f"CONTROL DE TIEMPO: serie con fechas {', '.join(serie['fechas'][:30])}{mas}; se muestra "
                     f"{serie.get('actual') or '—'} (set_time lo cambia o la anima)")

    vp = map_context.get("viewport")
    if vp and vp.get("bbox"):
        b = vp["bbox"]
        lines.append(
            f"ZONA VISIBLE (bbox {vp.get('crs', 'EPSG:4326')}): "
            f"[{', '.join(f'{x:.4f}' for x in b)}]"
        )

    if any(str(lyr.get("kind") or "").startswith(("raster", "arcgis", "wms")) for lyr in layers[:10]):
        # Un HECHO que el modelo no puede deducir: ve la descripción de la capa,
        # no sus píxeles (V5 F4: «el NDVI aquí» respondido con la media de la zona).
        lines.append(
            "  - Un RASTER del mapa es una imagen que ve el usuario: tú NO ves sus píxeles. "
            "Su valor en un punto o en una zona solo se conoce MIDIÉNDOLO con una "
            "herramienta (la que lo produjo, según su origen)."
        )
    if lines:
        lines.append(
            "  - IMPORTANTE: cuando el usuario diga \"esta capa\", \"esto\", "
            "\"el predio\", \"en esta zona\" / \"en la zona visible\" (el área que se ve), \"aquí\" (el punto marcado), "
            "\"lo que dibujé\" (una capa DIBUJADA por el usuario), resuélvelo contra lo de arriba; "
            "no vuelvas a la BD ni busques fuera si ya está cargado."
        )
    return "\n".join(lines)
