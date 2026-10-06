"""`QueryResponse`: el sobre de una respuesta de `/query` (F4, S4.1).

Vive en el contrato y no en la API para que el frontend genere sus tipos del
MISMO modelo que serializa el backend: el drift `reasoning_trace` /
`execution_trace` (el frontend esperaba un campo que el backend nunca mandó)
deja de ser posible por construcción.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from geo_copilot.platform.contracts.artifacts import Artifact
from geo_copilot.platform.contracts.common import CONTRACT_VERSION, Strict

QueryStatus = Literal["pending", "processing", "waiting_approval", "completed", "failed"]


class CorrectionInfo(Strict):
    """El SQL falló y se auto-corrigió: cuántas veces y por qué (categoría, no el error crudo)."""

    corrections_applied: int = Field(ge=0)
    original_error: str | None = None
    final_success: bool = False


class TraceEntry(Strict):
    """Una decisión del agente (router, herramienta, reflexión…), ya saneada."""

    step: int | None = None
    kind: str
    agent: str | None = None
    tool: str | None = None
    detail: str | None = None
    success: bool | None = None


class QueryResponse(Strict):
    contract_version: str = CONTRACT_VERSION
    query_id: str
    session_id: str
    status: QueryStatus
    intent: str | None = None
    confidence: float | None = None
    message: str | None = None
    requires_approval: bool = False
    pending_approval_id: str | None = None
    #: Lo que el turno produjo (capas, tablas, gráficos, servicios, informe…).
    artifacts: list[Artifact] = Field(default_factory=list)
    #: SQL ejecutado en el turno (pestaña SQL; también en la procedencia de la capa).
    sql: str | None = None
    #: FH.8: 2–3 pedidos de siguiente paso que propuso el LLM (clic = enviarlo).
    suggestions: list[str] = Field(default_factory=list, max_length=3)
    correction: CorrectionInfo | None = None
    reasoning_trace: list[TraceEntry] | None = None
    #: Con zona horaria: sin ella no es una fecha-hora ISO válida para el cliente.
    created_at: AwareDatetime
