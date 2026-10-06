"""
Endpoints para gestión de sesiones.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from geo_copilot.api.dependencies import get_conversation_manager
from geo_copilot.api.models import (
    ErrorResponse,
    PreferencesUpdateRequest,
    SessionCreateRequest,
    SessionListResponse,
    SessionResponse,
)
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.conversation import ConversationManager

logger = get_logger(__name__)

from geo_copilot.api.auth import asegurar_sesion_actual, require_principal

router = APIRouter(
    prefix="/session",
    tags=["Session"],
    dependencies=[Depends(require_principal)],
)

# S2: formato canónico de session_id (alfanumérico + guiones/underscores,
# 1..100). Antes solo ``get_history`` lo validaba; el resto de endpoints
# aceptaba ids arbitrarios. NOTA: el modelo es single-tenant (una API
# key, sin principal por usuario), así que esto NO implementa ownership
# real — un sistema multi-tenant requeriría ligar la sesión a un sujeto
# autenticado. Aquí solo unificamos la validación de formato/inyección.
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{1,100}$")


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_id inválido (use alfanumérico, '-' o '_', 1-100 chars)",
        )


async def _sesion_propia(session_id: str) -> None:
    """Formato del id y (F6) que la sesión sea de quien pregunta: la de otro no existe (404)."""
    _validate_session_id(session_id)
    await asegurar_sesion_actual(session_id)


@router.post(
    "/",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
    description="Create a new conversation session with optional preferences.",
)
async def create_session(
    request: SessionCreateRequest | None = None,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> SessionResponse:
    """Crear nueva sesión de conversación."""
    session_id = request.session_id if request else None
    if session_id is not None:
        await _sesion_propia(session_id)
        # R0.11 (auditoría 2026-07-26, AUD-23): 409 si el id ya existe. Antes
        # esto usaba `create_session` a secas, que construye un contexto NUEVO
        # y lo persiste con asignación incondicional: quien conociera el id de
        # otro le vaciaba la conversación en caliente. Verificado antes del
        # fix: `POST /session/` con un id ajeno devolvía 201.
        if conversation_manager.get_session(session_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="La sesión ya existe. Usa POST /session/{id}/reset para reiniciarla.",
            )
    context = conversation_manager.create_session(session_id)
    await asegurar_sesion_actual(context.session_id)  # F6: el id generado queda de quien lo creó

    # Aplicar preferencias si se proporcionaron
    if request and request.preferences:
        context.update_preferences(**request.preferences)
        conversation_manager.save_session(context)  # Redis no ve la mutación

    logger.info(f"Created session: {context.session_id}")

    return SessionResponse(
        session_id=context.session_id,
        created_at=context.created_at,
        updated_at=context.updated_at,
        message_count=len(context.history),
        entities_identified=context.state.entities_identified,
        analysis_type=context.state.analysis_type,
        has_results=context.state.last_results is not None,
        preferences={
            "language": context.preferences.language,
            "output_format": context.preferences.output_format,
            "map_style": context.preferences.map_style,
            "narrative_style": context.preferences.narrative_style,
            "max_results": context.preferences.max_results,
        }
    )


@router.get(
    "/",
    response_model=SessionListResponse,
    summary="List all sessions",
    description="Get a list of all active conversation sessions.",
)
async def list_sessions(
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> SessionListResponse:
    """Devolver sólo el recuento de sesiones activas.

    R0.11 (auditoría 2026-07-26, AUD-23): este endpoint devolvía TODAS las
    sesiones vivas con su ``session_id``, y ese id era el único discriminador
    de propiedad del HITL (``approval.py`` y el handshake del WebSocket).
    Enumerarlo bastaba para abrir el WebSocket de otro usuario, leer su SQL en
    claro y responder a sus aprobaciones.

    Como no existe identidad de usuario, TODAS las sesiones son ajenas: no hay
    forma de filtrar "las mías". Así que se deja de enumerar. El ``session_id``
    es un UUID4, de modo que sin listado no es adivinable.

    El frontend no consume este listado (sólo ``POST /session/`` y
    ``/session/{id}/reset``), así que no rompe nada. Cuando exista
    autenticación de usuario, esto puede devolver las sesiones del solicitante.
    """
    total = len(conversation_manager.list_sessions())
    return SessionListResponse(sessions=[], total=total)


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
    summary="Get session details",
    description="Get details of a specific conversation session.",
)
async def get_session(
    session_id: str,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> SessionResponse:
    """Obtener detalles de una sesión."""
    await _sesion_propia(session_id)
    context = conversation_manager.get_session(session_id)

    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    return SessionResponse(
        session_id=context.session_id,
        created_at=context.created_at,
        updated_at=context.updated_at,
        message_count=len(context.history),
        entities_identified=context.state.entities_identified,
        analysis_type=context.state.analysis_type,
        has_results=context.state.last_results is not None,
        preferences={
            "language": context.preferences.language,
            "output_format": context.preferences.output_format,
            "map_style": context.preferences.map_style,
            "narrative_style": context.preferences.narrative_style,
            "max_results": context.preferences.max_results,
        }
    )


@router.patch(
    "/{session_id}/preferences",
    response_model=SessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
    summary="Update session preferences",
    description="Update user preferences for a specific session.",
)
async def update_preferences(
    session_id: str,
    request: PreferencesUpdateRequest,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> SessionResponse:
    """Actualizar preferencias de una sesión."""
    await _sesion_propia(session_id)
    context = conversation_manager.get_session(session_id)

    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    # Actualizar solo los campos proporcionados
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if updates:
        context.update_preferences(**updates)
        conversation_manager.save_session(context)  # Redis no ve la mutación

    return SessionResponse(
        session_id=context.session_id,
        created_at=context.created_at,
        updated_at=context.updated_at,
        message_count=len(context.history),
        entities_identified=context.state.entities_identified,
        analysis_type=context.state.analysis_type,
        has_results=context.state.last_results is not None,
        preferences={
            "language": context.preferences.language,
            "output_format": context.preferences.output_format,
            "map_style": context.preferences.map_style,
            "narrative_style": context.preferences.narrative_style,
            "max_results": context.preferences.max_results,
        }
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
    summary="Delete a session",
    description="Delete a conversation session and all its data.",
)
async def delete_session(
    session_id: str,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> None:
    """Eliminar una sesión."""
    await _sesion_propia(session_id)
    deleted = conversation_manager.delete_session(session_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    logger.info(f"Deleted session: {session_id}")


@router.post(
    "/{session_id}/reset",
    response_model=SessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
    summary="Reset a session",
    description="Reset a session's history and state while keeping the session ID.",
)
async def reset_session(
    session_id: str,
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> SessionResponse:
    """Reiniciar una sesión."""
    await _sesion_propia(session_id)
    context = conversation_manager.get_session(session_id)

    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    context.reset()
    conversation_manager.save_session(context)  # Redis no ve la mutación
    logger.info(f"Reset session: {session_id}")

    return SessionResponse(
        session_id=context.session_id,
        created_at=context.created_at,
        updated_at=context.updated_at,
        message_count=len(context.history),
        entities_identified=context.state.entities_identified,
        analysis_type=context.state.analysis_type,
        has_results=context.state.last_results is not None,
        preferences={
            "language": context.preferences.language,
            "output_format": context.preferences.output_format,
            "map_style": context.preferences.map_style,
            "narrative_style": context.preferences.narrative_style,
            "max_results": context.preferences.max_results,
        }
    )


@router.get(
    "/{session_id}/history",
    response_model=list[dict],
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
    summary="Get conversation history",
    description="Get the message history for a specific session.",
)
async def get_history(
    session_id: str = Path(
        ...,
        min_length=1,
        max_length=100,
        description="ID de sesión (alfanumérico con guiones/underscores)"
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Número máximo de mensajes a retornar"
    ),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> list[dict]:
    """Obtener historial de conversación."""
    # Validar formato de session_id para seguridad (y, F6, que sea suya)
    if not re.match(r'^[a-zA-Z0-9\-_]+$', session_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_id contiene caracteres no válidos"
        )
    await asegurar_sesion_actual(session_id)

    context = conversation_manager.get_session(session_id)

    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    messages = context.get_last_messages(limit)
    return [m.to_dict() for m in messages]


@router.post(
    "/{session_id}/ws-ticket",
    summary="Ticket de un solo uso para abrir el WebSocket de la sesión",
    description=("F6: el navegador no puede mandar cabeceras en un WebSocket y el token en la URL "
                 "acabaría en los logs. El ticket vale unos segundos, sirve para UNA apertura y solo "
                 "para esta sesión."),
)
async def ws_ticket(session_id: str) -> dict:
    await _sesion_propia(session_id)
    from geo_copilot.platform.identidad.principal import principal_actual
    from geo_copilot.platform.identidad.servicio import identidad_actual

    identidad = identidad_actual()
    principal = principal_actual()
    assert principal is not None  # lo fijó require_principal (y _sesion_propia lo exige)
    ticket = await identidad.tickets.emitir(principal, session_id, identidad.ticket_ttl_s)
    return {"ticket": ticket, "expira_en_s": identidad.ticket_ttl_s}
