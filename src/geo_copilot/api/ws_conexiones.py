"""Las CONEXIONES WebSocket por sesión: presencia entre procesos (Redis), envío local o por el
bus, y las tareas en curso que «Detener» cancela.

Salió de `websocket.py` (F4 del plan de calidad: 1.352 líneas), tal cual.
"""

import asyncio
import time
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from geo_copilot.api.ws_mensajes import WSMessage, WSMessageType
from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.api.websocket")


class ConnectionManager:
    """Gestor de conexiones WebSocket.

    Fase 6 #8 / API-13 — el manager también lleva un registro de la tarea
    asyncio que está procesando la consulta activa de cada sesión. Esto
    permite que ``handle_cancel`` cancele la tarea real (antes solo
    enviaba una notificación sin parar nada).
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        # session_id → asyncio.Task de la query en vuelo (si la hay).
        self.active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        # F7 (S7.1): presencia entre procesos — «esta sesión tiene socket en ALGÚN worker».
        self._redis: Any = None
        self._renovadores: dict[str, asyncio.Task[None]] = {}
        #: F7 (auditoría): token de CADA conexión. La presencia es un conjunto de tokens con su
        #: caducidad: al cerrar un socket viejo se quita SU token, no la presencia de la sesión
        #: (antes un DEL sin dueño borraba la del socket nuevo de otra réplica).
        self._tokens: dict[str, str] = {}

    _PRESENCIA = "geo:ws:conexiones:"
    _PRESENCIA_S = 90
    #: Lo que se espera a que un cliente acepte un mensaje antes de darlo por perdido.
    _ENVIO_S = 10.0
    #: Avisos que no se pueden perder: además de al socket local van por el bus (F7, auditoría).
    _CRITICOS = frozenset({WSMessageType.APPROVAL_REQUEST, WSMessageType.RESULT})

    async def _marcar_presencia(self, session_id: str, token: str) -> None:
        clave, ahora = self._PRESENCIA + session_id, time.time()
        pipe = self._redis.pipeline()
        pipe.zadd(clave, {token: ahora + self._PRESENCIA_S})
        pipe.zremrangebyscore(clave, "-inf", ahora)  # las de procesos muertos
        pipe.expire(clave, self._PRESENCIA_S)
        await pipe.execute()

    async def _renovar_presencia(self, session_id: str, token: str) -> None:
        while True:
            await asyncio.sleep(self._PRESENCIA_S / 3)
            try:
                await self._marcar_presencia(session_id, token)
            except Exception:  # un fallo puntual de Redis; la marca caduca sola
                logger.debug(f"[ws] no se pudo renovar la presencia de {session_id}", exc_info=True)

    async def register_task(self, session_id: str, task: "asyncio.Task[Any]") -> None:
        """Asociar una tarea en curso con la sesión.

        Si ya había una tarea registrada (caso de doble query concurrente),
        se sobrescribe — la primera queda huérfana pero no se cancela
        automáticamente para no romper resultados ya en vuelo.
        """
        async with self._lock:
            self.active_tasks[session_id] = task

    async def clear_task(self, session_id: str, task: "asyncio.Task[Any]") -> None:
        """Quitar el registro si coincide con la tarea pasada.

        Comparamos identidad para evitar quitar la tarea de un retry que
        haya tomado el slot mientras tanto.
        """
        async with self._lock:
            current = self.active_tasks.get(session_id)
            if current is task:
                self.active_tasks.pop(session_id, None)

    async def cancelar_en_cualquier_proceso(self, session_id: str) -> bool:
        """«Detener»: la consulta puede estar corriendo en otro worker (F7). True si se canceló aquí
        o se pidió a los demás."""
        return await self.detener(session_id) in ("cancelled", "cancel_requested")

    async def detener(self, session_id: str) -> str:
        """«Detener», con lo que de verdad pasó (F7, auditoría):

        - ``cancelled``: la tarea estaba en este proceso y se canceló;
        - ``cancel_requested``: se pidió por el bus; el proceso que la tenga confirma con un
          ``status: cancelled`` (si ninguno la tenía, no llega confirmación);
        - ``no_active_task``: un solo proceso y sin tarea;
        - ``cancel_failed``: no se pudo pedir (Redis caído). Nunca lanza: el socket sigue vivo.
        """
        if await self.cancel_task(session_id):
            return "cancelled"
        from geo_copilot.platform.estado.bus import BusEnMemoria, bus

        if isinstance(bus(), BusEnMemoria):
            return "no_active_task"
        try:
            await bus().publicar(f"cancelar:{session_id}", {})
        except Exception:  # se dice al cliente; no tumba su WebSocket
            logger.warning(f"[ws] no se pudo pedir la cancelación de {session_id} por el bus", exc_info=True)
            return "cancel_failed"
        return "cancel_requested"

    async def cancel_task(self, session_id: str) -> bool:
        """Cancelar la tarea activa de una sesión, si existe.

        Returns:
            True si encontró y canceló una tarea, False en otro caso.
        """
        async with self._lock:
            task = self.active_tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        """Aceptar una nueva conexión."""
        await websocket.accept()
        token = uuid4().hex
        async with self._lock:
            anterior = self.active_connections.get(session_id)
            self.active_connections[session_id] = websocket
            token_anterior = self._tokens.get(session_id)
            self._tokens[session_id] = token
            # F7 (auditoría): dos handshakes casi a la vez en este proceso — el renovador del
            # primero se cancela (antes quedaba vivo para siempre, con una presencia fantasma). El
            # nuevo se registra en la MISMA sección crítica: si se creara tras un await (el cierre
            # del socket anterior), un tercer handshake concurrente no lo vería y quedaría huérfano.
            renovador_anterior = self._renovadores.pop(session_id, None)
            if self._redis is not None:
                self._renovadores[session_id] = asyncio.create_task(self._renovar_presencia(session_id, token))
        if renovador_anterior is not None:
            renovador_anterior.cancel()
        if anterior is not None and anterior is not websocket:
            await self._cerrar(anterior, session_id)
        if self._redis is not None:
            try:
                if token_anterior:
                    await self._redis.zrem(self._PRESENCIA + session_id, token_anterior)
                async with self._lock:
                    sigue = self._tokens.get(session_id) == token
                # otro handshake lo reemplazó mientras se cerraba el anterior: su token no se marca
                if sigue:
                    await self._marcar_presencia(session_id, token)
            except Exception:  # sin presencia, los avisos de otros workers se pierden (se registra)
                logger.warning(f"[ws] no se pudo registrar la presencia de {session_id}", exc_info=True)
        logger.info(f"WebSocket connected: {session_id}")

    async def disconnect(self, session_id: str, websocket: WebSocket | None = None) -> None:
        """Desconectar un cliente.

        API-12: la versión anterior solo eliminaba la entrada del dict; el
        WebSocket subyacente nunca se cerraba, lo que dejaba descriptores
        a medias en ``send_message`` errors y en shutdown. Ahora cerramos
        el socket explícitamente (con código 1000 = "normal closure").

        F7 (auditoría): con ``websocket``, solo si ESE es el socket registrado de la sesión — el
        cierre tardío de un socket viejo no quita al que lo reemplazó.
        """
        ws: WebSocket | None
        async with self._lock:
            actual = self.active_connections.get(session_id)
            if websocket is not None and actual is not websocket:
                ws, token, renovador = websocket, None, None
            else:
                ws = self.active_connections.pop(session_id, None)
                token = self._tokens.pop(session_id, None)
                renovador = self._renovadores.pop(session_id, None)
        if renovador is not None:
            renovador.cancel()
        if token is not None and self._redis is not None:
            try:
                await self._redis.zrem(self._PRESENCIA + session_id, token)  # solo SU token
            except Exception:  # caduca sola
                logger.debug(f"[ws] no se pudo quitar la presencia de {session_id}", exc_info=True)
        if ws is not None:
            await self._cerrar(ws, session_id)
        logger.info(f"WebSocket disconnected: {session_id}")

    @staticmethod
    async def _cerrar(ws: WebSocket, session_id: str) -> None:
        try:
            await ws.close(code=1000)
        except Exception as exc:  # el socket ya puede estar cerrándose (starlette/uvicorn/websockets lanzan tipos distintos)  # pragma: no cover
            logger.debug(f"WebSocket close failed for {session_id}: {exc}", exc_info=True)

    async def send_message(self, session_id: str, message: WSMessage) -> bool:
        """Enviar mensaje a un cliente específico.

        F7 (S7.1): si su WebSocket no está en ESTE proceso (varios workers/réplicas: la consulta
        o la aprobación entraron por otro), el mensaje va por el bus y lo entrega quien lo tenga.

        F7 (auditoría): nunca lanza — un corte de Redis no tumba un turno ya calculado (devuelve
        False y se registra). Los avisos críticos (aprobación, resultado) van TAMBIÉN por el bus
        aunque haya socket aquí: puede ser uno medio muerto (cambio de red) con la pestaña ya
        conectada en otra réplica; este proceso no lo entrega dos veces.
        """
        carga = message.model_dump(mode="json")
        async with self._lock:
            token = self._tokens.get(session_id)
        entregado = await self._enviar_local(session_id, carga)
        if entregado and message.type not in self._CRITICOS:
            return True
        from geo_copilot.platform.estado.bus import BusEnMemoria, bus

        if isinstance(bus(), BusEnMemoria):
            return entregado  # un solo proceso: si no está aquí, no está
        try:
            await bus().publicar(f"ws:{session_id}", {**carga, "_excluir": token if entregado else None})
        except Exception:  # Redis caído: el aviso se pierde, el turno sigue
            logger.warning(f"[ws] no se pudo publicar un {message.type.value} para {session_id}", exc_info=True)
            return entregado
        return True

    async def _enviar_local(self, session_id: str, carga: dict[str, Any], excluir: str | None = None) -> bool:
        # Obtener websocket de forma thread-safe
        async with self._lock:
            websocket = self.active_connections.get(session_id)
            token = self._tokens.get(session_id)
        if websocket is None or (excluir is not None and token == excluir):
            return False
        try:
            # F7 (auditoría): acotado — un cliente que no lee no retiene al emisor (ni al lector del bus)
            await asyncio.wait_for(websocket.send_json(carga), timeout=self._ENVIO_S)
            return True
        except TimeoutError:
            logger.warning(f"[ws] {session_id} no lee sus mensajes ({self._ENVIO_S:.0f} s): se cierra su socket")
        except Exception as e:  # noqa: BLE001 — transporte caído (WebSocketDisconnect/RuntimeError/OSError) o payload no serializable: se desconecta, nunca rompe al emisor; un cliente que se va es normal, sin traza
            logger.warning(f"Error sending message to {session_id}: {e}")
        await self.disconnect(session_id, websocket)
        return False

    def conectar_bus(self, redis: Any = None) -> None:
        """Entrega aquí lo que otro proceso publicó para un socket de este (F7)."""
        from geo_copilot.platform.estado.bus import bus

        self._redis = redis

        async def _entregar(canal: str, carga: dict[str, Any]) -> None:
            carga = dict(carga)
            excluir = carga.pop("_excluir", None)  # el socket al que el emisor ya se lo entregó
            await self._enviar_local(canal.removeprefix("ws:"), carga, excluir=excluir)

        async def _cancelar(canal: str, _carga: dict[str, Any]) -> None:
            sesion = canal.removeprefix("cancelar:")
            # F7 (auditoría): confirma quien de verdad cancela (el que pidió solo sabe que lo pidió)
            if await self.cancel_task(sesion):
                await self.send_message(sesion, WSMessage(type=WSMessageType.STATUS, data={
                    "status": "cancelled", "message": "Consulta cancelada"}))

        bus().suscribir("ws:", _entregar)
        bus().suscribir("cancelar:", _cancelar)

    async def broadcast(self, message: WSMessage) -> None:
        """Enviar mensaje a todos los clientes conectados."""
        # Copiar conexiones bajo lock para evitar race conditions
        async with self._lock:
            connections = list(self.active_connections.items())

        disconnected = []
        for session_id, websocket in connections:
            try:
                await websocket.send_json(message.model_dump(mode="json"))
            except Exception as e:  # un cliente caído no debe cortar el broadcast al resto
                logger.debug(f"[WebSocket] Failed to send to {session_id}: {e}", exc_info=True)
                disconnected.append(session_id)

        for session_id, websocket in connections:
            if session_id in disconnected:
                await self.disconnect(session_id, websocket)

    async def is_connected(self, session_id: str) -> bool:
        """¿La sesión tiene un WebSocket abierto en ESTE o en OTRO worker? (F7)

        Los `send_*` preguntan esto antes de avisar: con solo la mirada local, un aviso (p. ej. la
        aprobación HITL) generado en un worker para un socket de otro no se enviaba nunca y la
        aprobación expiraba (V5 con 2 workers)."""
        if await self.esta_aqui(session_id):
            return True
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.zcount(self._PRESENCIA + session_id, f"({time.time()}", "+inf"))
        except Exception:  # noqa: BLE001 — sin Redis, no se sabe: se intenta enviar (el bus decide)
            return True

    async def esta_aqui(self, session_id: str) -> bool:
        """¿El socket de la sesión está en ESTE proceso?"""
        async with self._lock:
            return session_id in self.active_connections
