"""
Tests E2E del flujo HITL — añadidos 2026-05-24 tras auditoría.

Los tests existentes en `test_data_agent.py::TestHITLManager` solo cubren
approve/reject/timeout en aislamiento. Estos verifican el ciclo completo
incluyendo:

- Notification callback (simula la conexión Frontend ↔ Backend WebSocket)
- Modify status (no solo approve/reject)
- Concurrencia (varias solicitudes en paralelo no se mezclan)
- Cleanup de pending requests después de respond
- Aprobaciones tardías (después de timeout — verifica que no contaminan
  la siguiente sesión)
"""

from __future__ import annotations

import asyncio

import pytest

from geo_copilot.security.hitl import (
    HITLActionType,
    HITLManager,
    HITLStatus,
)


@pytest.mark.asyncio
async def test_notification_callback_receives_request_payload():
    """Cuando el agente llama `request_approval`, el callback registrado
    debe recibir el `session_id` correcto y un payload con los detalles
    de la solicitud. Esto simula el flujo:

        agente → HITLManager.request_approval(...)
              → notification_callback(session_id, payload)
              → WebSocket.send(session_id, payload)  [en producción]
    """
    captured: dict[str, object] = {}

    async def fake_ws_callback(session_id: str, payload: dict) -> None:
        captured["session_id"] = session_id
        captured["payload"] = payload

    manager = HITLManager(timeout=1)
    manager.set_notification_callback(fake_ws_callback)

    # Responder rápido para no bloquear el test (tarea aparte).
    async def respond():
        await asyncio.sleep(0.05)
        pending = manager.get_pending_requests()
        if pending:
            await manager.approve(pending[0].id, approved_by="tester")

    asyncio.create_task(respond())

    response = await manager.request_approval(
        action_type=HITLActionType.SQL_EXECUTION,
        title="Ejecutar SELECT",
        description="Consulta parcelas",
        details={"sql": "SELECT * FROM parcelas LIMIT 10"},
        risks=["Lectura amplia"],
        session_id="session-abc-123",
    )

    assert response.status == HITLStatus.APPROVED
    # El callback recibió la session_id y el payload con info de la solicitud.
    assert captured.get("session_id") == "session-abc-123"
    payload = captured.get("payload") or {}
    assert isinstance(payload, dict)
    # El payload debe contener un id (approval_id es el shape actual del WS),
    # action_type y el contenido preview.
    assert payload.get("approval_id"), "payload sin approval_id"
    assert payload.get("action_type") == "sql_execution"
    # El SQL de detalles llega como `content` para que el frontend lo muestre.
    assert "SELECT" in str(payload.get("content", ""))


@pytest.mark.asyncio
async def test_modify_status_round_trip():
    """El status MODIFIED permite al usuario aprobar pero con contenido
    modificado (ej. ajustó el SQL antes de ejecutar). El response debe
    propagar `modified_content`."""
    manager = HITLManager(timeout=1)

    async def respond():
        await asyncio.sleep(0.05)
        pending = manager.get_pending_requests()
        if pending:
            await manager.modify(
                pending[0].id,
                modified_content="SELECT id FROM parcelas LIMIT 5",
                feedback="Reduje el LIMIT",
            )

    asyncio.create_task(respond())

    response = await manager.request_approval(
        action_type=HITLActionType.SQL_EXECUTION,
        title="Ejecutar SQL",
        description="...",
    )

    assert response.status == HITLStatus.MODIFIED
    assert response.modified_content == "SELECT id FROM parcelas LIMIT 5"
    assert "LIMIT" in response.feedback


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_cross_contaminate():
    """Dos solicitudes en paralelo deben resolverse independientemente.
    Antes (race condition potencial): si las respuestas se mezclaran, una
    podría llevarse el resultado de la otra."""
    manager = HITLManager(timeout=2)

    async def submit(session: str, action_title: str) -> tuple[str, str]:
        async def respond_specific():
            # Esperar un poco y responder con feedback identificable.
            await asyncio.sleep(0.05)
            pending = manager.get_pending_requests()
            for p in pending:
                if p.title == action_title:
                    await manager.approve(p.id, approved_by=f"user-{session}")
                    return

        asyncio.create_task(respond_specific())
        resp = await manager.request_approval(
            action_type=HITLActionType.EXTERNAL_API,
            title=action_title,
            description="X",
            session_id=session,
        )
        return action_title, resp.approved_by or ""

    # Lanzamos 3 en paralelo con titles únicos.
    results = await asyncio.gather(
        submit("s1", "Request A"),
        submit("s2", "Request B"),
        submit("s3", "Request C"),
    )

    # Cada uno aprobado por su user correspondiente.
    by_title = dict(results)
    assert by_title["Request A"] == "user-s1"
    assert by_title["Request B"] == "user-s2"
    assert by_title["Request C"] == "user-s3"


@pytest.mark.asyncio
async def test_pending_request_cleaned_up_after_response():
    """Después de responder, la solicitud NO debe seguir en pending.
    Si quedara, la lista crecería sin límite y leaks de memoria."""
    manager = HITLManager(timeout=1)

    async def respond():
        await asyncio.sleep(0.05)
        pending = manager.get_pending_requests()
        if pending:
            await manager.approve(pending[0].id)

    asyncio.create_task(respond())

    await manager.request_approval(
        action_type=HITLActionType.DATA_IMPORT,
        title="One",
        description="...",
    )

    # Justo después de que request_approval retorne, pending debe estar vacío.
    assert manager.get_pending_requests() == [], (
        "request_approval debe limpiar la entrada en finally"
    )


@pytest.mark.asyncio
async def test_late_response_does_not_affect_next_request():
    """Escenario adversarial: una respuesta llega DESPUÉS de que la
    solicitud expiró (timeout). La siguiente solicitud no debe recibir
    esa respuesta vieja por accidente."""
    manager = HITLManager(timeout=1)

    # Primera solicitud — timeout sin responder.
    resp1 = await manager.request_approval(
        action_type=HITLActionType.CODE_EXECUTION,
        title="Will timeout",
        description="...",
    )
    assert resp1.status == HITLStatus.EXPIRED

    # Intentar responder tardíamente a la primera (id ya no existe).
    # Esto NO debe lanzar excepción ni contaminar el manager.
    # Como no hay forma directa de obtener el id de la solicitud anterior
    # (se limpió), simulamos pasando un id random.
    result = await manager.respond(
        request_id="ghost-id-that-does-not-exist",
        status=HITLStatus.APPROVED,
    )
    assert result is False, "responder a una solicitud inexistente debe devolver False"

    # Segunda solicitud — distinto título, debe seguir su propio ciclo.
    async def respond_second():
        await asyncio.sleep(0.05)
        pending = manager.get_pending_requests()
        if pending:
            await manager.approve(pending[0].id, approved_by="legit")

    asyncio.create_task(respond_second())

    resp2 = await manager.request_approval(
        action_type=HITLActionType.CODE_EXECUTION,
        title="Should succeed",
        description="...",
    )
    assert resp2.status == HITLStatus.APPROVED
    assert resp2.approved_by == "legit"


@pytest.mark.asyncio
async def test_callback_failure_does_not_block_request():
    """Si el WebSocket no está conectado y el callback falla, la solicitud
    aún debe poder ser aprobada por otra vía (ej. REST /approval/:id).
    El callback es notificación, NO el medio único de aprobación."""
    manager = HITLManager(timeout=1)

    # Callback que SIEMPRE falla simulando un WebSocket caído.
    async def broken_callback(session_id: str, payload: dict) -> None:
        raise ConnectionError("WebSocket disconnected")

    manager.set_notification_callback(broken_callback)

    async def respond_via_other_channel():
        await asyncio.sleep(0.05)
        pending = manager.get_pending_requests()
        if pending:
            await manager.approve(pending[0].id, approved_by="rest_user")

    asyncio.create_task(respond_via_other_channel())

    response = await manager.request_approval(
        action_type=HITLActionType.EXTERNAL_API,
        title="Test",
        description="WS roto pero REST funciona",
        session_id="any-session",
    )

    # La solicitud SE aprueba a pesar de que el callback falló.
    assert response.status == HITLStatus.APPROVED
    assert response.approved_by == "rest_user"


@pytest.mark.asyncio
async def test_respond_with_invalid_status_returns_false():
    """`respond()` con un id que no existe debe devolver False, no levantar."""
    manager = HITLManager(timeout=1)
    result = await manager.respond(
        request_id="non-existent-id",
        status=HITLStatus.APPROVED,
    )
    assert result is False


@pytest.mark.asyncio
async def test_get_request_and_response_lifecycle():
    """`get_request(id)` devuelve la solicitud mientras pending, y `None`
    después de resolver. `get_response(id)` puede devolver el response
    mientras está pending de cleanup."""
    manager = HITLManager(timeout=1)

    captured_id: dict[str, str] = {}

    async def respond():
        await asyncio.sleep(0.05)
        pending = manager.get_pending_requests()
        if pending:
            captured_id["id"] = pending[0].id
            # Mientras pending, get_request devuelve la solicitud.
            req = manager.get_request(pending[0].id)
            assert req is not None
            assert req.title == "lifecycle"
            await manager.approve(pending[0].id)

    asyncio.create_task(respond())

    await manager.request_approval(
        action_type=HITLActionType.DATA_IMPORT,
        title="lifecycle",
        description="...",
    )

    # Después del finally cleanup, get_request devuelve None.
    rid = captured_id.get("id")
    assert rid is not None
    assert manager.get_request(rid) is None, "cleanup debió eliminar la solicitud"
