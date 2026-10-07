"""EventSink: cómo el orquestador avisa de su progreso SIN conocer la API (S1.2).

Antes, 13 puntos de `orchestrator/` hacían `from geo_copilot.api.websocket import
send_*` dentro de funciones: la dependencia iba al revés (el núcleo conocía su
transporte) y se esquivaba el import circular a mano. Ahora el núcleo emite a una
interfaz y la API, al arrancar, instala la implementación que reenvía por
WebSocket (`api/websocket.WebSocketEventSink`). En tests y en el bench no hay
socket: el sink por defecto no hace nada.

Uso desde el núcleo::

    from geo_copilot.platform import events
    await events.sink().retry_started(session_id, "gis_agent", 1, 3, error, "…")
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EventSink(Protocol):
    """Los eventos de progreso que el núcleo emite. Espejo de los mensajes WS
    que el frontend ya entiende (`api/websocket.WSMessageType`)."""

    async def agent_step(
        self, session_id: str, agent: str, description: str, status: str,
    ) -> None: ...

    async def retry_started(
        self, session_id: str, agent: str, attempt: int, max_attempts: int,
        error: str, action: str = "Corrigiendo...",
    ) -> None: ...

    async def retry_correction(
        self, session_id: str, agent: str, correction_type: str,
        original: str | None = None, corrected: str | None = None,
    ) -> None: ...

    async def retry_result(
        self, session_id: str, agent: str, success: bool, attempts: int,
        message: str | None = None,
    ) -> None: ...

    async def plan_created(
        self, session_id: str, plan: list[dict], reasoning: str | None = None,
    ) -> None: ...

    async def step_progress(
        self, session_id: str, step_index: int, total_steps: int, action: str,
        status: str, result: dict | None = None,
    ) -> None: ...

    async def traza(self, session_id: str, evento: dict) -> None:
        """Un paso del turno, legible para el usuario (orchestrator/traza.py)."""
        ...


class NullSink:
    """No emite nada: tests, bench y cualquier uso del núcleo sin transporte."""

    async def agent_step(self, session_id: str, agent: str, description: str,
                         status: str) -> None:
        return None

    async def retry_started(self, session_id: str, agent: str, attempt: int,
                            max_attempts: int, error: str,
                            action: str = "Corrigiendo...") -> None:
        return None

    async def retry_correction(self, session_id: str, agent: str, correction_type: str,
                               original: str | None = None,
                               corrected: str | None = None) -> None:
        return None

    async def retry_result(self, session_id: str, agent: str, success: bool,
                           attempts: int, message: str | None = None) -> None:
        return None

    async def plan_created(self, session_id: str, plan: list[dict],
                           reasoning: str | None = None) -> None:
        return None

    async def step_progress(self, session_id: str, step_index: int, total_steps: int,
                            action: str, status: str,
                            result: dict | None = None) -> None:
        return None

    async def traza(self, session_id: str, evento: dict) -> None:
        return None


_sink: EventSink = NullSink()


def set_sink(sink: EventSink) -> None:
    """Instala el sink del proceso (la API lo hace al arrancar)."""
    global _sink
    _sink = sink


def sink() -> EventSink:
    return _sink
