"""Bus de avisos entre procesos (F7, S7.1).

`publicar(canal, datos)` llega a TODOS los procesos suscritos a un prefijo del canal (también al
que publica). Canales en uso:

- `ws:<sesion>` — un mensaje para el WebSocket de esa sesión: lo entrega el proceso que lo tiene;
- `hitl:resp` — la respuesta a una aprobación: la recoge el proceso cuyo turno la espera;
- `cancelar:<sesion>` — «Detener»: lo cancela el proceso que corre la consulta.

En memoria (un solo proceso) la entrega es directa; en Redis, por pub/sub. Con Redis, cada canal
tiene su propia cola: el orden dentro de un canal se conserva, pero un WebSocket lento solo retrasa
SUS mensajes, no las aprobaciones ni los «Detener» de las demás sesiones (F7, auditoría).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

Manejador = Callable[[str, dict[str, Any]], Awaitable[None]]


class Bus(Protocol):
    def suscribir(self, prefijo: str, manejador: Manejador) -> None: ...
    async def publicar(self, canal: str, datos: dict[str, Any]) -> None: ...
    async def iniciar(self) -> None: ...
    async def cerrar(self) -> None: ...


class _Suscripciones:
    def __init__(self) -> None:
        self._manejadores: list[tuple[str, Manejador]] = []

    def suscribir(self, prefijo: str, manejador: Manejador) -> None:
        self._manejadores.append((prefijo, manejador))

    async def despachar(self, canal: str, datos: dict[str, Any]) -> None:
        for prefijo, manejador in list(self._manejadores):
            if canal.startswith(prefijo):
                try:
                    await manejador(canal, datos)
                except Exception:
                    logger.exception(f"[bus] el manejador de {prefijo!r} falló con {canal!r}")


class BusEnMemoria(_Suscripciones):
    """Un solo proceso: publicar = entregar."""

    async def publicar(self, canal: str, datos: dict[str, Any]) -> None:
        await self.despachar(canal, datos)

    async def iniciar(self) -> None:
        return None

    async def cerrar(self) -> None:
        return None


class BusEnRedis(_Suscripciones):
    """Varios procesos: Redis pub/sub con un lector en segundo plano por proceso."""

    PREFIJO = "geo:bus:"
    #: Mensajes que esperan como mucho en la cola de UN canal (un cliente que no lee): luego se
    #: descartan, con aviso en el log.
    COLA_MAX = 500

    def __init__(self, cliente: Any) -> None:
        super().__init__()
        self._r = cliente
        self._tarea: asyncio.Task[None] | None = None
        self._pubsub: Any = None
        self._cerrando = False
        self._colas: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._drenadores: set[asyncio.Task[None]] = set()

    async def publicar(self, canal: str, datos: dict[str, Any]) -> None:
        """Lanza los errores de Redis: quien publica decide si su aviso es prescindible."""
        await self._r.publish(self.PREFIJO + canal, json.dumps(datos, default=str))

    async def iniciar(self) -> None:
        self._pubsub = self._r.pubsub()
        await self._pubsub.psubscribe(self.PREFIJO + "*")
        self._tarea = asyncio.create_task(self._leer())

    async def _leer(self) -> None:
        # Condición de salida propia: con redis-py 8, `get_message(timeout=…)` se traga la
        # cancelación y un `while True` no terminaba nunca — el apagado de la app se colgaba
        # (visto en CI: el test esperaba para siempre a `cerrar()`).
        yo = asyncio.current_task()
        while not self._cerrando and not (yo is not None and yo.cancelling()):
            try:
                msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:  # Redis se cae: se reintenta, el proceso sigue sirviendo
                logger.warning("[bus] error leyendo de Redis; reintento", exc_info=True)
                await asyncio.sleep(1.0)
                continue
            if not msg:
                continue
            canal = msg.get("channel")
            canal = canal.decode() if isinstance(canal, bytes) else str(canal)
            datos = msg.get("data")
            try:
                carga = json.loads(datos.decode() if isinstance(datos, bytes) else datos)
            except (ValueError, TypeError, AttributeError):
                continue
            self._encolar(canal.removeprefix(self.PREFIJO), carga)

    def _encolar(self, canal: str, carga: dict[str, Any]) -> None:
        """F7 (auditoría): el lector no espera a los manejadores. Antes un `send_json` a un cliente
        lento bloqueaba el único bucle de lectura: las aprobaciones y los «Detener» de TODAS las
        sesiones del proceso esperaban detrás."""
        cola = self._colas.get(canal)
        if cola is None:
            cola = self._colas[canal] = asyncio.Queue(maxsize=self.COLA_MAX)
            tarea = asyncio.create_task(self._drenar(canal, cola))
            self._drenadores.add(tarea)
            tarea.add_done_callback(self._drenadores.discard)
        try:
            cola.put_nowait(carga)
        except asyncio.QueueFull:
            logger.warning(f"[bus] cola de {canal!r} llena ({self.COLA_MAX}): se descarta un mensaje")

    async def _drenar(self, canal: str, cola: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            try:
                carga = cola.get_nowait()
            except asyncio.QueueEmpty:
                if self._colas.get(canal) is cola:
                    del self._colas[canal]  # sin await entre medias: nadie encola en este hueco
                return
            await self.despachar(canal, carga)

    async def cerrar(self) -> None:
        self._cerrando = True
        for tarea in list(self._drenadores):
            tarea.cancel()
        if self._tarea is not None:
            self._tarea.cancel()
            # acotado: el lector sale en ≤ 1 s (su timeout de lectura); nunca bloquea el apagado
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(self._tarea), timeout=3)
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()


_bus: Bus = BusEnMemoria()


def instalar(bus: Bus) -> None:
    global _bus
    _bus = bus


def bus() -> Bus:
    return _bus
