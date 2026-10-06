"""
RetryExecutor — unificador de la lógica de reintento + auto-corrección
(ORC-4, Fase 6 #4).

Antes el repo tenía la misma lógica de retry copiada inline en tres
sitios (``_gis_agent_node`` y ``_python_agent_node`` en graph.py, y
``PlanExecutor._execute_step``), más dos frameworks muertos que se
borraron en Fase 2 (``RetryExecutor`` del paquete ``core/execution`` y
``ExecutionController``). Esta es la implementación viva y única.

Diseño:

* Sin estado global. ``RetryExecutor.run()`` recibe callables y los
  invoca; nada se guarda entre llamadas.
* Sin acoplamiento al grafo, al WebSocket ni a un agente concreto. Toda
  la I/O específica del caller vive en los callbacks que recibe.
* Sin segunda implementación. Cualquier futuro callsite que necesite
  retry-con-corrección debe usar esto. Los dos retries inline que
  quedan en graph.py (gis_agent_node, python_agent_node) se migrarán
  como parte de #6 (partir graph.py), con la red de seguridad de los
  tests de integración de #5.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.errors import GraphInterrupt

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AttemptOutcome:
    """Resultado estandarizado de un intento.

    Attributes:
        success: True si el intento debe considerarse exitoso.
        payload: Datos devueltos por la operación (geojson, sql, etc.).
        error: Mensaje legible si falló (None en éxito).
        no_retry: Si True, no se reintenta aunque ``success`` sea False.
            Útil para condiciones terminales que no son error técnico
            (p. ej. el plan se pausó esperando una selección del usuario).
    """

    success: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    no_retry: bool = False


@dataclass
class RetryResult:
    """Resultado agregado del bucle de reintento."""

    outcome: AttemptOutcome
    attempts: int
    errors: list[str] = field(default_factory=list)


# Firmas de los callbacks que recibe ``RetryExecutor.run``.
AttemptFn = Callable[[int], Awaitable[AttemptOutcome]]
"""``attempt(idx)``: ejecuta un intento. ``idx`` es 0-based."""

CorrectFn = Callable[[str, int], Awaitable[bool]]
"""``correct(error, idx)``: aplica una corrección.

Debe mutar el estado externo (vía closure) y devolver:
- True  → hubo corrección útil; vale la pena reintentar.
- False → no se pudo corregir; abortar y devolver el último error.
"""

OnRetryFn = Callable[[int, int, str], Awaitable[None]]
"""``on_retry(next_attempt, max_attempts, last_error)``: notificación
(p. ej. enviar ``send_retry_started`` por WebSocket)."""


class RetryExecutor:
    """Ejecuta una operación con reintentos y corrección opcional.

    Uso típico::

        outcome = await RetryExecutor(max_attempts=3).run(
            attempt=do_step,
            correct=apply_correction,    # opcional
            on_retry=notify_retry,        # opcional
        )

    El executor no asume nada sobre qué se está ejecutando ni cómo se
    notifica; toda la I/O específica vive en los callbacks.
    """

    def __init__(self, max_attempts: int):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts

    async def run(
        self,
        *,
        attempt: AttemptFn,
        correct: CorrectFn | None = None,
        on_retry: OnRetryFn | None = None,
    ) -> RetryResult:
        """Ejecuta ``attempt`` hasta éxito, terminal, o agotar reintentos."""
        errors: list[str] = []
        last_outcome = AttemptOutcome(success=False)

        for i in range(self.max_attempts):
            try:
                outcome = await attempt(i)
            except GraphInterrupt:
                # F4.2: control-flow de LangGraph (HITL interrupt) — NO es un
                # fallo del intento; debe PROPAGAR para que el grafo pause y se
                # pueda reanudar. Si lo tragáramos, el HITL nunca pausaría.
                raise
            except Exception as exc:  # noqa: BLE001 — caller may raise anything
                logger.warning(f"[RetryExecutor] attempt {i + 1} raised: {exc}")
                outcome = AttemptOutcome(success=False, error=str(exc))

            if outcome.success or outcome.no_retry:
                return RetryResult(outcome=outcome, attempts=i + 1, errors=errors)

            err = outcome.error or "Unknown error"
            errors.append(err)
            last_outcome = outcome

            # Último intento: salir sin notificar/corregir.
            if i >= self.max_attempts - 1:
                break

            # Aplicar corrección si hay corrector.
            if correct is not None:
                proceed = False
                try:
                    proceed = await correct(err, i)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"[RetryExecutor] correct() raised: {exc}")
                if not proceed:
                    # Sin corrección útil: no tiene sentido reintentar.
                    break

            # Notificar el siguiente intento (1-based para el usuario).
            if on_retry is not None:
                try:
                    await on_retry(i + 2, self.max_attempts, err)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[RetryExecutor] on_retry raised: {exc}")

        return RetryResult(outcome=last_outcome, attempts=len(errors), errors=errors)
