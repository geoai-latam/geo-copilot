"""Lo que el núcleo le ENVÍA al cliente por el WebSocket: progreso, aprobaciones, reintentos,
planes, pasos de agentes, estado y resultado (y el EventSink que lo publica).

Salió de `websocket.py` (F4 del plan de calidad: 1.352 líneas), tal cual.
"""

from geo_copilot.api.ws_mensajes import WSMessage, WSMessageType
from geo_copilot.core.error_sanitizer import describe_error
from geo_copilot.core.logging import get_logger


def _ws():
    """La instancia `connection_manager` vive en `api.websocket` (las pruebas la sustituyen ahí):
    se resuelve al usarla."""
    from geo_copilot.api import websocket

    return websocket

logger = get_logger("geo_copilot.api.websocket")


# Funciones de utilidad para enviar mensajes desde otros módulos
async def send_progress_update(session_id: str, step: str, progress: float, details: dict | None = None):
    """Enviar actualización de progreso a un cliente."""
    if await _ws().connection_manager.is_connected(session_id):
        await _ws().connection_manager.send_message(
            session_id,
            WSMessage(
                type=WSMessageType.PROGRESS,
                data={"step": step, "progress": progress, "details": details or {}}
            )
        )


async def send_approval_request(session_id: str, approval_data: dict):
    """Enviar solicitud de aprobación a un cliente.

    F7 (auditoría): sin preguntar antes por la presencia — `send_message` ya decide entre el socket
    local y el bus; si nadie lo tiene ahora, se re-envía al reconectar (`pendientes_de`)."""
    await _ws().connection_manager.send_message(
        session_id,
        WSMessage(
            type=WSMessageType.APPROVAL_REQUEST,
            data=approval_data
        )
    )


async def send_retry_started(
    session_id: str,
    agent: str,
    attempt: int,
    max_attempts: int,
    error: str,
    action: str = "Corrigiendo...",
):
    """
    Notificar inicio de reintento.

    Args:
        session_id: ID de sesión
        agent: Nombre del agente (gis_agent, python_agent)
        attempt: Número de intento actual
        max_attempts: Máximo de intentos
        error: Error que provocó el reintento
        action: Descripción de la acción de corrección
    """
    logger.info(f"[WS] send_retry_started called: session={session_id}, agent={agent}, attempt={attempt}")
    is_connected = await _ws().connection_manager.is_connected(session_id)
    logger.info(f"[WS] Session {session_id} connected: {is_connected}")

    if is_connected:
        sent = await _ws().connection_manager.send_message(
            session_id,
            WSMessage(
                type=WSMessageType.RETRY_STARTED,
                data={
                    "agent": agent,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    # S0.4: categoría, nunca el error crudo del intento.
                    "error": describe_error(error, context=f"retry.{agent}"),
                    "action": action,
                }
            )
        )
        logger.info(f"[WS] RETRY_STARTED sent: {sent}")
    else:
        logger.warning(f"[WS] Cannot send RETRY_STARTED - session {session_id} not connected")


async def send_retry_correction(
    session_id: str,
    agent: str,
    correction_type: str,
    original: str | None = None,
    corrected: str | None = None,
):
    """
    Notificar corrección aplicada.

    Args:
        session_id: ID de sesión
        agent: Nombre del agente
        correction_type: Tipo de corrección (sql, code)
        original: Contenido original (opcional, para debugging)
        corrected: Contenido corregido (opcional, para debugging)
    """
    if await _ws().connection_manager.is_connected(session_id):
        await _ws().connection_manager.send_message(
            session_id,
            WSMessage(
                type=WSMessageType.RETRY_CORRECTION,
                data={
                    "agent": agent,
                    "correction_type": correction_type,
                    "original_preview": original[:100] if original else None,
                    "corrected_preview": corrected[:100] if corrected else None,
                }
            )
        )


async def send_retry_result(
    session_id: str,
    agent: str,
    success: bool,
    attempts: int,
    message: str | None = None,
):
    """
    Notificar resultado de reintentos.

    Args:
        session_id: ID de sesión
        agent: Nombre del agente
        success: Si el reintento fue exitoso
        attempts: Número total de intentos
        message: Mensaje descriptivo
    """
    logger.info(f"[WS] send_retry_result called: session={session_id}, agent={agent}, success={success}, attempts={attempts}")
    is_connected = await _ws().connection_manager.is_connected(session_id)

    if is_connected:
        msg_type = WSMessageType.RETRY_SUCCESS if success else WSMessageType.RETRY_FAILED
        sent = await _ws().connection_manager.send_message(
            session_id,
            WSMessage(
                type=msg_type,
                data={
                    "agent": agent,
                    "success": success,
                    "attempts": attempts,
                    "message": message,
                }
            )
        )
        logger.info(f"[WS] {msg_type.value} sent: {sent}")
    else:
        logger.warning(f"[WS] Cannot send retry_result - session {session_id} not connected")


async def send_plan_created(
    session_id: str,
    plan: list[dict],
    reasoning: str | None = None,
):
    """
    Notificar plan multi-paso creado (Fase 2).

    Args:
        session_id: ID de sesión
        plan: Lista de pasos del plan
        reasoning: Razonamiento del plan
    """
    if await _ws().connection_manager.is_connected(session_id):
        await _ws().connection_manager.send_message(
            session_id,
            WSMessage(
                type=WSMessageType.PLAN_CREATED,
                data={
                    "plan": plan,
                    "total_steps": len(plan),
                    "reasoning": reasoning,
                }
            )
        )


async def send_step_progress(
    session_id: str,
    step_index: int,
    total_steps: int,
    action: str,
    status: str,  # "started", "completed", "failed"
    result: dict | None = None,
):
    """
    Notificar progreso de paso en plan multi-paso (Fase 2).

    Args:
        session_id: ID de sesión
        step_index: Índice del paso (0-based)
        total_steps: Total de pasos
        action: Descripción de la acción
        status: Estado del paso
        result: Resultado del paso (si completado)
    """
    if await _ws().connection_manager.is_connected(session_id):
        msg_type = WSMessageType.STEP_STARTED if status == "started" else WSMessageType.STEP_COMPLETED
        await _ws().connection_manager.send_message(
            session_id,
            WSMessage(
                type=msg_type,
                data={
                    "step_index": step_index,
                    "total_steps": total_steps,
                    "action": action,
                    "status": status,
                    "result": result,
                }
            )
        )


async def send_agent_step(
    session_id: str,
    agent: str,
    description: str,
    status: str,  # "started", "completed", "failed"
) -> None:
    """
    Emitir un evento por paso de agente individual del grafo
    (data_agent / gis_agent / symbology_agent / insights_agent).

    A diferencia de ``send_step_progress`` (que es por paso de PLAN
    multi-paso del Planner), esta helper se invoca desde cada nodo
    del grafo principal en CADA consulta — también las simples — para
    que el chip de pipeline del frontend se actualice en tiempo real.

    El payload usa los campos ``agent`` y ``description`` porque es lo
    que ``useAgentsPipeline`` clasifica en el frontend.
    """
    if not session_id:
        logger.debug(f"[WS pipe] send_agent_step skipped: no session_id ({agent}/{status})")
        return
    if not await _ws().connection_manager.is_connected(session_id):
        logger.debug(f"[WS pipe] send_agent_step skipped: WS not connected for {session_id} ({agent}/{status})")
        return
    msg_type = WSMessageType.STEP_STARTED if status == "started" else WSMessageType.STEP_COMPLETED
    sent = await _ws().connection_manager.send_message(
        session_id,
        WSMessage(
            type=msg_type,
            data={
                "agent": agent,
                "description": description,
                "status": status,
            },
        ),
    )
    logger.debug(f"[WS pipe] {agent} {status} sent={sent} session={session_id[:8]}")


async def send_status(session_id: str, status: str, details: dict | None = None) -> None:
    """Emitir status genérico (processing / completed / cancelled / etc)."""
    if not session_id:
        return
    if not await _ws().connection_manager.is_connected(session_id):
        return
    await _ws().connection_manager.send_message(
        session_id,
        WSMessage(
            type=WSMessageType.STATUS,
            data={"status": status, **(details or {})},
        ),
    )


async def send_result(session_id: str, result: dict | None = None) -> None:
    """Notificar que la consulta terminó (cierra el pipeline en el cliente)."""
    if not session_id:
        return
    if not await _ws().connection_manager.is_connected(session_id):
        return
    await _ws().connection_manager.send_message(
        session_id,
        WSMessage(
            type=WSMessageType.RESULT,
            data=result or {},
        ),
    )


async def send_execution_cancelled(
    session_id: str,
    partial_results: list[dict] | None = None,
    completed_steps: int = 0,
    total_steps: int = 0,
):
    """
    Notificar cancelación con resultados parciales.

    Args:
        session_id: ID de sesión
        partial_results: Resultados parciales obtenidos
        completed_steps: Pasos completados antes de cancelar
        total_steps: Total de pasos planeados
    """
    if await _ws().connection_manager.is_connected(session_id):
        await _ws().connection_manager.send_message(
            session_id,
            WSMessage(
                type=WSMessageType.EXECUTION_CANCELLED,
                data={
                    "partial_results": partial_results,
                    "completed_steps": completed_steps,
                    "total_steps": total_steps,
                    "message": f"Ejecución cancelada. {completed_steps}/{total_steps} pasos completados.",
                }
            )
        )


async def send_traza(session_id: str, evento: dict) -> None:
    """Un paso del turno para la trazabilidad del chat (orchestrator/traza.py)."""
    if session_id and await _ws().connection_manager.is_connected(session_id):
        await _ws().connection_manager.send_message(session_id, WSMessage(type=WSMessageType.TRACE, data=evento))


class WebSocketEventSink:
    """`platform.events.EventSink` sobre WebSocket (S1.2).

    El núcleo emite a la interfaz; esta clase lo reenvía con las funciones de
    arriba, que ya conocen el formato de mensaje que entiende el frontend. La
    instala `api/app.py` al arrancar.
    """

    async def agent_step(self, session_id: str, agent: str, description: str,
                         status: str) -> None:
        await send_agent_step(session_id, agent, description, status)

    async def retry_started(self, session_id: str, agent: str, attempt: int,
                            max_attempts: int, error: str,
                            action: str = "Corrigiendo...") -> None:
        await send_retry_started(session_id, agent, attempt, max_attempts, error, action)

    async def retry_correction(self, session_id: str, agent: str, correction_type: str,
                               original: str | None = None,
                               corrected: str | None = None) -> None:
        await send_retry_correction(session_id, agent, correction_type, original, corrected)

    async def retry_result(self, session_id: str, agent: str, success: bool,
                           attempts: int, message: str | None = None) -> None:
        await send_retry_result(session_id, agent, success, attempts, message)

    async def plan_created(self, session_id: str, plan: list[dict],
                           reasoning: str | None = None) -> None:
        await send_plan_created(session_id, plan, reasoning)

    async def step_progress(self, session_id: str, step_index: int, total_steps: int,
                            action: str, status: str,
                            result: dict | None = None) -> None:
        await send_step_progress(session_id, step_index, total_steps, action, status, result)

    async def traza(self, session_id: str, evento: dict) -> None:
        await send_traza(session_id, evento)
