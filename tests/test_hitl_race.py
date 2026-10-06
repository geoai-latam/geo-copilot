"""Regresión S8: race y límites en HITLManager.

1. El ``finally`` de ``request_approval`` popeaba ``_responses`` al
   despertar, antes de que el handler REST/WS leyera ``get_response`` →
   ``modified_content`` se perdía (se reportaba null).
2. ``_MAX_RESPONSES`` estaba declarado pero nunca se aplicaba.
3. ``created_at`` usaba ``datetime.utcnow`` (naive, deprecado) mientras
   ``approved_at`` era tz-aware → mezcla naive/aware.
"""

import asyncio

import pytest

from geo_copilot.security.hitl import (
    HITLActionType,
    HITLManager,
    HITLRequest,
    HITLStatus,
)


@pytest.mark.asyncio
async def test_modified_content_survives_for_get_response():
    mgr = HITLManager(timeout=5)

    async def _requester():
        return await mgr.request_approval(
            action_type=HITLActionType.SQL_EXECUTION,
            title="t",
            description="d",
        )

    task = asyncio.create_task(_requester())
    # Esperar a que la solicitud quede registrada.
    for _ in range(50):
        if mgr.get_pending_requests():
            break
        await asyncio.sleep(0.01)
    req_id = mgr.get_pending_requests()[0].id

    assert await mgr.modify(req_id, modified_content="SELECT 1 LIMIT 10") is True

    response = await task
    assert response.status == HITLStatus.MODIFIED
    assert response.modified_content == "SELECT 1 LIMIT 10"

    # El handler REST/WS lee get_response DESPUÉS de que el requester
    # terminó: el contenido modificado debe seguir disponible.
    later = mgr.get_response(req_id)
    assert later is not None
    assert later.modified_content == "SELECT 1 LIMIT 10"


@pytest.mark.asyncio
async def test_responses_dict_is_capped():
    mgr = HITLManager(timeout=5)
    mgr._MAX_RESPONSES = 5  # type: ignore[attr-defined]

    # Sembrar pending requests y responder a cada uno (sin bloquear).
    for i in range(20):
        req = HITLRequest(
            action_type=HITLActionType.SQL_EXECUTION,
            title=f"t{i}",
            description="d",
        )
        mgr._pending_requests[req.id] = req
        await mgr.respond(req.id, status=HITLStatus.APPROVED)

    assert len(mgr._responses) <= 5


def test_created_at_is_tz_aware():
    req = HITLRequest(
        action_type=HITLActionType.SQL_EXECUTION,
        title="t",
        description="d",
    )
    assert req.created_at.tzinfo is not None
