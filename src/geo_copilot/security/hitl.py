"""
Sistema HITL (Human-in-the-Loop) para validación humana.

Gestiona las solicitudes de aprobación para acciones que requieren
supervisión humana antes de ejecutarse.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger

# F4: los modelos (tipos, estados, solicitud, respuesta, digest) viven en hitl_modelos; se
# reexportan aquí porque el resto del código los importa de `hitl`.
from geo_copilot.security.hitl_modelos import (  # noqa: F401
    ApprovedContentMismatch,
    HITLActionType,
    HITLRequest,
    HITLResponse,
    HITLStatus,
    _auditar,
    _utcnow,
    content_digest,
)

logger = get_logger(__name__)

# F4: las respuestas del humano viven en su módulo (mixin).
from geo_copilot.security.hitl_respuestas import HITLRespuestasMixin

# Type for WebSocket notification callback
NotificationCallback = Callable[[str, dict], Awaitable[None]]
#: (solicitud huérfana, respuesta del humano, pre-aprobación para su turno o None)
Reanudador = Callable[["HITLRequest", "HITLResponse", "dict[str, Any] | None"], Awaitable[None]]


class HITLManager(HITLRespuestasMixin):
    """
    Gestor de solicitudes HITL.

    Maneja el flujo de aprobación humana para acciones sensibles,
    incluyendo timeout y expiración de solicitudes.
    """

    # ORC-9: cap del dict de respuestas. Sin tope crecía sin límite (el
    # ``finally`` de ``request_approval`` limpiaba pending y callbacks
    # pero no responses) → fuga de memoria proporcional al tráfico HITL.
    _MAX_RESPONSES = 256

    def __init__(
        self,
        timeout: int | None = None,
        notification_callback: NotificationCallback | None = None
    ):
        """
        Inicializar el gestor HITL.

        ``timeout`` ORC-10: si no se pasa, toma ``settings.hitl_timeout``
        (antes hardcodeaba 300 e ignoraba la config).
        """
        self.timeout = timeout if timeout is not None else get_settings().hitl_timeout
        self._pending_requests: dict[str, HITLRequest] = {}
        self._responses: dict[str, HITLResponse] = {}
        self._callbacks: dict[str, asyncio.Event] = {}
        self._notification_callback = notification_callback
        #: F7 (S7.1): qué hacer cuando se responde una solicitud que ya nadie espera (el proceso
        #: que la pidió se reinició). Lo instala la API: reanuda el turno que la pidió, con lo
        #: aprobado como pre-aprobación de ESA solicitud.
        self._reanudador: Reanudador | None = None
        self._en_bus = False

    def set_reanudador(self, reanudador: Reanudador | None) -> None:
        self._reanudador = reanudador

    def conectar_bus(self) -> None:
        """Recibe las respuestas que llegan a OTRO proceso (varios workers/réplicas, F7)."""
        if self._en_bus:
            return
        from geo_copilot.platform.estado.bus import bus

        async def _respuesta(_canal: str, datos: dict[str, Any]) -> None:
            id_ = str(datos.get("request_id") or "")
            if id_ in self._callbacks:
                self._responses[id_] = HITLResponse.model_validate(datos)
                self._callbacks[id_].set()

        bus().suscribir("hitl:resp", _respuesta)
        self._en_bus = True

    def set_notification_callback(self, callback: NotificationCallback) -> None:
        """Set the notification callback for WebSocket updates."""
        self._notification_callback = callback

    async def request_approval(
        self,
        action_type: HITLActionType,
        title: str,
        description: str,
        details: dict[str, Any] | None = None,
        risks: list[str] | None = None,
        preview: str | None = None,
        session_id: str | None = None
    ) -> HITLResponse:
        """Solicitar aprobación humana para una acción y devolver la decisión del usuario.

        ``details`` lleva lo que se aprueba (SQL, código…), ``risks`` los riesgos identificados,
        ``preview`` una vista previa del resultado y ``session_id`` la sesión WebSocket a notificar.
        """
        from geo_copilot.platform.estado.aprobaciones import MARGEN_S, almacen

        request = self._nueva_solicitud(action_type, title, description, details, risks, preview, session_id)
        aviso = self._datos_aprobacion(request)
        aviso, preaprobada = await self._preaprobada(request, aviso)
        if preaprobada is not None:
            return preaprobada

        # Almacenar solicitud
        self._pending_requests[request.id] = request
        self._callbacks[request.id] = asyncio.Event()
        renovador: asyncio.Task[None] | None = None
        reclamada = False
        # F7 (auditoría): TODO lo que sigue va dentro del try — si se cancela («Detener») o falla
        # Redis mientras se guarda, audita o notifica, el finally para el renovador y limpia.
        try:
            # F7: también fuera del proceso (sobrevive a un reinicio; otro worker puede responderla)
            await almacen().guardar({**request.model_dump(mode="json"), "aviso": aviso},
                                    ttl_s=int(self.timeout) + MARGEN_S)
            await almacen().renovar(request.id)
            renovador = asyncio.create_task(self._mantener_concesion(request.id))

            logger.info(f"HITL request created: {request.id} - {title}")
            # F6 (S6.4): la solicitud queda en la auditoría a nombre de quien la originó
            await _auditar("hitl.solicitar", request, "pendiente")

            # Notificar al frontend (en producción sería websocket/push)
            await self._notify_pending_request(request)
            await self._esperar(request)

            # F7 (auditoría): reclamarla, y con ella la respuesta guardada (ese es el acuse que
            # espera quien respondió desde otro proceso). Si ya no estaba, otro proceso la tomó
            # como huérfana y reanudó el turno: este no ejecuta nada (lo aprobado corre UNA vez,
            # allí). Un fallo de Redis aquí se propaga: no se ejecuta algo sin haberlo reclamado.
            reclamada, guardada = await almacen().reclamar(request.id)
            return self._tras_reclamar(request, reclamada, guardada)

        except TimeoutError:
            reclamada, guardada = await self._respuesta_guardada(request)
            return await self._expirar(request, reclamada, guardada)

        finally:
            await self._limpiar(request, renovador, reclamada)

    def _nueva_solicitud(self, action_type: HITLActionType, title: str, description: str,
                         details: dict[str, Any] | None, risks: list[str] | None, preview: str | None,
                         session_id: str | None) -> HITLRequest:
        """La solicitud, ligada al turno que la pide y a su ordinal dentro de él."""
        from geo_copilot.platform.estado.aprobaciones import turno_actual

        # F7 (auditoría): la solicitud queda ligada al turno que la pide y a su ordinal dentro de
        # él (la 1.ª SQL, la 2.ª…): es lo que identifica «esta petición» al reanudar el turno.
        turno = turno_actual()
        ordinal = turno.siguiente(action_type.value) if turno is not None else None
        return HITLRequest(
            action_type=action_type,
            title=title,
            description=description,
            details=details or {},
            risks=risks or [],
            preview=preview,
            session_id=session_id,
            metadata={"turno_id": turno.id, "ordinal": ordinal} if turno is not None else {},
        )

    async def _preaprobada(self, request: HITLRequest, aviso: dict[str, Any]
                           ) -> tuple[dict[str, Any], HITLResponse | None]:
        """(aviso, respuesta dada antes del reinicio si vale para ESTA solicitud, si no None)."""
        from geo_copilot.platform.estado.aprobaciones import almacen

        turno_id, ordinal = request.metadata.get("turno_id"), request.metadata.get("ordinal")
        pre = (await almacen().tomar_preaprobacion(turno_id, request.action_type.value, ordinal)
               if turno_id is not None and ordinal is not None else None)
        if pre is not None and pre.get("huella") == aviso["content_sha256"]:
            estado = HITLStatus(pre.get("estado") or HITLStatus.APPROVED.value)
            await _auditar("hitl.aprobar", request, estado.value,
                           {"reanudacion": True, "huella_aprobada": pre.get("huella")})
            return aviso, HITLResponse(
                request_id=request.id, status=estado,
                modified_content=pre.get("contenido") if estado == HITLStatus.MODIFIED else None,
                feedback="aprobada por el usuario antes del reinicio", approved_at=_utcnow())
        if pre is not None:
            # F7 (auditoría): el turno reanudado pide OTRA cosa (el LLM regeneró el SQL, o eligió otra
            # herramienta). No se sustituye por lo aprobado — hay consumidores que ignoran
            # `modified_content` (las llamadas MCP) y ejecutarían lo nuevo sin que nadie lo viera, y
            # cambiar en código lo que el LLM decidió no es nuestro papel: se vuelve a preguntar.
            logger.info(f"HITL {request.id}: el turno reanudado pide algo distinto de lo aprobado; se pregunta")
            request.description = (f"{request.description}\n\nAntes del reinicio del servidor aprobaste una "
                                   "versión distinta; al retomar la consulta se generó esta.")
            request.metadata = {**request.metadata, "aprobado_antes": pre.get("contenido")}
            aviso = self._datos_aprobacion(request)
        return aviso, None

    async def _esperar(self, request: HITLRequest) -> None:
        """Esperar la respuesta con timeout (TimeoutError si caduca)."""
        # Esperar respuesta con timeout. F7 (auditoría): esa espera es de la persona, no del
        # sistema: se cuenta aparte (también si caduca o «Detener» la corta) y el turno la descuenta.
        inicio_espera = time.monotonic()
        try:
            await asyncio.wait_for(
                self._callbacks[request.id].wait(),
                timeout=self.timeout
            )
        finally:
            _espera_hitl(time.monotonic() - inicio_espera)

    def _tras_reclamar(self, request: HITLRequest, reclamada: bool, guardada: dict | None) -> HITLResponse:
        """La respuesta reclamada, o EXPIRED si otro proceso la tomó o nadie respondió."""
        response = HITLResponse.model_validate(guardada) if guardada else self._responses.get(request.id)
        if response and reclamada:
            self._responses[request.id] = response
            logger.info(f"HITL request {request.id} resolved: {response.status}")
            _metrica_hitl(response.status.value)
            return response
        if response:
            logger.warning(f"HITL {request.id}: la retomó otro proceso como huérfana; este turno no la ejecuta")
            return HITLResponse(request_id=request.id, status=HITLStatus.EXPIRED,
                                feedback="La aprobación la retomó otro proceso (turno reanudado allí).")

        # Si no hay respuesta, marcar como expirado
        return HITLResponse(
            request_id=request.id,
            status=HITLStatus.EXPIRED,
            feedback="No response received"
        )

    async def _respuesta_guardada(self, request: HITLRequest) -> tuple[bool, dict | None]:
        # F7 (auditoría): la respuesta pudo quedar guardada justo antes del límite (con el aviso
        # del bus perdido y antes del siguiente sondeo): si está, se reclama y vale — quien la
        # dio ya leyó «aprobada». Sin Redis no se sabe: expira (no ejecutar es lo seguro).
        from geo_copilot.platform.estado.aprobaciones import almacen

        reclamada = False
        try:
            reclamada, guardada = await almacen().reclamar(request.id)
        except Exception:  # se registra y expira; el finally reintenta limpiar
            logger.warning(f"HITL {request.id}: no se pudo leer la respuesta guardada al expirar", exc_info=True)
            guardada = None
        return reclamada, guardada

    async def _expirar(self, request: HITLRequest, reclamada: bool, guardada: dict | None) -> HITLResponse:
        """La respuesta guardada justo al límite si se reclamó; si no, la solicitud expira."""
        if reclamada and guardada:
            response = HITLResponse.model_validate(guardada)
            self._responses[request.id] = response
            logger.info(f"HITL request {request.id} resolved al límite: {response.status}")
            _metrica_hitl(response.status.value)
            return response
        logger.warning(f"HITL request {request.id} expired after {self.timeout}s")
        _metrica_hitl("expired")
        await _auditar("hitl.expirar", request, "expirada", {"segundos": self.timeout})
        return HITLResponse(
            request_id=request.id,
            status=HITLStatus.EXPIRED,
            feedback=f"Request expired after {self.timeout} seconds"
        )

    async def _limpiar(self, request: HITLRequest, renovador: asyncio.Task[None] | None,
                       reclamada: bool) -> None:
        # Limpiar pending y callbacks. S8: NO popeamos ``_responses``
        # aquí — el handler REST/WS llama ``get_response`` DESPUÉS de
        # que este coroutine despierta, y popear provocaba una carrera
        # que perdía ``modified_content`` (reportado como null). La
        # respuesta persiste hasta que el cap FIFO ``_MAX_RESPONSES``
        # (aplicado en ``respond``) la desaloje.
        from geo_copilot.platform.estado.aprobaciones import almacen

        self._pending_requests.pop(request.id, None)
        self._callbacks.pop(request.id, None)
        if renovador is not None:
            renovador.cancel()
        if not reclamada:
            try:
                await almacen().resolver(request.id)
            except Exception:  # la clave caduca sola (TTL); no tumba el turno
                logger.warning(f"HITL {request.id}: no se pudo limpiar del almacén", exc_info=True)

    async def _mantener_concesion(self, id_: str) -> None:
        """Renueva la concesión mientras se espera y, de paso, mira el almacén: la respuesta durable
        (por si el aviso del bus se perdió) o la solicitud ya reclamada por otro (F7, auditoría)."""
        from geo_copilot.platform.estado.aprobaciones import RENOVAR_S, almacen

        while True:
            await asyncio.sleep(RENOVAR_S)
            try:
                await almacen().renovar(id_)
                evento = self._callbacks.get(id_)
                if evento is None or evento.is_set():
                    continue
                if (datos := await almacen().respuesta(id_)) is not None:
                    logger.info(f"HITL {id_}: respuesta leída del almacén (el aviso del bus no llegó)")
                    self._responses[id_] = HITLResponse.model_validate(datos)
                    evento.set()
                elif await almacen().obtener(id_) is None:
                    evento.set()  # la reclamó otro proceso: despertar y no ejecutar
            except Exception:  # un fallo puntual de Redis; se reintenta en el siguiente ciclo
                logger.warning(f"HITL {id_}: no se pudo renovar la concesión", exc_info=True)

    async def buscar(self, request_id: str) -> HITLRequest | None:
        """La solicitud pendiente, esté en este proceso, en otro o huérfana tras un reinicio."""
        if request_id in self._pending_requests:
            return self._pending_requests[request_id]
        from geo_copilot.platform.estado.aprobaciones import almacen

        dato = await almacen().obtener(request_id)
        return HITLRequest.model_validate({k: v for k, v in dato.items() if k != "aviso"}) if dato else None

    async def pendientes_de(self, session_id: str) -> list[dict[str, Any]]:
        """Los avisos de aprobación pendientes de una sesión (para re-enviarlos al reconectar)."""
        from geo_copilot.platform.estado.aprobaciones import almacen

        return [d["aviso"] for d in await almacen().pendientes(session_id) if isinstance(d.get("aviso"), dict)]


    def get_pending_requests(self) -> list[HITLRequest]:
        """Obtener todas las solicitudes pendientes."""
        return list(self._pending_requests.values())

    def get_request(self, request_id: str) -> HITLRequest | None:
        """Obtener una solicitud específica."""
        return self._pending_requests.get(request_id)

    def get_response(self, request_id: str) -> HITLResponse | None:
        """Obtener la respuesta de una solicitud procesada."""
        return self._responses.get(request_id)

    async def _notify_pending_request(self, request: HITLRequest) -> None:
        """
        Notificar que hay una nueva solicitud pendiente.

        Envía notificación vía WebSocket si hay callback configurado.
        """
        logger.info(
            f"HITL Notification: {request.action_type} - {request.title}\n"
            f"Description: {request.description}\n"
            f"Risks: {', '.join(request.risks) if request.risks else 'None identified'}"
        )

        # Send WebSocket notification if callback is configured
        if self._notification_callback and request.session_id:
            try:
                await self._notification_callback(request.session_id, self._datos_aprobacion(request))
                logger.info(f"HITL notification sent to session {request.session_id}")
            except Exception as e:
                # Captura amplia a propósito: callback de notificación inyectado; su fallo no debe
                # impedir registrar la aprobación.
                logger.exception(f"Failed to send HITL WebSocket notification: {e}")
        elif not self._notification_callback:
            logger.warning("HITL notification callback not configured")
        elif not request.session_id:
            logger.warning("No session_id for HITL notification")

    def _datos_aprobacion(self, request: HITLRequest) -> dict[str, Any]:
        """Lo que el panel de aprobación muestra (y se guarda para re-enviarlo al reconectar)."""
        # R0.8 (auditoría 2026-07-26, AUD-15): el artefacto viaja ÍNTEGRO. Antes esto era
        # `request.preview or details[...]`, y como `preview` venía truncado a 500 caracteres, el
        # `details["sql"]` completo NUNCA llegaba al panel: el humano aprobaba un fragmento y se
        # ejecutaba otra cosa. `preview` se mantiene como resumen aparte, no como sustituto.
        full_content = request.details.get("sql") or request.details.get("code") or request.preview or ""
        return {
            "approval_id": request.id,
            "content_type": self._get_content_type(request.action_type),
            "content": full_content,
            # Huella de lo que se MUESTRA. El ejecutor la vuelve a calcular sobre lo que va a
            # ejecutar y rechaza si difiere; y es la que se pre-aprueba al reanudar (F7).
            "content_sha256": content_digest(full_content),
            "preview": request.preview,
            "warnings": request.risks,
            "risk_level": self._calculate_risk_level(request.risks),
            "title": request.title,
            "description": request.description,
            "action_type": request.action_type.value,
            "created_at": request.created_at.isoformat(),
            # F7 (auditoría): el turno que la pidió — el cliente sigue su resultado por él
            "turno_id": (request.metadata or {}).get("turno_id"),
            # F7 (auditoría): al retomar un turno que pide algo distinto, lo que se aprobó antes
            **({"aprobado_antes": request.metadata["aprobado_antes"]}
               if (request.metadata or {}).get("aprobado_antes") is not None else {}),
        }

    def _get_content_type(self, action_type: HITLActionType) -> str:
        """Map action type to content type for frontend."""
        mapping = {
            HITLActionType.SQL_EXECUTION: "sql",
            HITLActionType.CODE_EXECUTION: "python",
            HITLActionType.DATA_IMPORT: "data",
            HITLActionType.RECOMMENDATION: "recommendation",
            HITLActionType.EXTERNAL_API: "api",
        }
        return mapping.get(action_type, "unknown")

    def _calculate_risk_level(self, risks: list[str]) -> str:
        """Calculate risk level based on warnings."""
        if not risks:
            return "low"
        high_risk_keywords = ["destructive", "delete", "drop", "write", "update", "insert"]
        for risk in risks:
            if any(kw in risk.lower() for kw in high_risk_keywords):
                return "high"
        if len(risks) >= 3:
            return "medium"
        return "low"


# NOTA: Las funciones helper create_sql_approval_request(), create_data_import_request()
# y sus helpers _identify_sql_risks(), _identify_data_risks() fueron removidas
# porque no se usaban. Se pueden restaurar desde el historial de git si se necesitan.


def _metrica_hitl(estado: str) -> None:
    from geo_copilot.platform.observabilidad import registrar_hitl

    registrar_hitl(estado)


def _espera_hitl(segundos: float) -> None:
    from geo_copilot.platform.observabilidad import registrar_espera_hitl

    registrar_espera_hitl(segundos)
