"""
Endpoints para aprobación HITL (Human-in-the-Loop).

Includes rate limiting to prevent abuse of approval endpoints.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from geo_copilot.api.dependencies import get_hitl_manager
from geo_copilot.api.limiter import limiter
from geo_copilot.api.models import (
    ApprovalAction,
    ApprovalRequest,
    ApprovalResultResponse,
    ApprovalStatusResponse,
    ErrorResponse,
)
from geo_copilot.api.websocket import sanitize_error_for_client
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger
from geo_copilot.security.hitl import HITLManager

logger = get_logger(__name__)

from geo_copilot.api.auth import asegurar_sesion_actual, require_principal

router = APIRouter(
    prefix="/approval",
    tags=["Approval"],
    dependencies=[Depends(require_principal)],
)


def _get_rate_limit() -> str:
    """Get rate limit string from settings."""
    settings = get_settings()
    return f"{settings.rate_limit_requests}/minute"


@router.get(
    "/pending",
    response_model=list[ApprovalStatusResponse],
    summary="List pending approvals",
    description="Get pending approval requests for a specific session.",
)
@limiter.limit(_get_rate_limit)
async def list_pending_approvals(
    request: Request,
    session_id: str,
    hitl_manager: HITLManager = Depends(get_hitl_manager),
) -> list[ApprovalStatusResponse]:
    """Listar aprobaciones pendientes de la sesión dada.

    SEC-3: el parámetro ``session_id`` es obligatorio. Sin él, este endpoint
    filtraba a nada (devolvía todos los approval_id del sistema, permitiendo
    a cualquier caller aprobar el HITL de otro usuario).
    """
    await asegurar_sesion_actual(session_id)  # F6: las aprobaciones de la sesión de otro, 404
    # F7: las de esta sesión estén en este proceso, en otro o huérfanas tras un reinicio
    avisos = await hitl_manager.pendientes_de(session_id)

    return [
        ApprovalStatusResponse(
            approval_id=a["approval_id"],
            query_id="unknown",
            content_type=a.get("action_type") or "unknown",
            content=a.get("preview") or a.get("content") or "",
            status="pending",
            risk_level=a.get("risk_level"),
            warnings=a.get("warnings") or [],
            created_at=datetime.fromisoformat(str(a["created_at"])) if a.get("created_at") else datetime.now(UTC),
        )
        for a in avisos
    ]


@router.get(
    "/{approval_id}",
    response_model=ApprovalStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Approval not found"},
    },
    summary="Get approval details",
    description="Get details of a specific approval request.",
)
@limiter.limit(_get_rate_limit)
async def get_approval(
    request: Request,
    approval_id: str,
    session_id: str = Query(..., description="Sesión dueña de la aprobación"),
    hitl_manager: HITLManager = Depends(get_hitl_manager),
) -> ApprovalStatusResponse:
    """Obtener detalles de una aprobación."""
    await asegurar_sesion_actual(session_id)
    req = await hitl_manager.buscar(approval_id)

    # SEC (IDOR de lectura): el GET exponía el preview (SQL/código pendiente) sin
    # verificar ownership de sesión, a diferencia del POST (SEC-3, submit_approval).
    # Se exige el session_id dueño y se devuelve 404 —no 403— ante mismatch para
    # NO confirmar la existencia del approval_id a un tercero.
    if not req or req.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval {approval_id} not found"
        )

    return ApprovalStatusResponse(
        approval_id=req.id,
        query_id=req.details.get("query_id", "unknown") if req.details else "unknown",
        content_type=req.action_type.value,
        content=req.preview or "",
        status="pending",  # If found in get_request(), it's still pending
        risk_level=None,
        warnings=req.risks,
        created_at=req.created_at,
    )


@router.post(
    "/{approval_id}",
    response_model=ApprovalResultResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Approval not found"},
        400: {"model": ErrorResponse, "description": "Invalid action"},
    },
    summary="Approve or reject",
    description="Submit approval, rejection, or modification for a pending request.",
)
@limiter.limit(_get_rate_limit)
async def submit_approval(
    request: Request,
    approval_id: str,
    approval_request: ApprovalRequest,
    hitl_manager: HITLManager = Depends(get_hitl_manager),
) -> ApprovalResultResponse:
    """Aprobar, rechazar o modificar una solicitud."""
    # F6: solo el dueño de la sesión aprueba lo que su sesión pidió (la de otro, 404)
    await asegurar_sesion_actual(approval_request.session_id)
    # F7: la puede estar esperando otro worker, o nadie (reinicio): se busca fuera del proceso
    req = await hitl_manager.buscar(approval_id)

    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval {approval_id} not found or already processed"
        )

    # SEC-3: enforce session ownership. The caller must declare the session
    # they own and it must match the session that opened the HITL request.
    # Without this check anyone with the API key could approve another
    # user's pending SQL/code execution.
    if req.session_id != approval_request.session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Approval does not belong to this session",
        )

    # If found in get_request(), it's pending (waiting for response)

    try:
        if approval_request.action == ApprovalAction.APPROVE:
            success = await hitl_manager.approve(approval_id)
            message = "Approved successfully"

        elif approval_request.action == ApprovalAction.REJECT:
            success = await hitl_manager.reject(approval_id, approval_request.reason or "Rejected by user")
            message = "Rejected successfully"

        elif approval_request.action == ApprovalAction.MODIFY:
            if not approval_request.modified_content:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="modified_content is required for modify action"
                )
            success = await hitl_manager.modify(approval_id, approval_request.modified_content)
            message = "Modified and approved successfully"

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown action: {approval_request.action}"
            )

        if not success:
            # F7 (auditoría): otra respuesta llegó antes (doble clic, otra pestaña u otra réplica),
            # o el turno que la esperaba terminó (expiró, «Detener») sin aplicarla: esta no cuenta.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Approval {approval_id} was already answered or expired before it could be applied",
            )

        return ApprovalResultResponse(
            approval_id=approval_id,
            action=approval_request.action,
            success=success,
            message=message,
            query_result=None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing approval {approval_id}: {e}")
        # Sanitizar error antes de enviar al cliente
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=sanitize_error_for_client(e)
        ) from e


@router.get(
    "/history/{session_id}",
    response_model=list[ApprovalStatusResponse],
    summary="Get approval history",
    description="Get approval history for a specific session.",
)
@limiter.limit(_get_rate_limit)
async def get_approval_history(
    request: Request,
    session_id: str,
    hitl_manager: HITLManager = Depends(get_hitl_manager),
) -> list[ApprovalStatusResponse]:
    """Obtener historial de aprobaciones de una sesión."""
    await asegurar_sesion_actual(session_id)
    # Filtrar aprobaciones por sesión - only pending are stored
    all_requests = hitl_manager.get_pending_requests()

    session_requests = [
        r for r in all_requests
        if r.details and r.details.get("session_id") == session_id
    ]

    return [
        ApprovalStatusResponse(
            approval_id=req.id,
            query_id=req.details.get("query_id", "unknown") if req.details else "unknown",
            content_type=req.action_type.value,
            content=req.preview or "",
            status="pending",  # All requests in pending_requests are pending
            risk_level=None,
            warnings=req.risks,
            created_at=req.created_at,
        )
        for req in session_requests
    ]
