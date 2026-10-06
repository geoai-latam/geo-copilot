"""
Endpoints para procesamiento de consultas.

Sistema basado en LangGraph con arquitectura A2A (Agent-to-Agent).
Requiere LLM configurado (Azure, OpenAI o Anthropic).
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from geo_copilot.api.dependencies import (
    get_agent_graph,
    get_conversation_manager,
)
from geo_copilot.api.limiter import limiter
from geo_copilot.api.models import (
    ErrorResponse,
    QueryRequest,
    QueryResponse,
    QueryStatus,
)
from geo_copilot.api.websocket import sanitize_error_for_client
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.conversation import ConversationManager
from geo_copilot.orchestrator.graph import GeoAgentGraph

logger = get_logger(__name__)

from geo_copilot.api.auth import asegurar_sesion_actual, require_principal
from geo_copilot.api.routes.turno import (  # F4: los pasos del turno
    Turno,
    consulta_a_procesar,
    correr_grafo,
    leer_mapa,
    leer_sesion,
)
from geo_copilot.api.routes.turno_salida import (
    recordar_en_sesion,
    respuesta_del_turno,
    resultados_del_turno,
)
from geo_copilot.api.routes.turno_workspace import (  # noqa: F401 — F4: el workspace del turno
    _geojson_del_workspace,
    _huella,
    _materializar_resultado,
    _muestra_para_estilo,
)

router = APIRouter(
    prefix="/query",
    tags=["Query"],
    dependencies=[Depends(require_principal)],
)


# =============================================================================
# HITL: separación del reloj de cómputo y el de espera humana
# =============================================================================

def compute_process_timeout(settings) -> int:
    """Timeout del ``wait_for`` que envuelve ``agent_graph.process``.

    ``total_execution_timeout`` es el presupuesto de CÓMPUTO (guardia contra un
    LLM colgado o un agente en bucle). ``hitl_timeout`` es cuánto se espera a un
    HUMANO para aprobar código/SQL de alto riesgo. Son relojes DISTINTOS: si el
    wait_for de cómputo envuelve la espera de aprobación —que internamente ya se
    auto-limita con ``hitl_timeout`` (ver security/hitl.py)— una aprobación
    lenta cancela la task antes de que la persona responda → 504 engañoso
    ("excedió el tiempo de procesamiento", falso) y el "Aprobar" posterior da
    404 sobre un request ya descartado, derrotando el mecanismo de seguridad.

    Sumamos el presupuesto de HITL cuando está activo para que la espera humana
    quepa dentro del wait_for sin capar el reloj de cómputo.
    """
    budget: int = settings.total_execution_timeout
    if getattr(settings, "hitl_enabled", False):
        budget += settings.hitl_timeout
    return budget


# =============================================================================
# SEGURIDAD: Validación de GeoJSON
# =============================================================================

VALID_GEOJSON_TYPES = frozenset([
    "FeatureCollection", "Feature", "Point", "LineString", "Polygon",
    "MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection"
])


def validate_geojson(data: Any, max_features: int | None = None) -> tuple[bool, str]:
    """
    Valida estructura básica de GeoJSON para seguridad.

    Args:
        data: Datos a validar
        max_features: Máximo de features permitidos (usa config si no se especifica)

    Returns:
        Tupla (es_valido, mensaje_error)
    """
    if max_features is None:
        max_features = get_settings().max_geojson_features

    if not isinstance(data, dict):
        return False, "GeoJSON debe ser un objeto"

    geojson_type = data.get("type")
    if geojson_type not in VALID_GEOJSON_TYPES:
        return False, f"Tipo GeoJSON no válido: {geojson_type}"

    if geojson_type == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            return False, "FeatureCollection.features debe ser una lista"

        if len(features) > max_features:
            return False, f"Demasiados features: {len(features)} (máximo: {max_features})"

        # Validación básica de cada feature
        for i, feature in enumerate(features[:100]):  # Solo validar primeros 100 para performance
            if not isinstance(feature, dict):
                return False, f"Feature {i} no es un objeto válido"
            if feature.get("type") != "Feature":
                return False, f"Feature {i} no tiene type='Feature'"

    elif geojson_type == "Feature":
        if "geometry" not in data:
            return False, "Feature debe tener geometry"

    return True, ""


@router.post(
    "/",
    response_model=QueryResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": ErrorResponse, "description": "LLM not configured"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Process a natural language query",
    description="Processes a geospatial query using LangGraph A2A architecture.",
)
@limiter.limit(lambda: f"{get_settings().rate_limit_requests}/minute")
async def process_query(
    request: Request,  # Requerido por slowapi - DEBE llamarse "request"
    query_request: QueryRequest,
    agent_graph: GeoAgentGraph | None = Depends(get_agent_graph),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> QueryResponse:
    """
    Procesar consulta usando LangGraph con arquitectura A2A.

    El grafo de agentes procesa la consulta pasando por:
    Router → DataAgent → GISAgent → SymbologyAgent → InsightsAgent
    """
    from geo_copilot.platform import observabilidad

    turno_id = uuid.uuid4().hex
    # F7 (auditoría): si el cliente se va (recargó con una aprobación pendiente y la aprobó
    # desde la pestaña nueva), la respuesta HTTP ya no llega a nadie: se entrega por el WebSocket.
    vigia = asyncio.ensure_future(_esperar_desconexion(request))
    espera = None
    try:
        # F7 (S7.3): el turno es el span raíz de lo que hace el agente (herramientas, LLM, MCP)
        async with turno_observado(query_request.session_id, "rest") as obs:
            espera = observabilidad.espera_del_turno()
            try:
                respuesta = await ejecutar_consulta(query_request, agent_graph, conversation_manager,
                                                    turno_id=turno_id)
            except HTTPException as exc:
                if _entregar_tambien_por_ws(vigia, espera) and query_request.session_id:
                    await entregar_resultado_por_ws(query_request.session_id, turno_id, query_request.query,
                                                    error=str(exc.detail))
                raise
            obs["ok"] = respuesta.status != QueryStatus.FAILED.value
        if _entregar_tambien_por_ws(vigia, espera):
            await entregar_resultado_por_ws(respuesta.session_id, turno_id, query_request.query, respuesta=respuesta)
        return respuesta
    finally:
        if vigia.done() and not vigia.cancelled():
            vigia.exception()  # recogida: sin «Task exception was never retrieved»
        vigia.cancel()


async def _esperar_desconexion(request: Request) -> bool:
    """Termina cuando el cliente HTTP se desconecta (el cuerpo ya se leyó: solo queda ese aviso)."""
    while True:
        mensaje = await request.receive()
        if mensaje.get("type") == "http.disconnect":
            return True


def _se_fue(vigia: "asyncio.Future[bool]") -> bool:
    return vigia.done() and not vigia.cancelled() and vigia.exception() is None and bool(vigia.result())


def _entregar_tambien_por_ws(vigia: "asyncio.Future[bool]", espera: Any) -> bool:
    """¿El resultado va también por el WebSocket? Si el cliente HTTP se fue, o si el turno esperó a
    una persona: mientras esperaba, la pestaña pudo recargarse (o la aprobó otra) y la petición
    HTTP puede seguir «viva» para el servidor sin que nadie la lea — un proxy (Vite, un balanceador)
    no siempre propaga el cierre (visto en V5). El cliente ignora la copia si su consulta sigue."""
    return _se_fue(vigia) or bool(getattr(espera, "aprobaciones", 0))


@contextlib.asynccontextmanager
async def turno_observado(session_id: str | None, canal: str) -> AsyncIterator[dict[str, bool]]:
    """Span raíz «turno», métrica de duración y id de correlación de un turno (F7, S7.3).

    F7 (auditoría): solo lo tenía la ruta REST; los turnos del WebSocket y los reanudados tras un
    reinicio salían sin métrica y con sus spans y logs sueltos. La duración NO incluye lo que el
    turno esperó a una persona (aprobaciones: van a `geo_hitl_espera_segundos`); la tarea del turno
    se crea dentro, así hereda la cuenta. El llamante marca `ok`."""
    import time

    from geo_copilot.platform import observabilidad

    token = observabilidad.fijar_correlacion(observabilidad.correlacion() or uuid.uuid4().hex[:16])
    inicio, estado = time.perf_counter(), {"ok": False}
    try:
        with (observabilidad.contar_espera_humana() as espera,
              observabilidad.span("turno", **{"geo.sesion": session_id, "geo.canal": canal})):
            try:
                yield estado
            finally:
                observabilidad.registrar_turno(time.perf_counter() - inicio - espera.segundos, estado["ok"])
    finally:
        token.var.reset(token)


async def entregar_resultado_por_ws(
    session_id: str, turno_id: str, consulta: str, *,
    respuesta: QueryResponse | None = None, error: str | None = None, reanudada: bool = False,
) -> None:
    """La respuesta de un turno cuya petición HTTP ya no existe, por el WebSocket de la sesión.

    F7 (auditoría): el cliente la aplica como cualquier respuesta; `turno_id` le dice de qué turno
    es. Si el socket tampoco está, se registra (al reconectar, el cliente ve que el turno ya no
    está en curso y lo dice)."""
    from geo_copilot.api.websocket import WSMessage, WSMessageType, connection_manager

    datos: dict[str, Any] = {"entrega": "ws", "turno_id": turno_id, "consulta": consulta,
                             "reanudada": reanudada}
    if respuesta is not None:
        datos.update(success=respuesta.status != QueryStatus.FAILED.value,
                     respuesta=respuesta.model_dump(mode="json"))
    else:
        datos.update(success=False, error=error or "No se pudo completar la consulta.")
    enviado = await connection_manager.send_message(session_id, WSMessage(type=WSMessageType.RESULT, data=datos))
    logger.info(f"[Query] turno {turno_id[:8]} entregado por WebSocket a {session_id} (enviado={enviado})")


async def ejecutar_consulta(  # noqa: PLR0912, PLR0915
    query_request: QueryRequest,
    agent_graph: GeoAgentGraph | None,
    conversation_manager: ConversationManager,
    *,
    reanudada: bool = False,
    turno_id: str | None = None,
) -> QueryResponse:
    """El turno completo, fuera de la petición HTTP (F7): lo llama la ruta y también la
    reanudación de un turno que murió con su proceso esperando una aprobación (E7.1).

    `reanudada`: el mensaje del usuario ya está en el historial (lo añadió el turno original).
    `turno_id`: el id del turno (el reanudado conserva el del original: sus aprobaciones van
    ligadas a él).
    """
    query_id = str(uuid.uuid4())[:8]
    turno_id = turno_id or uuid.uuid4().hex

    # Log para debug
    logger.info(f"[Query] Processing {query_id}: {query_request.query[:100]}")

    # Verificar que hay LLM configurado (el grafo lo necesita)
    if not agent_graph:
        logger.error("[Query] No LLM configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM no configurado. Configura LLM_PROVIDER y las credenciales en .env"
        )

    # F6: la sesión de otro no existe para quien pregunta (fuera del try: un 404, no un 500)
    if query_request.session_id:
        await asegurar_sesion_actual(query_request.session_id)
    from geo_copilot.platform import auditoria

    await auditoria.registrar("consulta", "agente", "recibida", session_id=query_request.session_id,
                              detalle={"consulta": query_request.query, "canal": "rest"})
    registrado = False  # F7 (auditoría): el registro del turno se cierra al final, con la respuesta hecha
    try:
        # Obtener o crear sesión
        if query_request.session_id:
            context = conversation_manager.get_or_create_session(query_request.session_id)
        else:
            context = conversation_manager.create_session()
            await asegurar_sesion_actual(context.session_id)

        session_id = context.session_id
        logger.info(f"[Query] Session: {session_id}, executing agent graph")

        t = Turno(session_id=session_id)
        await leer_sesion(context, t)
        await leer_mapa(query_request, context, t)
        query_to_process = consulta_a_procesar(query_request, context, t)

        # Agregar mensaje del usuario al historial (al reanudar ya estaba: lo añadió el turno original)
        if not reanudada:
            context.add_user_message(query_request.query)

        # Notificar al WS que arrancó (el cliente abre el chip de pipeline). Sin WS, no-op.
        from geo_copilot.api.websocket import send_result, send_status
        await send_status(session_id, "processing", {"query": query_to_process[:200], "turno_id": turno_id})

        try:
            result = await correr_grafo(agent_graph, query_request, query_to_process, t,
                                        turno_id=turno_id, query_id=query_id)
        finally:
            registrado = t.registrado  # también si excede el plazo: el registro se cierra igual
        if isinstance(result, QueryResponse):  # «Detener»
            return result

        # Cerrar el chip de pipeline en el cliente
        await send_result(session_id, {"success": bool(result.get("success"))})

        # S2.1: el resultado geográfico del turno se MATERIALIZA en el workspace
        # de la sesión (cruzable en SQL, servible como teselas). Best-effort: el
        # workspace nunca rompe una respuesta que ya funcionaba.
        # S2.5: si una capacidad ws_* ya creó el dataset, ese ES el resultado.
        layer_ref = result.get("result_layer_ref") or await _materializar_resultado(
            session_id, query_to_process, result, ya_en_workspace=t.hidratadas,
        )
        results_data, tiles, row_count = resultados_del_turno(result, layer_ref, session_id, t.active_layer_id)
        recordar_en_sesion(context, result, layer_ref)
        return await respuesta_del_turno(
            result, t, context, conversation_manager, agent_graph, query_request, query_id=query_id,
            layer_ref=layer_ref, results_data=results_data, tiles=tiles, row_count=row_count)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing query {query_id}: {e}", exc_info=True)
        # Cerrar el chip de pipeline en el cliente, si la sesión existe
        try:
            from geo_copilot.api.websocket import send_status
            sid = locals().get("session_id")
            if sid:
                await send_status(sid, "error", {"error": "internal_error"})
        except Exception:  # noqa: BLE001
            pass
        # Sanitizar error antes de enviar al cliente
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=sanitize_error_for_client(e)
        ) from e
    finally:
        # «En curso» hasta tener la respuesta: el cliente que reconecta en medio no da el turno por
        # perdido mientras aún se construye.
        if registrado:
            from geo_copilot.platform.estado.aprobaciones import almacen

            try:
                await almacen().cerrar_turno(turno_id)
            except Exception:  # F7 (auditoría): el registro caduca solo (TTL); no tapa el resultado
                logger.warning(f"[Query] no se pudo cerrar el registro del turno {turno_id[:8]}", exc_info=True)


# =============================================================================
# Endpoints stub para futuras funcionalidades
#
# Tracking asíncrono y cancelación NO están implementados todavía. La
# infraestructura mínima existe (``GraphState.cancelled`` + PlanExecutor
# verifica el flag entre pasos), pero falta: (a) un store de queries en
# vuelo indexado por ``query_id``, y (b) un mecanismo para que la API
# levante el flag en el state del grafo en curso.
#
# Hasta que se implemente, devolvemos 501 (Not Implemented) honestamente
# — antes ``/cancel`` devolvía 200 con ``{cancelled: true}`` aunque no
# cancelaba nada, y un cliente tenía cero forma de saber que la cancelación
# fue una mentira. Mejor errar visible.
# =============================================================================


@router.get(
    "/{query_id}",
    response_model=QueryResponse,
    responses={501: {"model": ErrorResponse}},
    summary="Get query status",
    description=(
        "[NO IMPLEMENTADO] Endpoint para obtener estado de query asíncrona. "
        "Devuelve 501 hasta que se implemente tracking por ``query_id``."
    ),
)
async def get_query_status(query_id: str) -> QueryResponse:
    """Obtener estado de una consulta — devuelve 501 honestamente."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Async query tracking no implementado. Las queries son síncronas "
            "actualmente y se completan dentro del POST /query/."
        ),
    )


@router.post(
    "/{query_id}/cancel",
    responses={501: {"model": ErrorResponse}},
    summary="Cancel a query",
    description=(
        "[NO IMPLEMENTADO] Endpoint para cancelar query en ejecución. "
        "Devuelve 501 hasta que se implemente."
    ),
)
async def cancel_query(query_id: str) -> dict:
    """Cancelar una consulta — devuelve 501 (antes mentía con 200)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Cancelación no implementada. Hay un flag ``cancelled`` en el "
            "GraphState que el PlanExecutor lee entre pasos, pero falta "
            "wiring API→state para levantarlo. Ver issue de seguimiento."
        ),
    )
