"""Las RESPUESTAS del humano: responder una solicitud (también una que ya no espera nadie)
y los atajos aprobar, rechazar y modificar.

Salió de `HITLManager` (F4 del plan de calidad: hitl.py tenía 662 líneas), tal cual.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger
from geo_copilot.security.hitl_modelos import (
    HITLRequest,
    HITLResponse,
    HITLStatus,
    _auditar,
    _utcnow,
)

if TYPE_CHECKING:
    from geo_copilot.security.hitl import Reanudador

logger = get_logger("geo_copilot.security.hitl")


class HITLRespuestasMixin:
    """Las RESPUESTAS del humano: responder una solicitud (también una que ya no espera nadie)"""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        _MAX_RESPONSES: int
        timeout: int
        _pending_requests: dict[str, HITLRequest]
        _responses: dict[str, HITLResponse]
        _callbacks: dict[str, asyncio.Event]
        _reanudador: Reanudador | None
        async def buscar(self, request_id: str) -> HITLRequest | None: ...

    async def respond(
        self,
        request_id: str,
        status: HITLStatus,
        modified_content: Any | None = None,
        feedback: str = "",
        approved_by: str | None = None
    ) -> bool:
        """
        Responder a una solicitud HITL.

        Args:
            request_id: ID de la solicitud
            status: Estado de la respuesta
            modified_content: Contenido modificado (si aplica)
            feedback: Comentarios del usuario
            approved_by: Usuario que aprobó

        Returns:
            True si la respuesta fue procesada, False si no existe la solicitud
        """
        local = self._pending_requests.get(request_id)
        remota = None if local is not None else await self.buscar(request_id)
        if local is None and remota is None:
            logger.warning(f"HITL request not found: {request_id}")
            return False
        solicitud = local or remota
        assert solicitud is not None
        evento = self._callbacks.get(request_id)
        if evento is not None and evento.is_set():
            logger.info(f"HITL {request_id}: ya respondida (doble clic); esta respuesta no cuenta")
            return False

        # F6: quien decide es el usuario de la petición en curso (REST o WebSocket)
        from geo_copilot.platform.identidad.principal import principal_actual

        quien = principal_actual()
        if approved_by is None and quien is not None:
            approved_by = quien.sub
        await _auditar(
            {HITLStatus.APPROVED: "hitl.aprobar", HITLStatus.REJECTED: "hitl.rechazar",
             HITLStatus.MODIFIED: "hitl.modificar"}.get(status, f"hitl.{status.value}"),
            solicitud, status.value,
            {"motivo": feedback} if feedback else None,
        )

        response = HITLResponse(
            request_id=request_id,
            status=status,
            modified_content=modified_content,
            feedback=feedback,
            approved_by=approved_by,
            approved_at=_utcnow()
        )

        self._responses[request_id] = response

        # S8: aplicar el cap FIFO (antes declarado pero nunca enforced) para
        # acotar la memoria. dict preserva orden de inserción → el primero
        # es el más antiguo.
        while len(self._responses) > self._MAX_RESPONSES:
            oldest = next(iter(self._responses))
            self._responses.pop(oldest, None)

        # Notificar que hay respuesta
        if request_id in self._callbacks:
            self._callbacks[request_id].set()
            return True
        return await self._responder_fuera(solicitud, response)

    async def _responder_fuera(self, solicitud: HITLRequest, response: HITLResponse) -> bool:  # noqa: C901, PLR0912
        """La solicitud la espera OTRO proceso, o ya nadie (reinicio): F7, S7.1.

        F7 (auditoría): la respuesta se guarda durable ANTES de avisar por el bus (pub/sub no guarda
        nada) y solo una por solicitud (la primera; doble clic o dos réplicas: la segunda, False).
        El acuse es inequívoco: el que espera la reclama junto con la respuesta guardada, de forma
        atómica. Si la solicitud desaparece y la respuesta sigue ahí, el que esperaba terminó sin
        aplicarla (expiró, o «Detener»): False, no un falso «aprobada». Si el que esperaba murió (su
        concesión caduca), se trata como huérfana. True solo si la respuesta se aplica (o queda
        guardada para el que espera, que sigue vivo)."""
        from geo_copilot.platform.estado.aprobaciones import (
            CONCESION_S,
            MARGEN_S,
            RENOVAR_S,
            almacen,
        )
        from geo_copilot.platform.estado.bus import bus

        id_ = solicitud.id
        respondida = False
        if await almacen().esperada(id_):
            if not await almacen().responder(id_, response.model_dump(mode="json"), int(self.timeout) + MARGEN_S):
                logger.info(f"HITL {id_}: ya tenía respuesta (doble clic, otra réplica); esta no cuenta")
                return False
            respondida = True
            await bus().publicar("hitl:resp", response.model_dump(mode="json"))
            limite = asyncio.get_running_loop().time() + CONCESION_S + RENOVAR_S
            while asyncio.get_running_loop().time() < limite:
                await asyncio.sleep(0.1)
                if await almacen().respuesta(id_) is None:
                    logger.info(f"HITL {id_} respondida en otro proceso; la recogió el que la espera")
                    return True
                if await almacen().obtener(id_) is None:
                    return await self._sin_aplicar(id_)
                if not await almacen().esperada(id_):
                    break  # el que la esperaba murió sin recogerla: huérfana
            else:
                logger.warning(f"HITL {id_}: el proceso que la espera no la ha recogido aún; queda guardada")
                return True
        # Huérfana: el turno que la pidió murió con su proceso. Quien la borra, la trata.
        dato = await almacen().obtener(id_) or {}
        if not await almacen().resolver(id_):
            if respondida:
                # la reclamó a la vez el que la esperaba (su concesión acababa de caducar)
                return await self._sin_aplicar(id_)
            logger.info(f"HITL {id_}: ya la procesó otra respuesta")
            return False
        if respondida:
            await almacen().descartar_respuesta(id_)
        sesion = solicitud.session_id
        logger.info(f"HITL {id_} respondida huérfana ({response.status.value}); sesión {sesion}")
        if not sesion:
            return True
        aviso = dato.get("aviso") or {}
        meta = solicitud.metadata or {}
        pre = None
        if (response.status in (HITLStatus.APPROVED, HITLStatus.MODIFIED) and aviso.get("content_sha256")
                and meta.get("turno_id") and meta.get("ordinal")):
            # F7 (auditoría): la huella es la de lo que el humano VIO; el turno reanudado solo recibe
            # su respuesta si vuelve a pedir exactamente eso (si no, se le vuelve a preguntar).
            contenido = response.modified_content if response.status == HITLStatus.MODIFIED else aviso.get("content")
            pre = {"tipo": solicitud.action_type.value, "ordinal": int(meta["ordinal"]),
                   "estado": response.status.value, "contenido": contenido, "huella": aviso["content_sha256"]}
        if self._reanudador is not None:
            # quien reanuda decide: solo pre-aprueba si de verdad puede retomar ESE turno
            await self._reanudador(solicitud, response, pre)
        return True

    @staticmethod
    async def _sin_aplicar(id_: str) -> bool:
        """La solicitud ya no está: ¿se llevó el que esperaba nuestra respuesta (acuse), o terminó
        sin ella (expiró, «Detener»)? Reclamar borra las dos a la vez, así que esto es definitivo."""
        from geo_copilot.platform.estado.aprobaciones import almacen

        if await almacen().respuesta(id_) is None:
            logger.info(f"HITL {id_} respondida en otro proceso; la recogió el que la espera")
            return True
        await almacen().descartar_respuesta(id_)
        logger.warning(f"HITL {id_}: el turno que la esperaba terminó sin aplicarla (expiró o se detuvo)")
        return False

    async def approve(
        self,
        request_id: str,
        approved_by: str | None = None,
        feedback: str = ""
    ) -> bool:
        """Aprobar una solicitud HITL."""
        return await self.respond(
            request_id=request_id,
            status=HITLStatus.APPROVED,
            approved_by=approved_by,
            feedback=feedback
        )

    async def reject(
        self,
        request_id: str,
        feedback: str = ""
    ) -> bool:
        """Rechazar una solicitud HITL."""
        return await self.respond(
            request_id=request_id,
            status=HITLStatus.REJECTED,
            feedback=feedback
        )

    async def modify(
        self,
        request_id: str,
        modified_content: Any,
        feedback: str = ""
    ) -> bool:
        """Aprobar con modificaciones una solicitud HITL."""
        return await self.respond(
            request_id=request_id,
            status=HITLStatus.MODIFIED,
            modified_content=modified_content,
            feedback=feedback
        )
