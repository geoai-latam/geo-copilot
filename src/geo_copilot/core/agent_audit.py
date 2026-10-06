"""Auditoría de decisiones del agente (Fase 4 / F4.T3) — prerrequisito ReAct.

El gap que cierra: cuando el agente pase a un bucle ReAct (elige herramienta →
observa resultado → vuelve a decidir), necesitamos una traza ESTRUCTURADA y
sanitizada de CADA decisión, no solo el ``reasoning_trace`` de una pasada (F1.2).
Sin esto, un bucle de tool-calling es una caja negra: no se puede depurar por qué
llamó a tal herramienta, ni auditar el costo, ni mostrarle al usuario el camino.

``DecisionTrace`` registra cada paso del bucle (tool_call / tool_result / final /
error) y produce una lista lista para la respuesta/telemetría. Sanitiza:
  - trunca cada campo a ``MAX_LEN`` y elimina saltos de línea;
  - redacta valores de claves sensibles (api_key/password/token/secret) en args;
  - nunca incluye objetos crudos (geojson completo, etc.): solo resúmenes.

Es independiente del bucle (lo consumirá F4.4): aquí solo está la mecánica de
registro + sanitización, testeable de forma aislada.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_MAX_LEN = 160
_SENSITIVE_KEYS = ("api_key", "apikey", "password", "passwd", "token", "secret", "authorization")


def _flatten(value: object) -> str:
    """Convierte cualquier valor a un string corto, sin saltos de línea."""
    text = str(value)
    text = " ".join(text.split())  # colapsa whitespace/newlines
    if len(text) > _MAX_LEN:
        text = text[: _MAX_LEN - 1] + "…"
    return text


def _sanitize_args(args: object) -> str:
    """Resumen sanitizado de los argumentos de una herramienta.

    Redacta valores de claves sensibles; resume dicts/listas grandes.
    """
    if isinstance(args, dict):
        parts: list[str] = []
        for k, v in list(args.items())[:8]:
            if any(s in str(k).lower() for s in _SENSITIVE_KEYS):
                parts.append(f"{k}=***")
            else:
                parts.append(f"{k}={_flatten(v)}")
        return _flatten(", ".join(parts))
    return _flatten(args)


@dataclass
class AgentDecision:
    """Una decisión del agente dentro del bucle."""

    step: int
    kind: str  # "tool_call" | "tool_result" | "final" | "error"
    tool: str | None = None
    detail: str | None = None       # args (tool_call) / resumen (result) / nota
    success: bool | None = None

    def to_dict(self) -> dict:
        d: dict = {"step": self.step, "kind": self.kind}
        if self.tool is not None:
            d["tool"] = self.tool
        if self.detail is not None:
            d["detail"] = self.detail
        if self.success is not None:
            d["success"] = self.success
        return d


@dataclass
class DecisionTrace:
    """Colector de decisiones de UN turno del agente.

    Uso típico (lo hará el nodo agent_loop en F4.4)::

        trace = DecisionTrace()
        trace.record_tool_call(0, "query_database", {"sql": "..."})
        trace.record_result(0, "query_database", success=True, result=rows)
        trace.record_final(1, "Respondí con 12 resultados")
        state_update["decision_trace"] = trace.to_list()
    """

    decisions: list[AgentDecision] = field(default_factory=list)

    def record_tool_call(self, step: int, tool: str, args: object = None) -> None:
        self.decisions.append(
            AgentDecision(step=step, kind="tool_call", tool=tool,
                          detail=_sanitize_args(args) if args is not None else None)
        )

    def record_result(self, step: int, tool: str, *, success: bool, result: object = None) -> None:
        self.decisions.append(
            AgentDecision(step=step, kind="tool_result", tool=tool,
                          detail=_flatten(result) if result is not None else None,
                          success=success)
        )

    def record_final(self, step: int, note: str = "") -> None:
        self.decisions.append(
            AgentDecision(step=step, kind="final", detail=_flatten(note) if note else None)
        )

    def record_reflection(self, step: int, note: str = "") -> None:
        """F5: el agente reconsideró si su respuesta cubre la consulta y siguió."""
        self.decisions.append(
            AgentDecision(step=step, kind="reflection", detail=_flatten(note) if note else None)
        )

    def record_error(self, step: int, error: object) -> None:
        self.decisions.append(
            AgentDecision(step=step, kind="error", detail=_flatten(error), success=False)
        )

    def to_list(self) -> list[dict]:
        """Lista sanitizada de decisiones para la respuesta/telemetría."""
        return [d.to_dict() for d in self.decisions]

    def __len__(self) -> int:
        return len(self.decisions)
