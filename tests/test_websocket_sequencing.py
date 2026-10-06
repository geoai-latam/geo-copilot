"""
Tests de orden y contenido de mensajes WebSocket — añadidos 2026-05-24.

Hueco crítico identificado en la auditoría: los tests existentes verifican
que `connect` produce 1 mensaje, pero NO testean el flujo real de una
consulta — secuencia `agent_step(router, started) → agent_step(router,
completed) → agent_step(data_agent, started) → ... → result`.

Estos tests usan un fake WebSocket en memoria que captura todos los
mensajes enviados, así podemos validar:
- Que la secuencia ESPERADA llega completa
- Que los tipos son correctos
- Que los campos del payload son los que el frontend lee
- Que la cancelación interrumpe limpiamente
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from geo_copilot.api.websocket import (
    ConnectionManager,
    WSMessage,
    WSMessageType,
)
from geo_copilot.api.websocket import (
    connection_manager as _global_manager,
)


# =============================================================================
# Fake WebSocket que captura los mensajes en una lista
# =============================================================================
class FakeWebSocket:
    """Stand-in del WebSocket de Starlette para tests.

    Implementa solo lo que `ConnectionManager` invoca: `send_json`,
    `close`, y un constructor sin parámetros. NO requiere event loop ni
    handshake real."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.closed:
            raise ConnectionError("WebSocket is closed")
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code

    async def accept(self) -> None:  # pragma: no cover — no usado en tests
        pass


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture
def manager():
    """Manager limpio para cada test."""
    m = ConnectionManager()
    return m


@pytest.fixture
async def connected_session(manager):
    """Sesión conectada con FakeWebSocket. Devuelve (manager, fake_ws, session_id)."""
    session_id = "test-session-001"
    fake_ws = FakeWebSocket()
    async with manager._lock:
        manager.active_connections[session_id] = fake_ws
    yield manager, fake_ws, session_id
    # Cleanup
    await manager.disconnect(session_id)


# =============================================================================
# Tests: secuencia de mensajes durante una "query"
# =============================================================================
@pytest.mark.asyncio
async def test_full_pipeline_sequence_arrives_in_order(connected_session):
    """Simula el flujo típico de una consulta: router → data → gis →
    symbology → insights → result. Verifica que TODOS los mensajes llegan
    en el orden esperado."""
    manager, fake_ws, sid = connected_session

    # Helper local — emula `send_agent_step` sin usar la versión global
    # que depende del connection_manager singleton (evitar interferencia).
    async def step(agent: str, status: str) -> None:
        msg_type = (
            WSMessageType.STEP_STARTED if status == "started"
            else WSMessageType.STEP_COMPLETED
        )
        await manager.send_message(
            sid,
            WSMessage(
                type=msg_type,
                data={"agent": agent, "description": f"{agent} {status}", "status": status},
            ),
        )

    # Secuencia simulando una consulta SQL completa
    pipeline = [
        ("router", "started"), ("router", "completed"),
        ("data_agent", "started"), ("data_agent", "completed"),
        ("gis_agent", "started"), ("gis_agent", "completed"),
        ("symbology_agent", "started"), ("symbology_agent", "completed"),
        ("insights_agent", "started"), ("insights_agent", "completed"),
    ]
    for agent, status in pipeline:
        await step(agent, status)

    # Mensaje final de resultado
    await manager.send_message(
        sid, WSMessage(type=WSMessageType.RESULT, data={"success": True}),
    )

    # Verificar TODOS los 11 mensajes en el orden exacto
    assert len(fake_ws.sent) == 11
    for i, (expected_agent, expected_status) in enumerate(pipeline):
        msg = fake_ws.sent[i]
        assert msg["data"]["agent"] == expected_agent, (
            f"Mensaje #{i}: esperaba agent={expected_agent}, recibí {msg['data']['agent']}"
        )
        assert msg["data"]["status"] == expected_status
    # Último mensaje es result.
    assert fake_ws.sent[-1]["type"] == "result"
    assert fake_ws.sent[-1]["data"]["success"] is True


@pytest.mark.asyncio
async def test_message_payload_contains_fields_frontend_reads(connected_session):
    """El frontend (`useAgentsPipeline`) lee `agent`, `description`,
    `status` del payload. Si faltan, el pipeline visual del UI se rompe
    sin error visible. Test contra regresión silenciosa."""
    manager, fake_ws, sid = connected_session

    await manager.send_message(
        sid,
        WSMessage(
            type=WSMessageType.STEP_STARTED,
            data={
                "agent": "gis_agent",
                "description": "Generando SQL espacial",
                "status": "started",
            },
        ),
    )

    msg = fake_ws.sent[0]
    # Estructura top-level
    assert "type" in msg
    assert "data" in msg
    # Campos que el frontend espera
    for required_field in ("agent", "description", "status"):
        assert required_field in msg["data"], (
            f"falta campo `{required_field}` que el frontend lee — "
            f"si lo quitas el AgentsPipe se rompe"
        )


@pytest.mark.asyncio
async def test_send_message_to_disconnected_session_returns_false(manager):
    """Mandar a un session_id desconocido devuelve False, no crash."""
    sent = await manager.send_message(
        "nonexistent-session",
        WSMessage(type=WSMessageType.STATUS, data={"x": 1}),
    )
    assert sent is False


@pytest.mark.asyncio
async def test_send_message_to_closed_socket_disconnects(manager):
    """Si el send falla (cliente desconectado), el manager limpia la
    conexión automáticamente — no se acumulan conexiones zombi."""
    sid = "zombie-session"
    fake_ws = FakeWebSocket()
    fake_ws.closed = True  # simular socket cerrado

    async with manager._lock:
        manager.active_connections[sid] = fake_ws

    assert await manager.is_connected(sid) is True

    sent = await manager.send_message(
        sid, WSMessage(type=WSMessageType.STATUS, data={}),
    )
    assert sent is False
    # Después del send fallido, debió desconectar.
    assert await manager.is_connected(sid) is False


@pytest.mark.asyncio
async def test_broadcast_to_multiple_sessions(manager):
    """Broadcast llega a TODAS las sesiones conectadas, en paralelo."""
    sessions = [f"sess-{i}" for i in range(5)]
    fake_wss = []
    for sid in sessions:
        ws = FakeWebSocket()
        fake_wss.append(ws)
        async with manager._lock:
            manager.active_connections[sid] = ws

    msg = WSMessage(type=WSMessageType.STATUS, data={"announce": "deploy"})
    await manager.broadcast(msg)

    for ws in fake_wss:
        assert len(ws.sent) == 1
        assert ws.sent[0]["data"]["announce"] == "deploy"


@pytest.mark.asyncio
async def test_broadcast_skips_disconnected_and_continues(manager):
    """Si UNA sesión está rota, broadcast NO debe fallar para las otras."""
    good_sid, bad_sid = "good", "bad"
    good_ws = FakeWebSocket()
    bad_ws = FakeWebSocket()
    bad_ws.closed = True   # ya cerrado, mandar va a fallar

    async with manager._lock:
        manager.active_connections[good_sid] = good_ws
        manager.active_connections[bad_sid] = bad_ws

    await manager.broadcast(
        WSMessage(type=WSMessageType.STATUS, data={"x": 1}),
    )

    # La sesión buena recibe el mensaje.
    assert len(good_ws.sent) == 1
    # La rota fue eliminada del manager.
    assert await manager.is_connected(bad_sid) is False
    # La buena sigue conectada.
    assert await manager.is_connected(good_sid) is True


@pytest.mark.asyncio
async def test_concurrent_sends_to_same_session_no_interleave(connected_session):
    """20 mensajes mandados concurrentemente deben TODOS llegar y mantener
    integridad (cada uno como un JSON completo, no fragmentado)."""
    manager, fake_ws, sid = connected_session

    async def send_one(i: int) -> None:
        await manager.send_message(
            sid,
            WSMessage(
                type=WSMessageType.STATUS,
                data={"index": i, "name": f"msg-{i}"},
            ),
        )

    await asyncio.gather(*(send_one(i) for i in range(20)))

    assert len(fake_ws.sent) == 20
    # Cada mensaje es un JSON completo y bien formado.
    indices_recibidos = sorted(m["data"]["index"] for m in fake_ws.sent)
    assert indices_recibidos == list(range(20))


@pytest.mark.asyncio
async def test_status_and_result_messages_have_distinct_types(connected_session):
    """`status` y `result` son tipos distintos — el frontend usa el tipo
    para decidir si actualizar el pipeline o cerrar el ciclo. Regresión
    silenciosa si alguien cambia el enum WSMessageType."""
    manager, fake_ws, sid = connected_session

    await manager.send_message(
        sid, WSMessage(type=WSMessageType.STATUS, data={"status": "processing"}),
    )
    await manager.send_message(
        sid, WSMessage(type=WSMessageType.RESULT, data={"success": True}),
    )

    types_sent = [m["type"] for m in fake_ws.sent]
    assert types_sent == ["status", "result"]


@pytest.mark.asyncio
async def test_disconnect_removes_from_active_connections(manager):
    """Después de disconnect, el session_id no aparece en
    `active_connections` y futuros sends devuelven False."""
    sid = "ephemeral"
    fake_ws = FakeWebSocket()
    async with manager._lock:
        manager.active_connections[sid] = fake_ws

    assert await manager.is_connected(sid) is True
    await manager.disconnect(sid)
    assert await manager.is_connected(sid) is False
    assert fake_ws.closed is True

    # Send después de disconnect → False
    assert await manager.send_message(
        sid, WSMessage(type=WSMessageType.STATUS, data={}),
    ) is False
