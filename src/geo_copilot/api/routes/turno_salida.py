"""La SALIDA de un turno de consulta (F4 del plan de calidad): qué se entrega, qué se recuerda y la
respuesta. La entrada (sesión, mapa, grafo) está en `turno.py`. Código movido tal cual desde
`query.ejecutar_consulta`.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.api.models import QueryRequest, QueryResponse, QueryStatus
from geo_copilot.api.routes.turno import Turno
from geo_copilot.core.config import get_settings
from geo_copilot.core.error_sanitizer import describe_error
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.conversation import DataSource

#: el mismo logger que query.py: los registros del turno siguen saliendo con su nombre
logger = get_logger("geo_copilot.api.routes.query")


def _correccion(result: dict) -> dict | None:
    """Info de auto-corrección (si hubo reintentos). S0.4: nunca el error crudo (nombres de tabla,
    SQLSTATE, DETAIL/HINT): solo su categoría del vocabulario cerrado."""
    retry_count = result.get("retry_count", 0)
    if retry_count <= 0:
        return None
    logger.info(f"[Query] Auto-correction applied: {retry_count} corrections")
    return {
        "corrections_applied": retry_count,
        "original_error": describe_error(
            (result.get("error_context") or {}).get("all_errors", [None])[0],
            context="query.correction_info",
        ),
        "final_success": result.get("success", False),
    }


def _por_teselas(results_data: dict, data: Any, layer_ref: dict | None, session_id: str, row_count: int) -> Any:
    """S2.3: una capa grande no viaja inline: se entrega como teselas MVT del workspace (el navegador
    pide solo lo visible). H22 (V5 F2): la TABLA tampoco viaja entera — con 41.033 puntos la respuesta
    pesaba 18 MB por las filas de atributos aunque la geometría ya fuera por teselas. Se entregan las
    primeras (tope inline) marcadas como truncadas; el total real va aparte."""
    from geo_copilot.api.routes.workspace import teselas_de

    tiles = teselas_de(session_id, layer_ref)
    if tiles is not None:
        results_data["geojson"] = None
        results_data["tiles"] = tiles
        tope_filas = get_settings().workspace_inline_max_features
        if isinstance(data, dict) and len(data.get("results") or []) > tope_filas:
            results_data["data"] = {
                **data, "results": data["results"][:tope_filas],
                "truncated": True, "total": row_count,
            }
    return tiles


def resultados_del_turno(result: dict, layer_ref: dict | None, session_id: str,
                         active_layer_id: str | None) -> tuple[Any, Any, int]:
    """(results_data, tiles, row_count): qué se entrega del turno (filas, teselas, corrección).

    found_services solo cuenta como resultado si ESTE turno ejecutó una búsqueda nueva. Sin el flag las
    cards se duplicaban en cada follow-up porque el grafo propaga found_services del state previo."""
    new_search = bool(result.get("new_search_executed"))
    has_results = (
        result.get("data")
        or result.get("sql")
        or result.get("geojson")
        or layer_ref  # S2.5: resultado grande de una ws_* (va por teselas)
        or result.get("external_imagery")  # MapServer/ImageServer como tile layer
        or (new_search and result.get("found_services"))  # cards solo en búsquedas nuevas
    )
    if not has_results:
        return None, None, 0
    data = result.get("data")
    results_list = data.get("results") if data and isinstance(data, dict) else None
    row_count = len(results_list) if results_list else 0
    results_data = {
        "sql": result.get("sql"),
        "data": data,
        "geojson": result.get("geojson"),
        "symbology": result.get("symbology"),
        "layer_name": result.get("layer_name"),
        # FRT-04: capa objetivo del re-estilo. Prioriza el target que el LLM (router/tool) RESOLVIÓ por
        # nombre; si no eligió ninguna, la activa que el mapa reportó (no «la última añadida»).
        "target_layer_id": result.get("target_layer_id") or active_layer_id,
        "row_count": row_count,
        "correction_info": _correccion(result),
        "external_imagery": result.get("external_imagery"),  # capa imagery (MapServer / ImageServer)
        # cards de servicios: solo si el turno ejecutó búsqueda nueva (si no, es el state heredado)
        "found_services": result.get("found_services") if new_search else None,
        "layer_ref": layer_ref,  # S2.1: el dataset del workspace que respalda la capa (LayerRef)
    }
    tiles = _por_teselas(results_data, data, layer_ref, session_id, row_count)
    logger.debug(f"[Query] Results: sql={bool(result.get('sql'))}, data={bool(result.get('data'))}, geojson={bool(result.get('geojson'))}")
    if result.get("geojson"):
        logger.debug(f"[Query] GeoJSON features: {len(result.get('geojson', {}).get('features', []))}")
    if result.get("symbology"):
        logger.debug(f"[Query] Symbology: {result.get('layer_name')}")
    return results_data, tiles, row_count


def _recordar_fuente_interna(context: Any, result: dict) -> None:
    """Un geojson de la BD interna: la fuente activa pasa a INTERNAL (y se olvidan los datos externos)."""
    # IMPORTANTE: Manejar fuente de datos activa
    # Si hay geojson de BD interna, limpiar datos externos y marcar como INTERNAL
    if result.get("geojson") and not result.get("external_geojson"):
        context.update_state(last_geojson=result.get("geojson"))
        # Limpiar datos externos para evitar confusión
        context.clear_external_data()
        # Determinar nombre de la tabla desde el SQL
        table_name = "base de datos interna"
        if result.get("sql"):
            # Intentar extraer nombre de tabla del SQL
            sql_lower = result.get("sql", "").lower()
            if "from " in sql_lower:
                parts = sql_lower.split("from ")[1].split()[0]
                table_name = parts.replace('"', '').replace("'", "")
        context.set_active_data_source(DataSource.INTERNAL, table_name)
        logger.info(f"[Query] Active data source → INTERNAL ({table_name})")


def _recordar_datos_externos(context: Any, result: dict, layer_ref: dict | None) -> None:
    """Datos externos para operaciones espaciales (PythonAgent); una capa grande vive en el workspace."""
    # Guardar datos externos para operaciones espaciales (PythonAgent)
    if result.get("external_geojson"):
        source_name = result.get("external_source_name", "servicio externo")
        context.set_variable("external_source_name", source_name)
        n_ext = len(result["external_geojson"].get("features") or [])
        if n_ext > get_settings().max_geojson_features:
            # S2.3: una capa externa grande no cabe en la sesión (Redis) ni
            # pasaría la validación del turno siguiente: vive en el
            # workspace y la sesión guarda solo su id.
            context.set_variable("external_geojson", None)
            context.update_state(
                last_geojson=None,
                last_dataset_id=(layer_ref or {}).get("id"),
            )
        else:
            context.set_variable("external_geojson", result.get("external_geojson"))
            # También guardar en last_geojson para consistencia
            context.update_state(last_geojson=result.get("external_geojson"))
        # Marcar fuente activa como EXTERNAL
        context.set_active_data_source(DataSource.EXTERNAL, source_name)
        feature_count = len(result.get("external_geojson", {}).get("features", []))
        logger.info(f"[Query] Active data source → EXTERNAL ({source_name}, {feature_count} features)")


def recordar_en_sesion(context: Any, result: dict, layer_ref: dict | None) -> None:
    """Lo que el turno siguiente necesita saber (SQL, datos, fuente activa, servicios, plan pausado)."""
    # Guardar en contexto para preguntas de seguimiento
    if result.get("sql"):
        context.update_state(last_sql=result.get("sql"))
    if result.get("data"):
        context.update_state(last_results=result.get("data"))

    _recordar_fuente_interna(context, result)

    # T2.0a: la capa mostrada quedó en el workspace → la sesión la recuerda
    # por referencia y suelta la copia de la geometría (no viaja a Redis).
    if layer_ref:
        context.update_state(last_geojson=None, last_dataset_id=layer_ref["id"])
    elif result.get("geojson") or result.get("external_geojson"):
        context.update_state(last_dataset_id=None)

    # Guardar servicios encontrados para selección por número en
    # siguiente turno.
    #
    # ORDEN IMPORTANTE: si el turno actual produjo found_services
    # NUEVOS (búsqueda), los persistimos sin importar si en el state
    # quedó external_geojson o external_imagery heredados del turno
    # previo. Solo limpiamos found_services si NO hay nuevos Y SÍ
    # se cargó algo (es decir: el turno fue select_service y la lista
    # se consumió).
    new_services = result.get("found_services")
    if new_services:
        context.set_variable("found_services", new_services)
    elif result.get("external_imagery") or result.get("external_geojson"):
        # No vinieron nuevos servicios Y se cargó algo → la lista
        # anterior ya se consumió. Limpiar para no mostrarla de nuevo.
        context.set_variable("found_services", None)

    # Guardar operaciones pendientes si el plan pausó (para retomar después)
    if result.get("pending_operations"):
        context.set_variable("pending_operations", result.get("pending_operations"))
        logger.info(f"[Query] Plan paused with {len(result.get('pending_operations') or [])} pending operations")

    _recordar_datos_externos(context, result, layer_ref)

async def _artefactos(result: dict, t: Turno, *, layer_ref: dict | None, tiles: Any, results_data: Any,
                      row_count: int) -> tuple[list, Any, dict]:
    """(artefactos, datos_recortados, result). F4: la respuesta son artefactos del contrato (el
    frontend los dibuja con su registro de renderers). `results_data` sigue siendo el estado interno
    del turno (contexto de la sesión, corrección), no se envía."""
    from geo_copilot.platform.artefactos import construir_artefactos

    map_context_dict, active_layer_id = t.map_context_dict, t.active_layer_id
    # Las filas que viajan son las ya recortadas al tope inline (H22): una tabla
    # de 41 033 filas excede el contrato y se perdería entera.
    datos_recortados = (results_data or {}).get("data", result.get("data"))
    # Capa grande (estilizada sobre una muestra, H23): la leyenda cuenta sobre la capa entera.
    # Vale para una capa nueva por teselas y para re-estilar una que ya está en el mapa.
    ds_estilo = (layer_ref or {}).get("id") if tiles is not None else next(
        (c.get("dataset_id") for c in (map_context_dict or {}).get("layers", [])
         if c.get("id") == (result.get("target_layer_id") or active_layer_id)), None)
    if result.get("symbology") and ds_estilo:
        from geo_copilot.api.dependencies import get_app_state
        from geo_copilot.platform.workspace.clases import recontar_clases

        result = {**result, "symbology": await recontar_clases(
            get_app_state().dataset_store, t.session_id, ds_estilo, result["symbology"])}
    artefactos = construir_artefactos(
        # el mapa de ESTA petición: el resultado del grafo no lo trae (V5: la capa con un
        # campo nuevo se añadía encima en vez de actualizarse en su sitio)
        {**result, "data": datos_recortados, "map_context": result.get("map_context") or map_context_dict},
        layer_ref=layer_ref, tiles=tiles,
        target_layer_id=result.get("target_layer_id") or active_layer_id, row_count=row_count,
    )
    return artefactos, datos_recortados, result


def _estado(result: dict) -> tuple[QueryStatus, bool, Any]:
    """Fase 6 #3 / API-8: el estado HITL del path REST tal como lo reportó el grafo (antes se forzaba
    a False/None y una consulta esperando aprobación devolvía COMPLETED/FAILED)."""
    requires_approval = bool(result.get("requires_approval"))
    pending_approval_id = result.get("pending_approval_id")
    if requires_approval and pending_approval_id:
        return QueryStatus.WAITING_APPROVAL, requires_approval, pending_approval_id
    if result.get("success"):
        return QueryStatus.COMPLETED, requires_approval, pending_approval_id
    return QueryStatus.FAILED, requires_approval, pending_approval_id


async def _sugerencias(agent_graph: Any, query_request: QueryRequest, final_message: str, artefactos: list,
                       status_value: QueryStatus, map_context_dict: dict | None) -> list[str]:
    """FH.8: siguientes pasos que propone el LLM (solo tras una respuesta completada). FH.9 (V5): si el
    agente espera algo en el mapa, lo siguiente es responderle, no otra cosa."""
    espera_en_mapa = any(getattr(getattr(a, "command", None), "op", None) == "request_input" for a in artefactos)
    if not (status_value == QueryStatus.COMPLETED and final_message and not espera_en_mapa
            and get_settings().sugerencias_habilitadas):
        return []
    from geo_copilot.platform.sugerencias import sugerir

    return await sugerir(agent_graph.llm, consulta=query_request.query, respuesta=final_message,
                         map_context=map_context_dict)


async def respuesta_del_turno(result: dict, t: Turno, context: Any, conversation_manager: Any, agent_graph: Any,
                              query_request: QueryRequest, *, query_id: str, layer_ref: dict | None,
                              results_data: Any, tiles: Any, row_count: int) -> QueryResponse:
    """Artefactos del contrato, historial, estado HITL y sugerencias."""
    artefactos, datos_recortados, result = await _artefactos(
        result, t, layer_ref=layer_ref, tiles=tiles, results_data=results_data, row_count=row_count)
    # Agregar respuesta del asistente al historial, con lo que se ENTREGÓ: el
    # turno siguiente lo ve como hecho, no solo lo que la respuesta afirma.
    context.add_assistant_message(
        result.get("message", ""),
        sql=result.get("sql"),
        row_count=results_data.get("row_count", 0) if results_data else 0,
        artefactos=[a.kind for a in artefactos],
        # FH.1 (V5): qué se intentó y qué falló, como HECHO para el turno siguiente
        # («prueba de nuevo» no depende de interpretar la redacción de la respuesta).
        intent=result.get("intent"),
        fallidas=sorted({
            str(d.get("tool")) for d in (result.get("decision_trace") or [])
            if isinstance(d, dict) and d.get("kind") == "tool_result" and d.get("success") is False
        }),
    )
    final_message = result.get("message", "")
    if result.get("retry_count", 0) > 0 and result.get("success"):
        final_message = f"[Auto-corregido] {final_message}"  # nota de corrección
    status_value, requires_approval, pending_approval_id = _estado(result)
    # V5 F3: con un backend que serializa (Redis) mutar el contexto no basta;
    # sin guardarlo, el turno siguiente llegaba sin historial ni estado.
    conversation_manager.save_session(context)
    _geo = result.get("geojson")
    _gj = _geo if isinstance(_geo, dict) else {}
    _filas = datos_recortados.get("results") if isinstance(datos_recortados, dict) else None
    logger.info(
        f"[Query] {query_id} artefactos={[a.kind for a in artefactos]} ← "
        f"geojson={len(_gj.get('features') or [])} filas={len(_filas or [])} "
        f"layer_ref={(layer_ref or {}).get('id', '-')} tiles={tiles is not None} "
        f"viz={bool(result.get('visualization'))} imagery={bool(result.get('external_imagery'))}"
    )
    sugerencias = await _sugerencias(agent_graph, query_request, final_message, artefactos, status_value,
                                     t.map_context_dict)
    return QueryResponse(
        query_id=query_id, session_id=t.session_id, status=status_value.value, intent=result.get("intent"),
        confidence=None, message=final_message, requires_approval=requires_approval,
        pending_approval_id=pending_approval_id, artifacts=artefactos, sql=result.get("sql"),
        suggestions=sugerencias, correction=(results_data or {}).get("correction_info"),
        reasoning_trace=result.get("reasoning_trace"),
    )
