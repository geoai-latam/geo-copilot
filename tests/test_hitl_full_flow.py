"""
Tests E2E del flujo HITL completo via REST — añadidos 2026-05-25 (F2.2).

`test_hitl_e2e.py` cubre el `HITLManager` aislado. Este archivo cubre el
flujo CON la API REST + el HITLManager de la app + el binding SEC-3
session_id → approval_id:

    1. Background task simula un agente que llama `request_approval`.
    2. Test verifica que la solicitud aparece en `GET /approval/pending?session_id=X`.
    3. Test hace `POST /approval/:id` con `action=approve, session_id=X`.
    4. Background task recibe `APPROVED` y termina.
    5. SEC-3: otra `session_id` Y NO puede aprobar la solicitud de la sesión X.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import app
from geo_copilot.api.dependencies import get_app_state
from geo_copilot.security.hitl import (
    HITLActionType,
    HITLStatus,
)


@pytest.fixture
def client():
    """TestClient con context manager — ejecuta lifespan (inicializa AppState)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def hitl_manager(client):
    """HITLManager real de la app, post-lifespan. Depende de `client`
    para garantizar que el lifespan ya inicializó AppState."""
    manager = get_app_state().hitl_manager
    assert manager is not None, (
        "hitl_manager debió ser inicializado por el lifespan de la app. "
        "Si está None, el lifespan no corrió — verifica que TestClient "
        "se usa con `with` y no como llamada directa."
    )
    return manager


# =============================================================================
# Helpers
# =============================================================================
async def _spawn_pending_approval(
    hitl_manager,
    session_id: str,
    title: str = "Test SQL",
    preview: str = "SELECT 1",
) -> tuple[asyncio.Task, str]:
    """Lanza request_approval en background, devuelve (task, approval_id).

    Espera que el manager registre el request en _pending_requests antes de
    devolver el id (para que el test pueda consultarlo vía REST).
    """
    task = asyncio.create_task(
        hitl_manager.request_approval(
            action_type=HITLActionType.SQL_EXECUTION,
            title=title,
            description="Test description",
            preview=preview,
            session_id=session_id,
        )
    )

    # Esperar a que el request esté registrado (max 500ms).
    for _ in range(50):
        await asyncio.sleep(0.01)
        pending = hitl_manager.get_pending_requests()
        for req in pending:
            if req.session_id == session_id and req.title == title:
                return task, req.id
    raise AssertionError(
        f"Request HITL no se registró en 500ms para session={session_id}"
    )


# =============================================================================
# Tests
# =============================================================================
@pytest.mark.asyncio
async def test_full_cycle_approve_via_rest(client, hitl_manager):
    """Ciclo completo: agente abre HITL → REST aprueba → agente recibe APPROVED."""
    session_id = "test-full-cycle-approve"

    task, approval_id = await _spawn_pending_approval(
        hitl_manager, session_id, title="Approve me",
        preview="SELECT * FROM parcelas",
    )

    # 1. La solicitud aparece en /pending de SU session.
    pending_resp = client.get(
        f"/api/v1/approval/pending?session_id={session_id}"
    )
    assert pending_resp.status_code == 200
    pending_list = pending_resp.json()
    assert any(p["approval_id"] == approval_id for p in pending_list), (
        f"approval {approval_id} no apareció en /pending"
    )

    # 2. Detalle del approval por ID también funciona (con el session_id dueño).
    detail_resp = client.get(f"/api/v1/approval/{approval_id}?session_id={session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["approval_id"] == approval_id
    assert detail["status"] == "pending"
    assert "SELECT" in detail["content"]

    # 2b. IDOR: un session_id ajeno NO puede leer el approval (404, no 403,
    # para no confirmar la existencia del id a un tercero).
    idor_resp = client.get(f"/api/v1/approval/{approval_id}?session_id=intruso")
    assert idor_resp.status_code == 404

    # 3. Aprobar vía REST.
    approve_resp = client.post(
        f"/api/v1/approval/{approval_id}",
        json={"action": "approve", "session_id": session_id},
    )
    assert approve_resp.status_code == 200, approve_resp.json()
    assert approve_resp.json()["success"] is True

    # 4. El task del agente recibe el APPROVED y termina.
    response = await asyncio.wait_for(task, timeout=2.0)
    assert response.status == HITLStatus.APPROVED


@pytest.mark.asyncio
async def test_full_cycle_reject_via_rest(client, hitl_manager):
    """Mismo ciclo pero rechazando."""
    session_id = "test-full-cycle-reject"

    task, approval_id = await _spawn_pending_approval(
        hitl_manager, session_id, title="Reject me",
    )

    reject_resp = client.post(
        f"/api/v1/approval/{approval_id}",
        json={
            "action": "reject",
            "session_id": session_id,
            "reason": "Demasiado riesgoso",
        },
    )
    assert reject_resp.status_code == 200

    response = await asyncio.wait_for(task, timeout=2.0)
    assert response.status == HITLStatus.REJECTED
    assert "riesgoso" in response.feedback.lower()


@pytest.mark.asyncio
async def test_full_cycle_modify_via_rest(client, hitl_manager):
    """Modify: el usuario cambia el contenido antes de aprobar."""
    session_id = "test-full-cycle-modify"

    task, approval_id = await _spawn_pending_approval(
        hitl_manager, session_id, title="Modify me",
        preview="SELECT * FROM parcelas",
    )

    modify_resp = client.post(
        f"/api/v1/approval/{approval_id}",
        json={
            "action": "modify",
            "session_id": session_id,
            "modified_content": "SELECT id FROM parcelas LIMIT 100",
        },
    )
    assert modify_resp.status_code == 200

    response = await asyncio.wait_for(task, timeout=2.0)
    assert response.status == HITLStatus.MODIFIED
    assert "LIMIT 100" in str(response.modified_content)


@pytest.mark.asyncio
async def test_sec3_other_session_cannot_approve(client, hitl_manager):
    """SEC-3: una solicitud HITL queda atada al session_id que la abrió.
    Otra session_id NO puede aprobarla aunque conozca el approval_id.

    Esta es la protección crítica contra approval hijacking. Si se rompe
    en algún refactor, un usuario podría aprobar acciones sensibles de
    otra sesión.
    """
    owner_session = "test-sec3-owner"
    attacker_session = "test-sec3-attacker"

    task, approval_id = await _spawn_pending_approval(
        hitl_manager, owner_session, title="SEC-3 protected",
    )

    # 1. La sesión atacante NO ve la solicitud en /pending.
    pending_resp = client.get(
        f"/api/v1/approval/pending?session_id={attacker_session}"
    )
    assert pending_resp.status_code == 200
    assert not any(
        p["approval_id"] == approval_id for p in pending_resp.json()
    ), "ATTACKER vio la solicitud de OTRA sesión — SEC-3 violado"

    # 2. POST de approval con la session equivocada → 403.
    attack_resp = client.post(
        f"/api/v1/approval/{approval_id}",
        json={"action": "approve", "session_id": attacker_session},
    )
    assert attack_resp.status_code == 403, (
        f"Atacante pudo aprobar solicitud de otra sesión — "
        f"status={attack_resp.status_code}"
    )

    # 3. El owner SÍ puede aprobar normalmente.
    legit_resp = client.post(
        f"/api/v1/approval/{approval_id}",
        json={"action": "approve", "session_id": owner_session},
    )
    assert legit_resp.status_code == 200
    response = await asyncio.wait_for(task, timeout=2.0)
    assert response.status == HITLStatus.APPROVED


@pytest.mark.asyncio
async def test_modify_requires_content_payload(client, hitl_manager):
    """`action=modify` sin `modified_content` → 400, no 500 ni crash."""
    session_id = "test-modify-no-content"

    task, approval_id = await _spawn_pending_approval(
        hitl_manager, session_id, title="Modify bad payload",
    )

    bad_resp = client.post(
        f"/api/v1/approval/{approval_id}",
        json={"action": "modify", "session_id": session_id},
    )
    assert bad_resp.status_code == 400
    assert "modified_content" in bad_resp.json().get("detail", "")

    # Limpieza: rechazar para que el task termine.
    client.post(
        f"/api/v1/approval/{approval_id}",
        json={"action": "reject", "session_id": session_id},
    )
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_approval_not_found_returns_404(client):
    """Aprobar un id que no existe → 404, no 500."""
    resp = client.post(
        "/api/v1/approval/this-id-does-not-exist",
        json={"action": "approve", "session_id": "anysession"},
    )
    assert resp.status_code == 404
