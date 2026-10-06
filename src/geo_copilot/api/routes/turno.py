"""La ENTRADA de un turno de consulta (F4 del plan de calidad: `ejecutar_consulta` tenía 615 líneas):
la sesión, el mapa, la consulta a procesar y el grafo. La salida está en `turno_salida.py`.

`query.ejecutar_consulta` orquesta; cada paso tiene UNA responsabilidad. Refactor sin cambio de
comportamiento: el código de cada paso es el que estaba en `ejecutar_consulta`, tal cual; solo se
leen y se guardan sus variables en `Turno`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status

from geo_copilot.api.models import QueryRequest, QueryResponse, QueryStatus
from geo_copilot.core.config import get_settings
from geo_copilot.core.llm_client import plazo_turno
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.conversation import DataSource

#: el mismo logger que query.py: los registros del turno siguen saliendo con su nombre
logger = get_logger("geo_copilot.api.routes.query")


@dataclass
class Turno:
    """Lo que el turno lleva de un paso al siguiente."""

    session_id: str
    conversation_history: Any = None
    previous_sql: Any = None
    previous_results: Any = None
    previous_geojson: Any = None
    found_services: Any = None
    external_geojson: Any = None
    external_source_name: Any = None
    has_external_data: bool = False
    active_data_source: Any = None
    active_source_name: Any = None
    pending_operations: Any = None
    map_context_dict: dict | None = None
    active_layer_id: str | None = None
    map_layers: dict[str, dict] | None = None
    hidratadas: dict[str, dict] = field(default_factory=dict)
    #: el registro del turno (E7.1) quedó guardado: `ejecutar_consulta` lo cierra al final
    registrado: bool = False


def _rutas():
    """`query` importa este módulo; sus funciones se resuelven al usarlas (sin ciclo de imports)."""
    from geo_copilot.api.routes import query

    return query


async def leer_sesion(context: Any, t: Turno) -> None:
    """Lo que la sesión trae del turno anterior (historial, capa, datos externos, plan pausado)."""
    session_id = t.session_id
    validate_geojson = _rutas().validate_geojson
    _geojson_del_workspace = _rutas()._geojson_del_workspace
    # Obtener historial y datos previos para contexto
    conversation_history = context.get_messages_for_llm(max_messages=10)
    previous_sql = context.state.last_sql
    previous_results = context.state.last_results.get("results") if context.state.last_results else None
    # GeoJSON de la capa mostrada en el turno anterior — da contexto
    # geométrico real a los follow-up (features, campos, tipo de geometría).
    previous_geojson = context.state.last_geojson
    # T2.0a: si la capa anterior quedó en el workspace, la sesión solo guarda
    # su id y la geometría se lee de ahí (del workspace de ESTA sesión).
    if previous_geojson is None and context.state.last_dataset_id:
        previous_geojson = await _geojson_del_workspace(
            session_id, context.state.last_dataset_id,
        )

    # Obtener servicios encontrados previamente para selección por número
    found_services = context.get_variable("found_services")

    # Obtener datos externos cargados previamente (para operaciones espaciales)
    external_geojson = context.get_variable("external_geojson")
    external_source_name = context.get_variable("external_source_name")
    has_external_data = external_geojson is not None

    # S4: validar el GeoJSON externo (estructura + tope de features) ANTES
    # de inyectarlo al grafo. Antes la única validación era código muerto
    # (``hasattr`` sobre un campo inexistente de QueryRequest); el geojson
    # real entra por aquí (variables de sesión) y no se validaba, saltando
    # la protección DoS de ``max_geojson_features``.
    if has_external_data:
        is_valid, error_msg = validate_geojson(external_geojson)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GeoJSON externo inválido: {error_msg}",
            )

    # Obtener fuente de datos activa
    active_data_source, active_source_name = context.get_active_data_source()

    # Obtener operaciones pendientes de un plan pausado
    pending_operations = context.get_variable("pending_operations")
    t.conversation_history = conversation_history
    t.previous_sql = previous_sql
    t.previous_results = previous_results
    t.previous_geojson = previous_geojson
    t.found_services = found_services
    t.external_geojson = external_geojson
    t.external_source_name = external_source_name
    t.has_external_data = has_external_data
    t.active_data_source = active_data_source
    t.active_source_name = active_source_name
    t.pending_operations = pending_operations


async def _hidratar_y_validar(map_context_dict: dict, session_id: str, hidratadas: dict[str, dict]) -> None:
    """Las capas del workspace llegan por id (T2.0a): su geometría, su filtro (FH.5) y su validación (S4)."""
    validate_geojson = _rutas().validate_geojson
    _geojson_del_workspace = _rutas()._geojson_del_workspace
    _muestra_para_estilo = _rutas()._muestra_para_estilo
    # T2.0a: las capas respaldadas por el workspace llegan SOLO con su
    # dataset_id; la geometría se lee del workspace de la sesión. Un id
    # de otra sesión no existe aquí: la capa queda con metadatos.
    for layer in map_context_dict.get("layers", []):
        if layer.get("dataset_id") and not layer.get("data"):
            layer["data"] = await _geojson_del_workspace(session_id, layer["dataset_id"])
            if layer["data"] is None:
                # H23: demasiado grande para hidratarla — el estilo se
                # diseña con una muestra (nunca se analiza con ella).
                muestra = await _muestra_para_estilo(session_id, layer["dataset_id"])
                if muestra is not None:
                    layer["muestra"], layer["total"] = muestra
        if layer.get("dataset_id") and layer.get("data"):
            hidratadas[layer["dataset_id"]] = layer["data"]
    # FH.5: una capa filtrada ES su subconjunto también para las herramientas
    # que trabajan en memoria (las del workspace lo materializan al resolverla).
    from geo_copilot.platform.seleccion import filtrar_capa

    for layer in map_context_dict.get("layers", []):
        if layer.get("filtro") and isinstance(layer.get("data"), dict):
            layer["completa"] = layer["data"]  # para contar/cambiar el filtro en este turno
            layer["data"] = filtrar_capa(layer["data"], layer["filtro"])
            layer["filtro_count"] = len(layer["data"]["features"])
    for layer in map_context_dict.get("layers", []):
        if layer.get("data"):
            ok, err = validate_geojson(layer["data"])
            if not ok:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"GeoJSON de capa '{layer.get('id')}' inválido: {err}",
                )


def _capas_por_id(map_context_dict: dict) -> dict[str, dict] | None:
    """FRT-04: capas por id (data + nombre) para operar sobre una capa NOMBRADA por el usuario (no sólo
    la activa). Es del TURNO — NO se persiste en la sesión (evita bloat: el front re-manda cada turno)."""
    map_layers = {
        lyr["id"]: {
            "data": lyr.get("data"), "name": lyr.get("name", ""),
            # H23: solo para estilo; `data` sigue vacío (nadie analiza una muestra)
            **({"muestra": lyr["muestra"], "total": lyr.get("total")} if lyr.get("muestra") else {}),
            **({"completa": lyr.pop("completa")} if lyr.get("completa") else {}),
        }
        for lyr in map_context_dict.get("layers", [])
        if lyr.get("id") and (lyr.get("data") or lyr.get("muestra"))
    } or None
    # FH.2: lo seleccionado es una capa más para las herramientas (`seleccion`).
    from geo_copilot.platform.seleccion import SELECCION, capa_virtual

    if map_layers and (virtual := capa_virtual(map_layers, map_context_dict)):
        map_layers[SELECCION] = virtual
    return map_layers


def _tomar_capa_activa(map_context_dict: dict, t: Turno) -> None:
    """A4a: si el mapa reporta una capa ACTIVA con geojson, esa es la capa autoritativa para operar
    (buffer/área/centroide) — refleja lo que el usuario ve AHORA, más confiable que el estado
    reconstruido de la sesión. Se inyecta como datos externos para que el python_agent/gis_agent
    operen sobre ella sin tocar cada nodo."""
    active_layer = next(
        (lyr for lyr in map_context_dict.get("layers", []) if lyr.get("is_active") and lyr.get("data")),
        None,
    )
    if active_layer is not None:
        t.active_layer_id = active_layer.get("id")
        t.external_geojson = active_layer["data"]
        t.external_source_name = active_layer.get("name") or t.external_source_name
        t.has_external_data = True
        t.active_data_source = DataSource.EXTERNAL
        t.active_source_name = active_layer.get("name") or t.active_source_name


async def leer_mapa(query_request: QueryRequest, context: Any, t: Turno) -> None:
    """Fase A: el contexto del mapa que el frontend adjunta (capas activas, feature seleccionada,
    viewport): hidratado del workspace, filtrado y validado (S4), por id (FRT-04) y la capa activa (A4a).
    Sin contexto del mapa, el turno sigue sin él."""
    if query_request.map_context is None:
        return
    map_context_dict = query_request.map_context.model_dump(mode="json")
    t.map_context_dict = map_context_dict
    await _hidratar_y_validar(map_context_dict, t.session_id, t.hidratadas)
    t.map_layers = _capas_por_id(map_context_dict)
    # Persistir en la sesión SIN los geojson (bloat): el front los reenvía
    # cada turno; la sesión sólo necesita los metadatos (nombre/campos).
    context.set_variable("map_context", {
        **map_context_dict,
        "layers": [
            {k: v for k, v in lyr.items() if k not in ("data", "muestra")}
            for lyr in map_context_dict.get("layers", [])
        ],
    })
    _tomar_capa_activa(map_context_dict, t)


def consulta_a_procesar(query_request: QueryRequest, context: Any, t: Turno) -> str:
    """La consulta del turno; al elegir un servicio con un plan pausado, compuesta con lo pendiente."""
    pending_operations, found_services = t.pending_operations, t.found_services
    # =====================================================================
    # RETOMAR PLAN PAUSADO: Si hay operaciones pendientes y el usuario
    # selecciona un servicio, construir query compuesto automáticamente
    # =====================================================================
    query_to_process = query_request.query
    if pending_operations and found_services:
        # Detectar si es una selección de servicio (número simple o "carga el X")
        query_lower = query_request.query.strip().lower()
        is_service_selection = (
            query_lower.isdigit() or
            query_lower.startswith("carga el ") or
            query_lower.startswith("el ") or
            query_lower in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
        )

        if is_service_selection:
            # Construir query compuesto: selección + operaciones pendientes
            pending_queries = [op.get("query", "") for op in pending_operations if op.get("query")]
            if pending_queries:
                # Asegurar que el query de selección tenga formato correcto
                if query_lower.isdigit():
                    selection_query = f"carga el {query_lower}"
                else:
                    selection_query = query_request.query

                # Concatenar: "carga el 2, buffer 500m, color rojo"
                query_to_process = f"{selection_query}, {', '.join(pending_queries)}"
                logger.info(f"[Query] Resuming paused plan: '{query_request.query}' → '{query_to_process}'")

                # Print visible en consola

                # Limpiar operaciones pendientes ya que se van a ejecutar
                context.set_variable("pending_operations", None)
    return query_to_process


async def _registrar_turno(turno_id: str, session_id: str, query_request: QueryRequest, ttl_s: int) -> None:
    """F7 (E7.1): el turno queda registrado fuera del proceso mientras corre; si el proceso
    muere esperando una aprobación, esa aprobación puede reanudarlo (sin token: el de
    un usuario no se guarda en Redis). F7 (auditoría): por su id, no por sesión — un
    segundo turno de la sesión ya no lo pisa ni lo borra al terminar."""
    from geo_copilot.platform.estado.aprobaciones import almacen
    from geo_copilot.platform.identidad.principal import principal_actual

    quien = principal_actual()
    await almacen().guardar_turno(turno_id, session_id, {
        "session_id": session_id,
        "consulta": query_request.model_dump(mode="json"),
        "principal": ({"sub": quien.sub, "org_id": quien.org_id, "roles": sorted(quien.roles),
                       "nombre": quien.nombre, "via": quien.via} if quien else None),
    }, ttl_s=ttl_s)


def _lanzar_grafo(agent_graph: Any, query_request: QueryRequest, query_to_process: str, t: Turno,
                  turno_id: str, timeout: float) -> asyncio.Task[Any]:
    """La tarea del grafo con el contexto del turno. Las aprobaciones que pida quedan ligadas a ESTE
    turno, y sus llamadas al LLM no deciden esperar un 429 más allá de su `wait_for` (la tarea
    hereda el contexto)."""
    from geo_copilot.platform.estado.aprobaciones import fijar_turno, soltar_turno

    token_turno = fijar_turno(turno_id)
    try:
        with plazo_turno(timeout):
            tarea: asyncio.Task[Any] = asyncio.ensure_future(agent_graph.process(
                query=query_to_process,
                session_id=t.session_id,
                conversation_history=t.conversation_history,
                previous_sql=t.previous_sql,
                previous_results=t.previous_results,
                previous_geojson=t.previous_geojson,
                found_services=t.found_services,
                external_geojson=t.external_geojson,
                external_source_name=t.external_source_name,
                has_external_data=t.has_external_data,
                active_data_source=t.active_data_source.value,
                active_source_name=t.active_source_name,
                map_context=t.map_context_dict,
                map_layers=t.map_layers,  # FRT-04: capas por id (target por nombre)
                session_region=query_request.session_region,  # F2.2
            ))
            return tarea
    finally:
        soltar_turno(token_turno)


async def correr_grafo(agent_graph: Any, query_request: QueryRequest, query_to_process: str, t: Turno,
                       *, turno_id: str, query_id: str) -> dict | QueryResponse:
    """El grafo con su plazo (cómputo + HITL), registrado fuera del proceso y cancelable.

    SEC-9: timeout duro — un LLM colgado o un agente en bucle no debe bloquear la conexión y un slot
    del pool indefinidamente. Reloj de cómputo + reloj de espera humana (HITL) separados: el
    presupuesto de cómputo NO debe capar la ventana de aprobación."""
    from geo_copilot.api.websocket import connection_manager, send_status
    from geo_copilot.platform.estado.aprobaciones import MARGEN_S

    session_id = t.session_id
    tarea: asyncio.Future[Any] | None = None
    try:
        _process_timeout = _rutas().compute_process_timeout(get_settings())
        await _registrar_turno(turno_id, session_id, query_request, _process_timeout + MARGEN_S)
        t.registrado = True
        tarea = _lanzar_grafo(agent_graph, query_request, query_to_process, t, turno_id, _process_timeout)
        # «Detener» cancela ESTA tarea (antes solo se registraban las consultas del WebSocket:
        # en la UI, que consulta por REST, el botón no paraba nada)
        await connection_manager.register_task(session_id, tarea)
        try:
            result: dict = await asyncio.wait_for(tarea, timeout=_process_timeout)
        finally:
            await connection_manager.clear_task(session_id, tarea)
    except asyncio.CancelledError:
        if tarea is None or not tarea.cancelled():
            raise  # nos cancelan a nosotros (el servidor se apaga): se propaga
        logger.info(f"[Query] {query_id} cancelada por el usuario")
        return QueryResponse(query_id=query_id, session_id=session_id, status=QueryStatus.FAILED.value,
                             message="Consulta detenida.", artifacts=[])
    except TimeoutError as exc:
        logger.error(f"[Query] Graph timed out after {_process_timeout}s (cómputo+HITL)")
        # Cerrar el pipeline en el cliente
        await send_status(session_id, "error", {"error": "timeout"})
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="La consulta excedió el tiempo máximo permitido (cómputo + aprobación).",
        ) from exc
    logger.info(f"[Query] Graph finished, success={result.get('success')}")
    return result
