"""
SEC-3 vía WebSocket — añadidos 2026-06-19.

El control REST (`api/routes/approval.py::submit_approval`) ya ata cada
aprobación a la sesión que la originó. Pero el path WebSocket
(`api/websocket.py::handle_approval`) NO validaba ownership: cualquier cliente
conectado podía aprobar/rechazar/modificar el SQL/código pendiente de OTRA
sesión conociendo solo el `approval_id` (IDOR / approval hijacking).

Estos tests prueban que `handle_approval`:
- NO ejecuta la acción si la aprobación pertenece a otra sesión (responde ERROR).
- NO ejecuta la acción si el `approval_id` no existe (responde ERROR).
- SÍ ejecuta la acción cuando la sesión de la conexión es la dueña.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from geo_copilot.api.websocket import (
    connection_manager,
    handle_approval,
)


# =============================================================================
# Stubs
# =============================================================================
class FakeWebSocket:
    """Stand-in del WebSocket: captura los mensajes enviados."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class StubHITLManager:
    """HITLManager mínimo: registra si se invocó approve/reject/modify.

    `requests` mapea approval_id → session_id dueña."""

    def __init__(self, requests: dict[str, str]) -> None:
        self._requests = requests
        self.approved: list[str] = []
        self.rejected: list[str] = []
        self.modified: list[str] = []

    def get_request(self, approval_id: str):
        session_id = self._requests.get(approval_id)
        if session_id is None:
            return None
        return SimpleNamespace(id=approval_id, session_id=session_id)

    async def buscar(self, approval_id: str):
        """F7 (auditoría): handle_approval busca la solicitud también fuera del proceso."""
        return self.get_request(approval_id)

    async def approve(self, approval_id: str) -> bool:
        self.approved.append(approval_id)
        return True

    async def reject(self, approval_id: str, reason: str) -> bool:
        self.rejected.append(approval_id)
        return True

    async def modify(self, approval_id: str, content: str) -> bool:
        self.modified.append(approval_id)
        return True

    def get_response(self, approval_id: str):
        return None


@pytest.fixture
async def register_session():
    """Registra una sesión con FakeWebSocket en el connection_manager global
    y la limpia al final. Devuelve un helper para registrar varias."""
    registered: list[str] = []

    async def _register(session_id: str) -> FakeWebSocket:
        ws = FakeWebSocket()
        async with connection_manager._lock:
            connection_manager.active_connections[session_id] = ws
        registered.append(session_id)
        return ws

    yield _register

    for sid in registered:
        await connection_manager.disconnect(sid)


# =============================================================================
# Tests
# =============================================================================
@pytest.mark.asyncio
async def test_other_session_cannot_approve_via_ws(register_session):
    """SEC-3: una sesión NO dueña no puede aprobar el HITL de otra, aunque
    conozca el approval_id. La acción no se ejecuta y se responde ERROR."""
    owner, attacker = "owner-session", "attacker-session"
    hitl = StubHITLManager({"appr-1": owner})
    app_state = SimpleNamespace(hitl_manager=hitl)

    attacker_ws = await register_session(attacker)

    await handle_approval(
        attacker,
        {"approval_id": "appr-1", "action": "approve"},
        app_state,
    )

    # La aprobación NO se ejecutó (protección contra hijacking).
    assert hitl.approved == [], "ATACANTE aprobó el HITL de otra sesión — SEC-3 violado"
    # El atacante recibió un error explícito.
    assert attacker_ws.sent, "se esperaba un mensaje de error al atacante"
    last = attacker_ws.sent[-1]
    assert last["type"] == "error"
    assert "does not belong" in last["data"]["error"]


@pytest.mark.asyncio
async def test_other_session_cannot_reject_or_modify_via_ws(register_session):
    """SEC-3: reject y modify también quedan bloqueados para sesiones ajenas."""
    owner, attacker = "owner-2", "attacker-2"
    hitl = StubHITLManager({"appr-2": owner})
    app_state = SimpleNamespace(hitl_manager=hitl)
    await register_session(attacker)

    await handle_approval(
        attacker, {"approval_id": "appr-2", "action": "reject", "reason": "x"}, app_state
    )
    await handle_approval(
        attacker,
        {"approval_id": "appr-2", "action": "modify", "modified_content": "DROP TABLE x"},
        app_state,
    )

    assert hitl.rejected == [], "ATACANTE rechazó el HITL de otra sesión — SEC-3 violado"
    assert hitl.modified == [], "ATACANTE modificó el HITL de otra sesión — SEC-3 violado"


@pytest.mark.asyncio
async def test_unknown_approval_id_is_rejected(register_session):
    """Un approval_id inexistente no ejecuta acción y responde ERROR (no crash)."""
    hitl = StubHITLManager({})  # sin solicitudes
    app_state = SimpleNamespace(hitl_manager=hitl)
    ws = await register_session("sess-3")

    await handle_approval(
        "sess-3", {"approval_id": "ghost", "action": "approve"}, app_state
    )

    assert hitl.approved == []
    assert ws.sent[-1]["type"] == "error"
    assert "not found" in ws.sent[-1]["data"]["error"]


@pytest.mark.asyncio
async def test_owner_session_can_approve_via_ws(register_session):
    """Camino feliz: la sesión DUEÑA sí puede aprobar su propia solicitud."""
    owner = "owner-4"
    hitl = StubHITLManager({"appr-4": owner})
    app_state = SimpleNamespace(hitl_manager=hitl)
    ws = await register_session(owner)

    await handle_approval(
        owner, {"approval_id": "appr-4", "action": "approve"}, app_state
    )

    assert hitl.approved == ["appr-4"], "el dueño no pudo aprobar su propia solicitud"
    # Recibe un status de aprobación, no un error.
    assert ws.sent[-1]["type"] == "status"
    assert ws.sent[-1]["data"]["status"] == "approved"


@pytest.mark.asyncio
async def test_owner_session_can_modify_via_ws(register_session):
    """Camino feliz: la sesión dueña puede modificar su propia solicitud."""
    owner = "owner-5"
    hitl = StubHITLManager({"appr-5": owner})
    app_state = SimpleNamespace(hitl_manager=hitl)
    ws = await register_session(owner)

    await handle_approval(
        owner,
        {"approval_id": "appr-5", "action": "modify", "modified_content": "SELECT 1"},
        app_state,
    )

    assert hitl.modified == ["appr-5"]
    assert ws.sent[-1]["type"] == "status"
    assert ws.sent[-1]["data"]["status"] == "approved"
