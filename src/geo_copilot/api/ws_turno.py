"""Un TURNO por el WebSocket: validar la consulta, lanzar el grafo como tarea cancelable con su plazo,
esperarla y entregar el resultado (o la solicitud de aprobación).

Salió de `websocket._procesar_query` (F4 del plan de calidad: 170 líneas), paso por paso. Lo que las
pruebas sustituyen en `api.websocket` (`connection_manager`, `get_settings`) se resuelve allí al usarlo.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from geo_copilot.api.ws_mensajes import WSMessageType, sanitize_error_for_client
from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.api.websocket")


def _ws():
    """`api.websocket` importa este módulo; su gestor de conexiones y su configuración se resuelven al usarlos."""
    from geo_copilot.api import websocket

    return websocket


async def _entrada_valida(session_id: str, data: dict) -> str | None:
    """La consulta, o None si no se puede procesar (y el cliente ya recibió el porqué)."""
    query = data.get("query")
    if not query:
        await _ws()._enviar(session_id, WSMessageType.ERROR, {"error": "Query is required"})
        return None
    from geo_copilot.platform import auditoria

    await auditoria.registrar("consulta", "agente", "recibida", session_id=session_id,
                              detalle={"consulta": query, "canal": "ws"})

    # SEC (paridad con el REST POST /query): validar el GeoJSON externo del
    # cliente ANTES de inyectarlo al grafo. El WS lo pasaba CRUDO, evadiendo el
    # tope de features (protección DoS de memoria) que sí aplica el REST
    # (routes/query.py). Import local para evitar cualquier ciclo de imports.
    external_geojson = data.get("external_geojson")
    if external_geojson is not None:
        from geo_copilot.api.routes.query import validate_geojson
        ok, err = validate_geojson(external_geojson)
        if not ok:
            await _ws()._enviar(session_id, WSMessageType.ERROR,
                                {"error": f"GeoJSON externo inválido: {err}"})
            return None
    return str(query)


def _lanzar(agent_graph: Any, query: str, session_id: str, data: dict, limite: float) -> asyncio.Task[Any]:
    """El grafo como tarea, con el plazo del turno y sus aprobaciones ligadas a ESTE turno."""
    from geo_copilot.core.llm_client import plazo_turno
    from geo_copilot.platform.estado.aprobaciones import fijar_turno, soltar_turno

    # las aprobaciones de esta consulta quedan ligadas a SU turno (sin registro para
    # reanudar: una huérfana de aquí se responde «vuelve a enviarla», sin pre-aprobar nada)
    token_turno = fijar_turno(uuid4().hex)
    try:
        with plazo_turno(limite):
            return asyncio.create_task(
                agent_graph.process(
                    query=query,
                    session_id=session_id,
                    conversation_history=data.get("conversation_history"),
                    previous_sql=data.get("previous_sql"),
                    previous_results=data.get("previous_results"),
                    found_services=data.get("found_services"),
                    external_geojson=data.get("external_geojson"),
                    external_source_name=data.get("external_source_name"),
                    has_external_data=data.get("external_geojson") is not None,
                )
            )
    finally:
        soltar_turno(token_turno)


async def _esperar(session_id: str, process_task: asyncio.Task[Any], limite: float) -> dict | None:
    """El resultado, o None si se canceló o pasó el plazo (y el cliente ya lo sabe)."""
    cm = _ws().connection_manager
    await cm.register_task(session_id, process_task)
    try:
        resultado: dict = await asyncio.wait_for(process_task, timeout=limite)
        return resultado
    except asyncio.CancelledError:
        # handle_cancel disparó .cancel() sobre la task — notificamos
        # al cliente y salimos sin propagar (es flujo esperado, no error).
        logger.info(f"[WS] Query cancelled by user: session={session_id}")
        await _ws()._enviar(session_id, WSMessageType.STATUS,
                            {"status": "cancelled", "message": "Consulta cancelada"})
        return None
    except TimeoutError:
        logger.error(f"[WS] Graph timed out after {limite}s")
        process_task.cancel()
        await _ws()._enviar(session_id, WSMessageType.ERROR,
                            {"error": "La consulta excedió el tiempo máximo de procesamiento"})
        return None
    finally:
        # Sea cual sea el camino (éxito, timeout, cancel, exception),
        # desregistramos para que un cancel posterior no apunte a
        # una task ya completada.
        await cm.clear_task(session_id, process_task)


async def _entregar(session_id: str, result: dict) -> bool:
    """El resultado al cliente, o la solicitud de aprobación si el turno quedó esperándola."""
    if result.get("requires_approval"):
        await _ws()._enviar(session_id, WSMessageType.APPROVAL_REQUEST, {
            "approval_id": result.get("pending_approval_id"),
            "content_type": result.get("content_type"),
            "content": result.get("content_for_approval"),
            "warnings": result.get("warnings", []),
            "risk_level": result.get("risk_level"),
        })
    else:
        await _ws()._enviar(session_id, WSMessageType.RESULT, result)
    # una pausa para aprobar no es un fallo del turno (F7, auditoría: la métrica lo contaba así)
    return bool(result.get("success")) or bool(result.get("requires_approval"))


async def procesar_query(session_id: str, data: dict, app_state: Any) -> bool:
    """La consulta del WebSocket; True si terminó bien."""
    query = await _entrada_valida(session_id, data)
    if query is None:
        return False

    # Notificar inicio del procesamiento
    await _ws()._enviar(session_id, WSMessageType.STATUS, {"status": "processing", "query": query})

    try:
        # - SEC-9: timeout duro (LLM colgado no bloquea el socket).
        # - Fase 6 #8 / API-13: una task explícita registrada en connection_manager para que
        #   handle_cancel pueda cancelarla de verdad.
        agent_graph = app_state.agent_graph
        if not agent_graph:
            raise ValueError("Agent graph not initialized")

        from geo_copilot.api.routes.query import compute_process_timeout

        # Mismo presupuesto que REST (cómputo + espera humana): antes el WS —el camino de la UI—
        # cortaba a los 120 s una aprobación HITL que tiene hasta 300 s para responderse.
        # F7 (auditoría): se calcula ANTES de crear la tarea, que hereda su plazo (`plazo_turno`):
        # ante un 429 sostenido el usuario lee el mensaje de saturación, no el del timeout.
        limite = compute_process_timeout(_ws().get_settings())
        result = await _esperar(session_id, _lanzar(agent_graph, query, session_id, data, limite), limite)
        if result is None:
            return False
        return await _entregar(session_id, result)

    except Exception as e:
        # Captura amplia a propósito: frontera de la query WS: el grafo puede lanzar cualquier cosa;
        # se devuelve error saneado.
        logger.exception(f"Error processing query via WebSocket: {e}")
        await _ws()._enviar(session_id, WSMessageType.ERROR, {"error": sanitize_error_for_client(e)})
        return False
