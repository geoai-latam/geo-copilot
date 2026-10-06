"""Contrato de eventos del frontend para ReAct (Fase 4 / F4.T2).

Prerrequisito del bucle ReAct (F4.4). Define el ESQUEMA TIPADO de los eventos
que el bucle emitirá al frontend (vía WebSocket/stream) para que la UI pueda
renderizar el razonamiento paso a paso: "pensando…", "llamando a query_database",
"obtuve 12 filas", "respuesta final".

Hoy el frontend solo recibe ``step_progress`` (multi-paso de plan fijo) y
``retry_*``. El bucle ReAct es dinámico: el número y tipo de pasos no se conocen
de antemano, así que necesita su propio contrato estable.

CONTRATO (invariantes de secuencia que el emisor — F4.4 — debe respetar):
  1. Un turno emite 0+ pares (TOOL_CALL → TOOL_RESULT) y termina SIEMPRE con
     exactamente un FINAL_ANSWER **o** un ERROR (nunca ambos, nunca ninguno).
  2. Todo TOOL_RESULT corresponde a un TOOL_CALL previo del mismo ``step``.
  3. ``step`` es monótono no decreciente y arranca en 0.
  4. CIRCUIT_BREAK (si aparece) es el último evento antes de FINAL_ANSWER/ERROR
     e indica que se alcanzó el tope de iteraciones/presupuesto (F4.3).
  5. Los payloads son RESÚMENES sanitizados (sin geojson crudo ni secretos);
     la data pesada viaja por el canal de resultados normal, no por estos eventos.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReActEventType(str, Enum):
    """Tipos de evento del bucle ReAct."""

    REASONING = "reasoning"          # el agente está deliberando (texto opcional)
    TOOL_CALL = "tool_call"          # decidió invocar una herramienta
    TOOL_RESULT = "tool_result"      # observó el resultado de la herramienta
    FINAL_ANSWER = "final_answer"    # terminó con una respuesta
    CIRCUIT_BREAK = "circuit_break"  # cortó por tope de iteraciones/presupuesto
    ERROR = "error"                  # terminó por error irrecuperable


class ReActEvent(BaseModel):
    """Un evento del bucle ReAct para el frontend."""

    type: ReActEventType
    step: int = Field(..., ge=0, description="Índice de iteración del bucle (0-based)")
    tool: str | None = Field(None, description="Herramienta (en tool_call/tool_result)")
    summary: str | None = Field(
        None, description="Resumen legible y sanitizado (args, resultado o mensaje)"
    )
    success: bool | None = Field(None, description="Éxito (en tool_result)")
    session_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"type": "tool_call", "step": 0, "tool": "query_database",
                 "summary": "sql=SELECT count(*) FROM lotes"},
                {"type": "tool_result", "step": 0, "tool": "query_database",
                 "summary": "12 filas", "success": True},
                {"type": "final_answer", "step": 1, "summary": "Hay 12 lotes."},
            ]
        }
    }


# Mapeo de las clases de decisión del audit (F4.T3) → tipo de evento FE.
_KIND_TO_EVENT = {
    "tool_call": ReActEventType.TOOL_CALL,
    "tool_result": ReActEventType.TOOL_RESULT,
    "final": ReActEventType.FINAL_ANSWER,
    "error": ReActEventType.ERROR,
}


def event_from_decision(decision: dict[str, Any], session_id: str | None = None) -> ReActEvent:
    """Construye un ``ReActEvent`` a partir de una decisión del ``DecisionTrace``
    (F4.T3). Une la auditoría interna con el contrato del frontend sin duplicar
    la lógica de sanitización (la decisión ya viene sanitizada).
    """
    kind = decision.get("kind", "")
    return ReActEvent(
        type=_KIND_TO_EVENT.get(kind, ReActEventType.REASONING),
        step=int(decision.get("step", 0)),
        tool=decision.get("tool"),
        summary=decision.get("detail"),
        success=decision.get("success"),
        session_id=session_id,
    )
