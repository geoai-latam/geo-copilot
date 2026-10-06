"""Circuit-breaker del bucle ReAct (Fase 4 / F4.3).

Un bucle de tool-calling guiado por LLM puede no terminar: el modelo puede pedir
herramientas indefinidamente (bucle de reintentos, oscilación entre dos acciones,
malentendido del estado). Sin un corte duro, eso quema herramientas, tokens y
tiempo, y puede colgar el grafo.

``CircuitBreaker`` impone DOS límites independientes por turno:
  - ``max_tool_calls``: número de invocaciones de herramienta (la guarda primaria).
  - ``max_tokens`` (opcional, 0 = sin límite): presupuesto de tokens consumidos.

El bucle (F4.4) consulta ``tripped`` antes de cada iteración y, si saltó, fuerza
una respuesta final honesta ("alcancé el límite de pasos…") en vez de seguir.
``record_tool_call`` / ``record_tokens`` se llaman tras cada paso.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CircuitBreaker:
    """Corta el bucle ReAct al alcanzar el tope de herramientas o tokens."""

    max_tool_calls: int = 12
    max_tokens: int = 0  # 0 = sin límite de tokens
    tool_calls: int = 0
    tokens_used: int = 0

    def __post_init__(self) -> None:
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls debe ser >= 1")
        if self.max_tokens < 0:
            raise ValueError("max_tokens no puede ser negativo")

    def record_tool_call(self, n: int = 1) -> None:
        self.tool_calls += n

    def record_tokens(self, n: int) -> None:
        if n and n > 0:
            self.tokens_used += n

    @property
    def tripped(self) -> bool:
        """¿Se alcanzó algún límite? El bucle debe cerrar si es True."""
        if self.tool_calls >= self.max_tool_calls:
            return True
        if self.max_tokens and self.tokens_used >= self.max_tokens:
            return True
        return False

    @property
    def reason(self) -> str | None:
        """Por qué saltó (para el log/respuesta honesta), o None si no saltó."""
        if self.tool_calls >= self.max_tool_calls:
            return (
                f"límite de {self.max_tool_calls} llamadas a herramientas alcanzado"
            )
        if self.max_tokens and self.tokens_used >= self.max_tokens:
            return (
                f"presupuesto de {self.max_tokens} tokens agotado "
                f"({self.tokens_used} usados)"
            )
        return None

    def remaining_calls(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls)

    @classmethod
    def from_settings(cls, settings) -> CircuitBreaker:
        """Construye el breaker desde la config (react_max_tool_calls / budget)."""
        return cls(
            max_tool_calls=getattr(settings, "react_max_tool_calls", 12),
            max_tokens=getattr(settings, "react_token_budget", 0) or 0,
        )
