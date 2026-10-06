"""
WebSocket handler para streaming de respuestas.

Permite comunicación bidireccional en tiempo real
para consultas de larga duración y actualizaciones de progreso.
"""

import asyncio
import json
import re
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from geo_copilot.api.dependencies import get_app_state
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# F4.6: los mensajes, las conexiones y los envíos viven en sus módulos; se reexportan aquí porque
# el resto del núcleo y las pruebas los importan desde `api.websocket`.
from geo_copilot.api.ws_conexiones import (
    ConnectionManager,
)
from geo_copilot.api.ws_envios import (  # noqa: F401
    WebSocketEventSink,
    send_agent_step,
    send_approval_request,
    send_execution_cancelled,
    send_plan_created,
    send_progress_update,
    send_result,
    send_retry_correction,
    send_retry_result,
    send_retry_started,
    send_status,
    send_step_progress,
)
from geo_copilot.api.ws_mensajes import (  # noqa: F401
    _WS_MSG_CONFIG,
    WSApprovalMessage,
    WSCancelMessage,
    WSMessage,
    WSMessageType,
    WSPingMessage,
    WSQueryMessage,
    sanitize_error_for_client,
    validate_ws_message,
)

# =============================================================================
# SEGURIDAD: Sanitización de errores y validación de mensajes
# =============================================================================





















# Singleton del gestor de conexiones
connection_manager = ConnectionManager()


#: las consultas del WS que corren aparte del bucle (referencia fuerte: asyncio solo guarda una débil
#: y una tarea suelta podía recolectarse a mitad si el cliente se desconectaba)
_CONSULTAS_EN_CURSO: set[asyncio.Task[Any]] = set()


def _en_segundo_plano(corrutina: Any) -> None:
    tarea = asyncio.create_task(corrutina)
    _CONSULTAS_EN_CURSO.add(tarea)
    tarea.add_done_callback(_CONSULTAS_EN_CURSO.discard)


async def _en_orden(candado: asyncio.Lock, corrutina: Any) -> None:
    async with candado:
        await corrutina


async def _despachar(session_id: str, message_type: Any, message_data: dict, app_state: Any,
                     en_orden: asyncio.Lock) -> None:
    """Un mensaje del cliente. La consulta corre APARTE y el bucle sigue leyendo: su aprobación o su
    «Detener» llegan por el mismo socket mientras corre (antes se leían DESPUÉS de ella y el turno
    quedaba colgado esperando una aprobación que nadie leía). En orden: una consulta de la conexión
    empieza cuando termina la anterior."""
    mensaje = handle_message(session_id=session_id, message_type=message_type,
                             message_data=message_data, app_state=app_state)
    if message_type == WSMessageType.QUERY.value:
        _en_segundo_plano(_en_orden(en_orden, mensaje))
    else:
        await mensaje


async def _enviar(session_id: str, tipo: WSMessageType, datos: dict) -> None:
    """Un mensaje al cliente de la sesión (el gestor se resuelve al usarse: las pruebas lo sustituyen)."""
    await connection_manager.send_message(session_id, WSMessage(type=tipo, data=datos))

# Mismo formato que valida el REST (`routes/session.py:_SESSION_ID_RE`).
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{1,100}$")

# Códigos de cierre de aplicación (rango 4000-4999). 4403 ya lo usa SEC-4.
WS_CLOSE_INVALID_SESSION = 4400
WS_CLOSE_UNKNOWN_SESSION = 4404
WS_CLOSE_SESSION_IN_USE = 4409


async def _session_rejection(session_id: str) -> tuple[int, str] | None:
    """Motivo para rechazar el socket de ``session_id``, o ``None`` si vale."""
    if not _SESSION_ID_RE.match(session_id or ""):
        return WS_CLOSE_INVALID_SESSION, "session_id inválido"
    conversation_manager = get_app_state().conversation_manager
    if conversation_manager is None or not conversation_manager.session_exists(session_id):
        return WS_CLOSE_UNKNOWN_SESSION, "sesión inexistente"
    # Local a propósito: tras la caída de un worker, su presencia en Redis tarda en caducar y no
    # debe impedir que la pestaña se reconecte.
    if await connection_manager.esta_aqui(session_id):
        return WS_CLOSE_SESSION_IN_USE, "la sesión ya tiene una conexión activa"
    return None


async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    Endpoint principal de WebSocket.

    Maneja la comunicación bidireccional para:
    - Envío de consultas
    - Recepción de actualizaciones de progreso
    - Aprobaciones HITL
    - Resultados y visualizaciones

    Incluye:
    - Validación de sesión antes de aceptar conexión
    - Validación opcional de token
    - Timeout configurable para evitar DoS
    - Validación de mensajes entrantes
    - Sanitización de errores
    """
    settings = get_settings()
    if not await _admitir(websocket, settings, session_id):
        return

    await connection_manager.connect(websocket, session_id)

    try:
        app_state = await _saludar(session_id)
        await _bucle(websocket, settings, session_id, app_state)

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {session_id}")
    except Exception as e:
        # Captura amplia a propósito: frontera del endpoint WS: informar al cliente (saneado) y
        # cerrar limpio.
        logger.exception(f"WebSocket error for {session_id}: {e}")
        # Sanitizar error antes de enviar al cliente
        await _enviar(session_id, WSMessageType.ERROR, {"error": sanitize_error_for_client(e)})
    finally:
        await connection_manager.disconnect(session_id, websocket)


async def _admitir(websocket: WebSocket, settings: Any, session_id: str) -> bool:
    """Autenticar y validar la sesión ANTES de aceptar el socket; False si se cerró."""
    # SEC-4 + F6: Origin y credenciales ANTES de aceptar el socket (4403 = no autenticado, para
    # distinguirlo de un error del servidor). El navegador trae un ticket de un solo uso de ESTA
    # sesión (POST /session/{id}/ws-ticket); un cliente de servicio, la API key.
    from geo_copilot.api.auth import autenticar_websocket
    from geo_copilot.platform.identidad.principal import fijar_principal
    from geo_copilot.platform.identidad.propiedad import SesionAjena
    from geo_copilot.platform.identidad.servicio import identidad_actual

    principal = await autenticar_websocket(websocket, settings, session_id)
    if principal is None or principal.rol is None:
        await websocket.close(code=4403, reason="Forbidden")
        return False
    # F6: la sesión de otro se rechaza igual que una inexistente (no se confirma que exista)
    if _SESSION_ID_RE.match(session_id or ""):
        try:
            await identidad_actual().propiedad.asegurar(principal, session_id)
        except SesionAjena:
            await websocket.close(code=WS_CLOSE_UNKNOWN_SESSION, reason="sesión inexistente")
            return False
    # Todo lo que corra en esta conexión (consultas, aprobaciones) es de este principal
    fijar_principal(principal)
    from geo_copilot.api.auth import cargar_conexiones_de

    await cargar_conexiones_de(principal)

    # S0.3 (#14, IDOR residual). Antes, cualquier session_id del path se
    # auto-creaba y `connect()` sustituía sin más la conexión viva de esa
    # sesión: conocer un id bastaba para robarle a otro cliente el canal de
    # progreso y de aprobaciones HITL. Ahora la sesión la crea SOLO el REST
    # (`POST /session/`, que el frontend llama antes de abrir el socket) y el
    # socket se rechaza —antes de aceptarlo— si el id es inválido, no existe
    # o ya tiene una conexión viva. Sin identidad de usuario (Fase 6 del plan)
    # esto no es ownership real, pero cierra la toma por id adivinado.
    reason = await _session_rejection(session_id)
    if reason is not None:
        code, text = reason
        logger.warning(f"WebSocket rechazado ({code}): {text}")
        await websocket.close(code=code, reason=text)
        return False
    return True


async def _saludar(session_id: str) -> Any:
    """El «connected» (con los turnos en curso) y las aprobaciones pendientes; el estado de la app."""
    # F7 (auditoría): con el «connected» van los turnos de la sesión que siguen en curso — el
    # cliente que esperaba el resultado de uno que terminó mientras estaba desconectado lo sabe
    # (y no espera para siempre). Sin el dato (Redis caído), no se manda y el cliente no concluye.
    from geo_copilot.platform.estado.aprobaciones import almacen

    conectado: dict[str, Any] = {"status": "connected", "session_id": session_id}
    try:
        conectado["turnos_en_curso"] = await almacen().turnos_en_curso(session_id)
    except Exception:  # se registra; el socket sirve igual
        logger.warning(f"[ws] no se pudieron leer los turnos en curso de {session_id}", exc_info=True)
    # Enviar mensaje de conexión exitosa
    await _enviar(session_id, WSMessageType.STATUS, conectado)

    # Obtener estado de la aplicación
    app_state = get_app_state()
    if not app_state.is_initialized:
        await app_state.initialize()

    # F7 (E7.1): las aprobaciones que la sesión tenía pendientes vuelven a mostrarse al
    # reconectar (tras recargar o tras un reinicio del servidor, cuando quedaron huérfanas)
    if app_state.hitl_manager is not None:
        for aviso in await app_state.hitl_manager.pendientes_de(session_id):
            await _enviar(session_id, WSMessageType.APPROVAL_REQUEST, aviso)
    return app_state


async def _bucle(websocket: WebSocket, settings: Any, session_id: str, app_state: Any) -> None:
    """Recibir, validar y despachar los mensajes del cliente (en orden) hasta que se desconecte."""
    en_orden = asyncio.Lock()
    while True:
        # Recibir mensaje del cliente con timeout para evitar DoS
        try:
            raw_data = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=settings.websocket_receive_timeout
            )
        except TimeoutError:
            # Enviar ping para verificar conexión activa
            await _enviar(session_id, WSMessageType.PONG, {"ping": "keep-alive"})
            continue

        # Validar tamaño del mensaje
        if len(raw_data) > settings.websocket_max_message_size:
            await _enviar(session_id, WSMessageType.ERROR,
                          {"error": "Mensaje demasiado grande"})
            continue

        try:
            data = json.loads(raw_data)

            # Validar estructura del mensaje
            try:
                validated_data = validate_ws_message(data)
                message_type = validated_data.get("type")
                message_data = validated_data.get("data", {})
            except (ValueError, ValidationError) as ve:
                logger.warning(f"Invalid WS message from {session_id}: {ve}")
                await _enviar(session_id, WSMessageType.ERROR, {"error": "Mensaje inválido"})
                continue

            await _despachar(session_id, message_type, message_data, app_state, en_orden)

        except json.JSONDecodeError:
            await _enviar(session_id, WSMessageType.ERROR, {"error": "JSON inválido"})


async def handle_message(
    session_id: str,
    message_type: str,
    message_data: dict,
    app_state: Any
) -> None:
    """Manejar mensajes entrantes."""

    if message_type == WSMessageType.PING.value:
        await _enviar(session_id, WSMessageType.PONG, {})

    elif message_type == WSMessageType.QUERY.value:
        await handle_query(session_id, message_data, app_state)

    elif message_type == WSMessageType.APPROVAL.value:
        await handle_approval(session_id, message_data, app_state)

    elif message_type == WSMessageType.CANCEL.value:
        await handle_cancel(session_id, message_data, app_state)

    else:
        await _enviar(session_id, WSMessageType.ERROR,
                      {"error": f"Unknown message type: {message_type}"})


async def handle_query(session_id: str, data: dict, app_state: Any) -> None:
    """Manejar consulta vía WebSocket."""
    from geo_copilot.api.routes.query import turno_observado

    # F7 (auditoría): span «turno», métrica y correlación también para el canal WebSocket
    async with turno_observado(session_id, "ws") as obs:
        obs["ok"] = await _procesar_query(session_id, data, app_state)


async def _procesar_query(session_id: str, data: dict, app_state: Any) -> bool:
    """La consulta del WebSocket; True si terminó bien. F4: los pasos viven en `ws_turno`."""
    from geo_copilot.api.ws_turno import procesar_query

    return await procesar_query(session_id, data, app_state)


async def handle_approval(session_id: str, data: dict, app_state: Any) -> None:  # noqa: PLR0912
    """Manejar aprobación vía WebSocket."""
    approval_id = data.get("approval_id")
    action = data.get("action")

    if not approval_id or not action:
        await _enviar(session_id, WSMessageType.ERROR,
                      {"error": "approval_id and action are required"})
        return

    if action not in ("approve", "reject", "modify"):
        # F7 (auditoría): antes de buscarla — una acción desconocida no es «not found» (la
        # aprobación existe y sigue pendiente), como en la ruta REST.
        await _enviar(session_id, WSMessageType.ERROR, {"error": f"Unknown action: {action}"})
        return

    try:
        hitl_manager = app_state.hitl_manager

        # SEC-3: enforce session ownership. La conexión WebSocket está ligada a
        # `session_id`; la aprobación debe pertenecer a esa misma sesión. Sin
        # este check cualquier cliente conectado podría aprobar/rechazar/modificar
        # el SQL/código pendiente de OTRA sesión (IDOR). Espejo del control REST
        # en api/routes/approval.py::submit_approval.
        # F7 (auditoría): fuera de la memoria del proceso, como la ruta REST — la puede estar
        # esperando otro worker, o nadie (huérfana tras un reinicio, re-enviada al reconectar).
        req = await hitl_manager.buscar(approval_id)
        if req is None:
            await connection_manager.send_message(
                session_id,
                WSMessage(
                    type=WSMessageType.ERROR,
                    data={"error": f"Approval {approval_id} not found or already processed"}
                )
            )
            return
        if req.session_id != session_id:
            await _enviar(session_id, WSMessageType.ERROR,
                          {"error": "Approval does not belong to this session"})
            return

        procesada: bool
        if action == "approve":
            procesada = await hitl_manager.approve(approval_id)
        elif action == "reject":
            procesada = await hitl_manager.reject(approval_id, data.get("reason", "Rejected by user"))
        else:
            modified = data.get("modified_content")
            if modified:
                procesada = await hitl_manager.modify(approval_id, modified)
            else:
                await connection_manager.send_message(
                    session_id,
                    WSMessage(
                        type=WSMessageType.ERROR,
                        data={"error": "modified_content is required for modify action"}
                    )
                )
                return
        if not procesada:  # otra respuesta llegó antes (otra pestaña, otra réplica), o expiró sin aplicarse
            await connection_manager.send_message(session_id, WSMessage(
                type=WSMessageType.ERROR,
                data={"error": f"Approval {approval_id} was already answered or expired before it could be applied"}))
            return

        # Notificar aprobación exitosa
        if action in ["approve", "modify"]:
            # La aprobación ha sido registrada en el HITLManager
            response = hitl_manager.get_response(approval_id)
            await connection_manager.send_message(
                session_id,
                WSMessage(
                    type=WSMessageType.STATUS,
                    data={
                        "status": "approved",
                        "approval_id": approval_id,
                        "action": action,
                        "modified_content": response.modified_content if response else None
                    }
                )
            )
        else:
            await _enviar(session_id, WSMessageType.STATUS,
                          {"status": "rejected", "approval_id": approval_id})

    except Exception as e:
        # Captura amplia a propósito: frontera de la aprobación WS: se devuelve error saneado al
        # cliente.
        logger.exception(f"Error handling approval via WebSocket: {e}")
        # Sanitizar error antes de enviar al cliente
        await _enviar(session_id, WSMessageType.ERROR, {"error": sanitize_error_for_client(e)})


async def handle_cancel(session_id: str, data: dict, app_state: Any) -> None:
    """Cancelar la query en vuelo de una sesión.

    Fase 6 #8 / API-13 — la versión anterior leía
    ``app_state.current_execution_state``, atributo que nunca existió, y
    solo emitía una notificación; la tarea seguía consumiendo CPU/LLM/BD
    hasta terminar por su cuenta. Ahora llamamos a
    ``connection_manager.cancel_task`` que invoca ``Task.cancel()`` sobre
    la tarea registrada al iniciar la query.

    El cliente recibe siempre una notificación, incluso si no había nada
    que cancelar (idempotente).
    """
    query_id = data.get("query_id")

    # F7 (auditoría): el estado dice lo que pasó de verdad; con varios procesos, «cancelled» lo
    # confirma el que tenía la tarea. Un fallo de Redis ya no cierra el WebSocket del usuario.
    estado = await connection_manager.detener(session_id)
    if estado != "cancelled":
        logger.info(f"[handle_cancel] session={session_id} query={query_id} — {estado}")

    mensajes = {
        "cancelled": "Query cancellation issued",
        "cancel_requested": "Cancellation requested to the worker running it; it confirms with 'cancelled'",
        "no_active_task": "No active query to cancel",
        "cancel_failed": "Could not request the cancellation (shared state unavailable)",
    }
    await _enviar(session_id, WSMessageType.STATUS,
                  {"status": estado, "query_id": query_id, "message": mensajes[estado]})






# =============================================================================
# Funciones de utilidad para autonomía (self-correction)
# =============================================================================
