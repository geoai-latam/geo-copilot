"""Capacidades `core.*`: las herramientas propias del núcleo (S1.3).

Cada una era una rama del `if name == ...` de `react_tools.dispatch_tool`, un
schema escrito a mano en `tool_schemas.py`, una línea del prompt ReAct y una
entrada del mapa de pasos del chip. Ahora es UNA entrada: `Capability`.

Los ejecutores son los de antes, sin cambios de comportamiento: reusan los nodos
del grafo (gis_agent, python_agent, symbology, data_agent) vía
`react_tools._run_node`, heredando su HITL, su auto-corrección y el juez de
resultados vacíos.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.platform.capabilities import Capability, ToolOutcome, registry

# FRT-04: parámetro OPCIONAL compartido — la capa OBJETIVO cuando el usuario
# NOMBRA una capa específica y hay varias cargadas. El bucle ReAct lo valida
# contra map_layers antes de despachar al nodo.
_TARGET_LAYER_ID = {
    "type": ["string", "null"],
    "description": "[id] de la capa objetivo de 'CAPAS EN EL MAPA' si el usuario "
    "NOMBRÓ una capa específica y hay 2+ capas (`seleccion` = solo lo seleccionado); "
    "null u omitir para la capa activa.",
}


def _params(properties: dict, required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Ejecutores (importes de nodos locales: evitan el ciclo nodes ↔ graph)
# ---------------------------------------------------------------------------


async def _query_database(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.orchestrator.nodes import gis_agent as gis_node
    from geo_copilot.orchestrator.react_tools import _FRESH, _run_node

    # GENERA datos frescos de la BD interna → pizarra limpia + aislar externos.
    outcome = await _run_node(graph, gis_node.run, working, {
        "query": str(args.get("request") or ""),
        "intent": "query_data",
        "external_geojson": None, "has_external_data": False,
        "active_data_source": "internal",
        "_desde_bucle": True,  # si la BD interna no lo tiene, se le dice al bucle (no otro bucle dentro)
    }, clear=_FRESH + ("external_geojson",))
    # La fuente activa ES interna tras traer datos de la BD — el nodo gis no
    # la re-emite y sin esto el estado conservaba "none": la siguiente
    # herramienta (analyze/spatial) veía "sin fuente activa" y rechazaba
    # honesto con 0 features (bug destapado por el bench hybrid).
    gj = outcome.delta.get("geojson")
    con_geometria = isinstance(gj, dict) and bool(gj.get("features"))
    if outcome.success and not outcome.delta.get("error"):
        outcome.delta.setdefault("active_data_source", "internal")
    if outcome.success and not outcome.delta.get("error") and con_geometria:
        # Los datos nuevos se llaman por lo que el agente pidió, no por la capa del
        # turno anterior (V5 F4: 30 lotes de la manzana 004503004 salieron
        # etiquetados «Lotes Manzana 004503009»).
        # SOLO si traen geometría (V3 F5): un conteo dejaba la capa activa —las 41 sedes del
        # turno anterior— renombrada «cuenta los lotes…»; el LLM la cruzó con las sedes y
        # respondió 129 lotes donde había 3.
        outcome.delta.setdefault("active_source_name", str(args.get("request") or "")[:60] or None)
    return outcome


async def _spatial_operation(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.orchestrator.nodes import python_agent as py_node
    from geo_copilot.orchestrator.react_tools import (
        _TRANSFORM_CLEAR,
        _effective_source,
        _resolve_target,
        _run_node,
    )

    # TRANSFORMA la capa cargada: conserva el geojson de entrada (no se limpia),
    # pero declara la fuente activa explícitamente y limpia error/sql/raw_data
    # y el canal analítico de pasos previos.
    return await _run_node(graph, py_node.run, working, {
        "query": str(args.get("request") or ""),
        "intent": "spatial_operation",
        # el nombre de la capa resultante sale de aquí (antes: el texto de la pregunta)
        "operacion_espacial": str(args.get("operation") or "") or None,
        "target_layer_id": _resolve_target(args, working),  # FRT-04
        "active_data_source": _effective_source(working),
    }, clear=_TRANSFORM_CLEAR, deriva_capa=True)


async def _analyze_layer(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.orchestrator.nodes import python_agent as py_node
    from geo_copilot.orchestrator.react_tools import (
        _TRANSFORM_CLEAR,
        _effective_source,
        _resolve_target,
        _run_node,
    )

    # R1.4: ANALIZA la capa cargada (clustering/correlación/outliers/…).
    # Mismo ejecutor que spatial_operation (python_agent → sandbox), pero el
    # resultado esperado es el canal analítico: ``data`` + ``visualization``.
    return await _run_node(graph, py_node.run, working, {
        "query": str(args.get("request") or ""),
        "intent": "analyze",
        "target_layer_id": _resolve_target(args, working),  # FRT-04
        "active_data_source": _effective_source(working),
    }, clear=_TRANSFORM_CLEAR, deriva_capa=True)


async def _apply_symbology(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.orchestrator.nodes import symbology as sym_node
    from geo_copilot.orchestrator.react_tools import _resolve_target, _run_node

    # FH.12 (bench de deixis): un estilo es de una CAPA; `seleccion` es un subconjunto de la suya
    # (el LLM la ponía como objetivo de «clasifícalos en 7 clases» con el chip de selección puesto).
    if str(args.get("target_layer_id") or "").strip("[]") == "seleccion":
        from geo_copilot.platform.seleccion import seleccion_actual

        sel = seleccion_actual(working)
        args = {**args, "target_layer_id": sel.layer_id if sel is not None else None}
    # ESTILIZA la capa: no genera ni invalida datos — conserva raw_data y el
    # canal analítico (mismo contrato que step_router en F1: la simbología
    # no borra el análisis que viaja al responder). Solo limpia sql/error.
    return await _run_node(graph, sym_node.run, working, {
        "query": str(args.get("request") or ""),
        "intent": "apply_symbology",
        "target_layer_id": _resolve_target(args, working),  # FRT-04
    }, clear=("sql", "error"))


async def _search_external(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.orchestrator.nodes import data_agent as data_node
    from geo_copilot.orchestrator.react_tools import _FRESH, _run_node

    return await _run_node(graph, data_node.run, working, {
        "query": str(args.get("query") or ""),
        "intent": "search_external",
        "_desde_bucle": True,  # el bucle YA eligió buscar en portales: no se le devuelve el turno
    }, clear=_FRESH)


async def _select_service(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.orchestrator.nodes import data_agent as data_node
    from geo_copilot.orchestrator.react_tools import _FRESH, _run_node

    # data_agent.run NO resuelve el número → lo hacemos aquí desde la lista
    # de la búsqueda previa, y cargamos por URL (load_external, que sí maneja).
    services = working.get("found_services") or []
    try:
        num = int(args.get("number"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        num = 0
    if not services:
        return ToolOutcome(
            observation="no hay lista de servicios previa; usa search_external primero",
            success=False)
    if num < 1 or num > len(services):
        return ToolOutcome(
            observation=f"número {num} fuera de rango (1..{len(services)})", success=False)
    svc = services[num - 1] or {}
    url = svc.get("url")
    if not url:
        return ToolOutcome(observation=f"el servicio {num} no tiene URL cargable", success=False)
    # la capa: la que eligió el LLM (servicio con varias capas) o la que traía el resultado
    capa = args.get("layer")
    vistas = str((working.get("service_layers_of") or {}).get("url") or "").rstrip("/").lower()
    if capa is not None and vistas != str(url).rstrip("/").lower():
        # V4 (LLM real): pasaba `layer` sin haber visto las capas del servicio (adivinaba la 0 o la 1):
        # se ignora y el servicio responde con sus capas para que elija con los hechos
        capa = None
    if capa is None:
        capa = svc.get("layer_id")
    if capa is not None and not url.rstrip("/").rsplit("/", 1)[-1].isdigit():
        url = f"{url.rstrip('/')}/{int(capa)}"
    return await _run_node(graph, data_node.run, working, {
        "intent": "load_external", "external_url": url, "_nombre_a_cargar": svc.get("name"),
    }, clear=_FRESH)


async def _load_external(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.orchestrator.nodes import data_agent as data_node
    from geo_copilot.orchestrator.react_tools import _FRESH, _run_node

    return await _run_node(graph, data_node.run, working, {
        "intent": "load_external",
        "external_url": str(args.get("url") or ""),
        "_desde_bucle": True,
    }, clear=_FRESH)


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

def _muchas_tools_mcp(graph: Any) -> bool:
    from geo_copilot.core.config import get_settings
    from geo_copilot.platform.mcp.busqueda import hay_demasiadas

    return hay_demasiadas(graph, get_settings().mcp_tools_umbral)


async def _find_tools(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.mcp.busqueda import ejecutar_find_tools

    return await ejecutar_find_tools(graph, working, args)


CORE: tuple[Capability, ...] = (
    Capability(
        id="core.find_tools", tool_name="find_tools",
        description=(
            "Activa para este turno herramientas de los SERVICIOS CONECTADOS: por su nombre exacto "
            "(`names`, del CATÁLOGO del prompt) o buscando por lo que quieres hacer (`query`). Úsala "
            "antes de usar un servicio conectado: sus herramientas no se pueden llamar hasta activarlas."
        ),
        parameters=_params({
            "names": {"type": "array", "items": {"type": "string"},
                      "description": "Nombres exactos a activar (p. ej. «srv__ruta»), del catálogo."},
            "query": {"type": "string", "description": "Qué quieres hacer, en palabras del dominio "
                      "(p. ej. «índice de vegetación por polígono», «ruta más corta entre dos puntos»)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10,
                      "description": "Cuántas candidatas activar por `query` (por defecto 6)."},
        }, []),
        executor=_find_tools,
        blurb="activar herramientas de los servicios conectados (por nombre del catálogo o buscando).",
        available=_muchas_tools_mcp,
        step=("agent_loop", "Buscando herramientas de los servicios conectados"),
    ),
    Capability(
        id="core.query_database", tool_name="query_database",
        description=(
            "Consulta la base de datos PostGIS interna (entidades como lotes, predios, "
            "construcciones, manzanas). Úsala para obtener/contar/filtrar/analizar datos "
            "que YA están en la BD. NO para datos externos. También ve los datasets del workspace "
            "(ds_…): un cruce entre una tabla de la BD y un dataset («lotes a menos de 200 m de las "
            "sedes de ds_…») se pide ENTERO en una sola petición y la BD lo resuelve; no traigas una "
            "tabla grande para cruzarla después."
        ),
        parameters=_params({
            "request": {
                "type": "string",
                "description": "Qué obtener, en lenguaje natural y autónomo "
                "(ej. 'los predios del barrio centro con área > 500 m2').",
            },
        }, ["request"]),
        executor=_query_database,
        blurb="trae datos de la BD interna (lotes, predios, construcciones…).",
        risk="read", step=("gis_agent", "Consultando la base de datos (SQL)"),
    ),
    Capability(
        id="core.spatial_operation", tool_name="spatial_operation",
        description=(
            "Operación geométrica (buffer, centroide, área, unión, intersección, "
            "disolución, clip, distancia, heatmap, cluster) SOBRE LA CAPA YA CARGADA. "
            "Se resuelve ESCRIBIENDO un script de Python que corre en el sandbox (el "
            "usuario puede tener que aprobarlo) y devuelve una CAPA NUEVA con el resultado; "
            "sirve también para campos calculados a medida. No re-consulta datos."
        ),
        parameters=_params({
            "operation": {
                "type": "string",
                "description": "La operación: buffer | centroid | area | union | "
                "intersect | dissolve | clip | distance | heatmap | cluster | other.",
            },
            "request": {
                "type": "string",
                "description": "Instrucción completa incl. parámetros "
                "(ej. 'buffer de 500 metros', 'centroide de cada feature').",
            },
            "target_layer_id": _TARGET_LAYER_ID,
        }, ["operation", "request"]),
        executor=_spatial_operation,
        blurb="buffer/centroide/área/etc. SOBRE la capa ya cargada.",
        risk="compute", cost="medium",
        step=("python_agent", "Operación espacial sobre la geometría"),
    ),
    Capability(
        id="core.analyze_layer", tool_name="analyze_layer",
        description=(
            "Análisis estadístico/ML SOBRE LA CAPA YA CARGADA: clustering (DBSCAN, "
            "K-Means), correlación, outliers, distribución/histograma, estadísticas "
            "descriptivas, regresión, autocorrelación espacial. Produce tabla, "
            "estadísticas o gráfico (no un mapa nuevo): sus columnas NO se añaden a "
            "la capa, así que la simbología no puede usarlas. No re-consulta la BD. "
            "Se resuelve ESCRIBIENDO un script de Python en el sandbox (el usuario puede "
            "tener que aprobarlo)."
        ),
        parameters=_params({
            "request": {
                "type": "string",
                "description": "El análisis pedido, completo y autónomo (ej. "
                "'agrupa los puntos con DBSCAN y cuenta por cluster', "
                "'correlación entre valor y pisos', 'outliers en el campo área').",
            },
            "target_layer_id": _TARGET_LAYER_ID,
        }, ["request"]),
        executor=_analyze_layer,
        blurb=(
            "análisis estadístico/ML sobre la capa ya cargada (clustering,\n"
            "  correlación, outliers, distribución…) → produce tabla/estadísticas/gráfico."
        ),
        risk="compute", cost="medium",
        step=("python_agent", "Análisis estadístico de la capa"),
    ),
    Capability(
        id="core.apply_symbology", tool_name="apply_symbology",
        description=(
            "Cambia el ESTILO de los elementos de la capa cargada (color, tamaño, "
            "clasificación temática/coropleta, heatmap, cluster). Clasifica por campos que "
            "la capa YA tiene. Las etiquetas, la transparencia, el orden y la visibilidad de "
            "una capa NO son estilo: son map_command. No toca la BD ni descarga nada."
        ),
        parameters=_params({
            "request": {
                "type": "string",
                "description": "Estilo deseado (ej. 'color rojo', 'coropleto por "
                "uso_suelo en 5 clases', 'etiqueta con el campo nombre').",
            },
            "target_layer_id": _TARGET_LAYER_ID,
        }, ["request"]),
        executor=_apply_symbology,
        blurb="cambia el estilo visual de la capa cargada.",
        risk="read", step=("symbology_agent", "Simbología de la capa"),
    ),
    Capability(
        id="core.search_external", tool_name="search_external",
        description=(
            "Busca datasets/servicios geoespaciales en fuentes externas (ArcGIS Hub, "
            "portales de datos abiertos) por tema/entidad. Devuelve una LISTA para elegir."
        ),
        parameters=_params({
            "query": {
                "type": "string",
                "description": "Qué buscar (ej. 'estaciones de bomberos', "
                "'ortofotos de Zipaquirá').",
            },
        }, ["query"]),
        executor=_search_external,
        blurb="busca datasets en fuentes externas (devuelve una lista).",
        risk="external_egress", step=("data_agent", "Buscando datos en fuentes externas"),
    ),
    Capability(
        id="core.select_service", tool_name="select_service",
        description=(
            "Carga un servicio de la LISTA devuelta por una búsqueda externa previa, "
            "por su número (1..N)."
        ),
        parameters=_params({
            "number": {
                "type": "integer",
                "description": "Número del servicio en la lista (1-based).",
                "minimum": 1,
            },
            "layer": {
                "type": "integer",
                "description": ("Si el servicio tiene varias capas, el id de la que se carga (cuando se "
                                "elige un servicio con varias, se responde con sus capas para elegir)."),
                "minimum": 0,
            },
        }, ["number"]),
        executor=_select_service,
        blurb="carga un servicio de esa lista por su número.",
        risk="external_egress", step=("data_agent", "Cargando el servicio elegido"),
    ),
    Capability(
        id="core.load_external", tool_name="load_external",
        description=(
            "Carga datos desde una URL geoespacial directa (GeoJSON/Shapefile/GPKG/KML "
            "o servicio ArcGIS REST)."
        ),
        parameters=_params({
            "url": {"type": "string", "description": "URL http(s) completa del recurso."},
        }, ["url"]),
        executor=_load_external,
        blurb="carga datos desde una URL.",
        risk="external_egress", step=("data_agent", "Cargando datos externos"),
    ),
)


def ensure_core() -> None:
    """Registra las capacidades `core.*` (idempotente)."""
    # S2.5: las espaciales deterministas sobre el workspace (ws_*).
    from geo_copilot.orchestrator.capabilities_espaciales import ESPACIALES

    # FH.1: operar el mapa compartido (zoom, visibilidad, opacidad, orden…).
    from geo_copilot.orchestrator.capabilities_mapa import MAPA

    reg = registry()
    for cap in (*CORE, *ESPACIALES, *MAPA):
        if reg.get(cap.tool_name) is None:
            reg.register(cap)


ensure_core()
