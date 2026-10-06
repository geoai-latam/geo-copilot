"""El BLOQUE DEL WORKSPACE que lee el LLM: los datasets de la sesión, sus campos y los
subconjuntos de la base que ya trajo.

Salió de `capabilities_espaciales.py` (F4 del plan de calidad: capabilities_espaciales.py tenía 600 líneas), tal cual.
"""

from __future__ import annotations

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.orchestrator.capabilities_espaciales")


def _ce():
    """`capabilities_espaciales` importa este módulo; lo que las pruebas sustituyen ahí (`store_actual`,
    `_subconjunto_de_la_bd`, …) se resuelve al usarlo."""
    from geo_copilot.orchestrator import capabilities_espaciales

    return capabilities_espaciales

async def bloque_workspace(session_id: str | None, map_context: dict | None = None) -> str:
    """Datasets de la sesión para el prompt del bucle (ids que usan las ws_*).

    FH.2: si hay algo seleccionado en el mapa, `seleccion` va en la lista como un
    dataset más (se materializa al usarlo): V5 — con la selección solo en «CAPAS
    EN EL MAPA», el agente midió el dataset entero que sí veía aquí.
    """
    store = _ce().store_actual()
    if store is None or not session_id:
        return ""
    try:
        refs = await store.list_datasets(session_id)
    except Exception:  # el workspace es aditivo: sin él, el bucle sigue sin las ws_*
        logger.warning("[agent_loop] no se pudo listar el workspace", exc_info=True)
        return ""
    if not refs:
        return ""
    lineas = ["DATASETS DEL WORKSPACE (resultados de esta sesión; úsalos por id en las herramientas ws_*):"]
    # V5 (FH.3): un dibujo que el usuario borró del mapa seguía aquí como si estuviera.
    en_mapa = ({c.get("dataset_id") for c in (map_context or {}).get("layers") or []}
               if map_context is not None else None)
    # V5 (otra temática): el punto marcado / la zona visible de turnos ANTERIORES seguían aquí
    # («Punto marcado (-74.03767, …)») y el agente usó ese punto viejo como su «aquí». Los de
    # ahora son `punto` y `viewport`; los materializados antes no son datos del usuario.
    refs = [r for r in refs
            if getattr(getattr(r, "provenance", None), "capability", None) not in ("core.punto", "core.viewport")]
    if not refs:
        return ""
    for r in refs[-20:]:
        campos = ", ".join(f.name for f in r.fields[:12])
        dibujo = " (DIBUJADO por el usuario)" if r.provider == "sketch" else ""
        from geo_copilot.platform.seleccion import filtro_de_dataset

        if filtro := filtro_de_dataset(r.id, {"map_context": map_context or {}}):
            from geo_copilot.core.formatters import describir_filtro

            dibujo += (f" (en el mapa FILTRADA: solo {describir_filtro(filtro)}; las ws_* reciben ese "
                       "subconjunto; ws_add_measure mide la capa entera)")
        args_prov = getattr(getattr(r, "provenance", None), "arguments", None) or {}
        total_srv = args_prov.get("total_en_servicio")
        if isinstance(total_srv, int) and total_srv > (r.feature_count or 0):
            # Pendiente del acta FH: la carga del catálogo tiene tope; si el servicio tiene más, se dice.
            dibujo += (f" (INCOMPLETA: {r.feature_count} de {total_srv} elementos del servicio; conteos y totales "
                       "sobre ella no son del servicio completo)")
        if subconjunto := _ce()._subconjunto_de_la_bd(getattr(getattr(r, "provenance", None), "sql", None)):
            # V5 acumulada: con «Construcciones Manzana X» (9) cargada, «¿cuántas construcciones del
            # catastro caen en el buffer?» se respondió «9 construcciones del catastro»
            dibujo += (f" (SUBCONJUNTO de la BD: solo las filas de {subconjunto} que cumplen el filtro de su "
                       "consulta; un conteo sobre esta capa NO es el de la tabla completa)")
        if en_mapa is not None and r.id not in en_mapa:
            dibujo += " (NO está en el mapa ahora: el usuario lo quitó o no se añadió)"
        lineas.append(
            f"  - {r.id} «{r.name}»{dibujo} — {r.geometry_type or 'tabla'}; {r.feature_count} elementos; "
            f"campos: {campos or '—'}"
        )
    from geo_copilot.platform.seleccion import seleccion_actual

    sel = seleccion_actual({"map_context": map_context or {}})
    if sel is not None:
        n = sel.count if sel.count is not None else len(sel.ids or ())
        de = f"{sel.dataset_id} " if sel.dataset_id else ""
        lineas.append(
            f"  - seleccion «Selección de {sel.layer_name}» — los {n} elemento(s) SELECCIONADOS por el "
            f"usuario en {de}[{sel.layer_id}] (por {sel.origin}); mismos campos"
        )
    # V5 (otra temática): como la selección (FH.2), el punto marcado va donde el agente busca
    # los datasets; sin esta línea hacía un buffer de la capa entera antes de dar con `punto`.
    pt = (map_context or {}).get("clicked_point") or {}
    if pt.get("lon") is not None and pt.get("lat") is not None:
        lineas.append(
            f"  - punto «Punto marcado ({pt['lon']:.5f}, {pt['lat']:.5f})» — el «aquí» del usuario AHORA; "
            "Point; 1 elemento (para «a N m de aquí»: `ws_spatial_join` dwithin N con dataset_b `punto`)"
        )
    lineas.append(
        "  - Para medir, buffer, intersección/diferencia, unión espacial, conteo por zonas "
        "o LISA/Gi* sobre estos datasets, PREFIERE las herramientas ws_* (exactas y "
        "reproducibles) antes que `spatial_operation`/`analyze_layer`. Las ws_* operan "
        "SOLO sobre estos datasets: para cruzarlos con tablas de la BD (catastro…) usa "
        "`query_database`, que ve la BD y estos datasets a la vez."
    )
    return "\n".join(lineas)
