"""Los MODELOS del HITL: tipos de acción y estados, la solicitud y la respuesta, y el resumen
(digest) del contenido aprobado que impide ejecutar otro distinto.

Salió de `hitl.py` (F4 del plan de calidad: hitl.py tenía 662 líneas), tal cual.
"""

import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Helper: timestamp UTC tz-aware. Reemplaza ``_utcnow()``
    (deprecado en Python 3.12+) y los ``datetime.now()`` naive previos."""
    return datetime.now(UTC)


class HITLActionType(str, Enum):
    """Tipos de acciones que requieren HITL."""
    DATA_IMPORT = "data_import"
    SQL_EXECUTION = "sql_execution"
    CODE_EXECUTION = "code_execution"
    RECOMMENDATION = "recommendation"
    EXTERNAL_API = "external_api"
    PLAN_APPROVAL = "plan_approval"  # Aprobación de plan multi-paso


class HITLStatus(str, Enum):
    """Estados de una solicitud HITL."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXPIRED = "expired"


def content_digest(content: str | None) -> str:
    """Huella del artefacto que se presenta al humano (R0.8, AUD-15).

    El panel de aprobación mostraba `sql[:500]` sin elipsis ni contador, y al
    aprobar se ejecutaba la variable COMPLETA: el artefacto autorizado y el
    ejecutado eran objetos distintos, así que bastaba con que la cola del SQL
    —invisible— cambiara la tabla objetivo. El control no se evadía: se
    engañaba con su propia presentación.

    Comparar esta huella antes de ejecutar convierte esa divergencia en un
    fallo ruidoso en vez de una ejecución silenciosa.
    """
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


class ApprovedContentMismatch(RuntimeError):
    """Lo que se va a ejecutar no es lo que se aprobó (R0.8, AUD-15)."""


class HITLRequest(BaseModel):
    """Solicitud de aprobación HITL."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    action_type: HITLActionType
    title: str
    description: str
    details: dict[str, Any] = {}
    risks: list[str] = []
    preview: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)  # S8: tz-aware
    expires_at: datetime | None = None
    metadata: dict[str, Any] = {}
    session_id: str | None = None  # Session to notify


class HITLResponse(BaseModel):
    """Respuesta a una solicitud HITL."""
    request_id: str
    status: HITLStatus
    modified_content: Any | None = None
    feedback: str = ""
    approved_by: str | None = None
    approved_at: datetime | None = None


async def _auditar(accion: str, request: HITLRequest, resultado: str,
                   extra: dict[str, Any] | None = None) -> None:
    """Deja constancia de una solicitud HITL o de su decisión (F6, S6.4)."""
    from geo_copilot.platform import auditoria

    await auditoria.registrar(
        accion, f"{request.action_type.value}:{request.id}", resultado, session_id=request.session_id,
        detalle={"titulo": request.title, "vista_previa": request.preview, **(extra or {})},
    )
