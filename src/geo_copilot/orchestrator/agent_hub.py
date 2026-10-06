"""
AgentHub — registry para comunicación A2A real (2026-05-31).

Permite que un agente, mid-execution, invoque métodos públicos de otro
agente. No es "comunicación implícita via state" — es una llamada
explícita ``self.hub.get("data_agent").lookup_entity(...)`` que retorna
un resultado tipado, y ese resultado cambia el comportamiento del agente
origen.

**Propiedades del protocolo:**

1. **Acoplamiento por capacidad, no por instancia.** El agente origen no
   sabe qué clase implementa el target — pide el ``capability`` y el
   hub resuelve. Si mañana mueves la capacidad ``lookup_entity`` del
   ``DataAgent`` al ``GISAgent``, los callers no se enteran.

2. **Telemetría obligatoria.** Cada call queda registrada en
   ``hub.call_log`` con timestamp, caller, target, método, args resumidos
   y duración. Esto permite que los tests verifiquen que A2A ocurrió de
   verdad, y que en producción se vea cuánto se usa.

3. **No-circular.** El hub detecta llamadas circulares (A→B→A) y las
   rechaza para no enredar.

4. **Async-first.** Cualquier método invocado debe ser ``async`` — el
   hub awaiteará el resultado.

Por qué NO una cola de mensajes con dispatcher: añadiría 1 nodo extra al
grafo por cada A2A call, multiplicando latencia. El A2A real útil es
casi siempre síncrono ("¿existe esta entidad?" — sí/no rápido); la cola
no aporta y complica el debugging.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


# Pila de calls activos en el contexto async actual. Sirve para detectar
# loops (A → B → A) sin necesidad de pasar un parámetro explícito por toda
# la cadena. ContextVar respeta scopes async.
_call_stack: ContextVar[tuple[str, ...]] = ContextVar(
    "agent_hub_call_stack", default=()
)


@dataclass
class A2ACall:
    """Registro de una llamada A2A (para telemetría / debugging)."""
    caller: str
    target: str
    method: str
    args_summary: str
    duration_ms: float
    success: bool
    error: str | None = None
    timestamp: float = field(default_factory=time.time)


class AgentHub:
    """Registry mínimo para comunicación A2A entre agentes."""

    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}
        self.call_log: list[A2ACall] = []
        # Límite del log para no crecer indefinidamente en sesiones largas.
        self._max_log_entries = 1000

    def register(self, name: str, agent: Any) -> None:
        """Registrar un agente por nombre canónico."""
        if name in self._agents:
            logger.warning(f"[AgentHub] sobreescribiendo agente '{name}'")
        self._agents[name] = agent
        logger.info(f"[AgentHub] registrado '{name}': {type(agent).__name__}")

    def get(self, name: str) -> Any | None:
        """Obtener referencia al agente (None si no está registrado)."""
        return self._agents.get(name)

    def has(self, name: str) -> bool:
        return name in self._agents

    async def call(
        self,
        *,
        caller: str,
        target: str,
        method: str,
        **kwargs: Any,
    ) -> tuple[bool, Any]:
        """Invocar un método público de otro agente con telemetría.

        Devuelve ``(success, result_or_error)``. Si ``success=False``,
        el segundo elemento es el mensaje de error. Esto permite al
        caller decidir si degrada o aborta sin tener que envolver en
        try/except cada llamada.
        """
        agent = self._agents.get(target)
        if agent is None:
            err = f"target '{target}' no registrado en el hub"
            self._record(caller, target, method, "", 0.0, False, err)
            return False, err

        # Anti-loop: si el target ya está en la pila de calls activos,
        # rechazamos para no enredar (A→B→A degeneraría en recursión).
        stack = _call_stack.get()
        if target in stack:
            err = f"loop A2A detectado: {' → '.join([*stack, target])}"
            logger.warning(f"[AgentHub] {err}")
            self._record(caller, target, method, "", 0.0, False, err)
            return False, err

        fn = getattr(agent, method, None)
        if fn is None or not callable(fn):
            err = f"'{target}.{method}' no existe o no es invocable"
            self._record(caller, target, method, "", 0.0, False, err)
            return False, err

        args_summary = _summarize(kwargs)
        token = _call_stack.set((*stack, target))
        start = time.perf_counter()
        try:
            result = fn(**kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            duration_ms = (time.perf_counter() - start) * 1000.0
            self._record(caller, target, method, args_summary, duration_ms, True)
            logger.debug(
                f"[A2A] {caller} → {target}.{method}({args_summary}) "
                f"OK in {duration_ms:.1f}ms"
            )
            return True, result
        except Exception as exc:  # despachador A2A: el agente destino puede lanzar cualquier cosa; se devuelve (False, err)
            duration_ms = (time.perf_counter() - start) * 1000.0
            err = f"{type(exc).__name__}: {exc}"
            self._record(caller, target, method, args_summary, duration_ms, False, err)
            logger.warning(
                f"[A2A] {caller} → {target}.{method}({args_summary}) "
                f"FAILED in {duration_ms:.1f}ms: {err}",
                exc_info=True,
            )
            return False, err
        finally:
            _call_stack.reset(token)

    def _record(
        self,
        caller: str,
        target: str,
        method: str,
        args_summary: str,
        duration_ms: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        if len(self.call_log) >= self._max_log_entries:
            # FIFO: tirar el más viejo.
            self.call_log.pop(0)
        self.call_log.append(A2ACall(
            caller=caller,
            target=target,
            method=method,
            args_summary=args_summary,
            duration_ms=duration_ms,
            success=success,
            error=error,
        ))

    def recent_calls(self, limit: int = 10) -> list[A2ACall]:
        return list(self.call_log[-limit:])

    def clear_log(self) -> None:
        self.call_log.clear()


def _summarize(kwargs: dict[str, Any], max_len: int = 120) -> str:
    """Render compacto de kwargs para el log (sin volcar payloads enormes)."""
    if not kwargs:
        return ""
    parts: list[str] = []
    for k, v in kwargs.items():
        s = repr(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    joined = ", ".join(parts)
    if len(joined) > max_len:
        joined = joined[:max_len - 3] + "..."
    return joined
