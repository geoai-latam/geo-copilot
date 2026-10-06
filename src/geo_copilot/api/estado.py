"""Estado compartido del proceso (F7, S7.1): Redis obligatorio en producción, y la reanudación de
un turno que murió con su proceso mientras esperaba una aprobación (E7.1).
"""
from __future__ import annotations

import asyncio
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


class EstadoCompartidoNoDisponible(RuntimeError):
    """Producción sin Redis: varios workers no se verían entre sí y un reinicio perdería las
    aprobaciones pendientes. Se prefiere no arrancar a arrancar «sano» a medias."""


def redis_requerido(config: Any) -> bool:
    from geo_copilot.api.auth import is_production_environment

    return is_production_environment(config)


async def iniciar(config: Any) -> Any | None:
    """Instala bus y almacén de aprobaciones. Devuelve el cliente Redis (o None en memoria)."""
    from geo_copilot.platform.estado import aprobaciones, bus

    quiere_redis = str(getattr(config, "session_backend", "")).lower() == "redis" or redis_requerido(config)
    if not quiere_redis:
        logger.info("[estado] en memoria del proceso (desarrollo): un solo worker, nada sobrevive a un reinicio")
        return None
    try:
        import redis.asyncio as aioredis

        cliente = aioredis.Redis.from_url(str(config.redis_url))
        await cliente.ping()
    except Exception as exc:
        if redis_requerido(config):
            raise EstadoCompartidoNoDisponible(
                f"Inicialización abortada: producción exige Redis ({type(exc).__name__}: {exc}). "
                "Sin él las aprobaciones HITL no sobreviven a un reinicio y los workers no se ven.") from exc
        logger.warning(f"[estado] Redis no disponible ({exc}); estado en memoria del proceso (desarrollo)")
        return None
    bus.instalar(bus.BusEnRedis(cliente))
    await bus.bus().iniciar()
    aprobaciones.instalar(aprobaciones.AprobacionesEnRedis(cliente))
    logger.info("[estado] Redis: bus entre procesos y aprobaciones durables")
    return cliente


async def cerrar() -> None:
    from geo_copilot.platform.estado import bus

    await bus.bus().cerrar()


#: F7 (auditoría): los turnos reanudados en curso. El loop solo guarda referencias débiles a las
#: tareas: sin esto, un turno reanudado podía ser recolectado a mitad de ejecución.
_REANUDADOS: set[asyncio.Task[None]] = set()


async def reanudar_turno(solicitud: Any, respuesta: Any, preaprobacion: dict[str, Any] | None) -> None:
    """Se respondió una aprobación cuando ya nadie la esperaba (el proceso se reinició).

    Aprobada y con SU turno registrado (el id va en la solicitud) → se vuelve a correr ese turno
    como su usuario, con ESE contenido pre-aprobado para esa misma solicitud. En cualquier otro
    caso se le dice al usuario qué pasó. F7 (auditoría): el turno se reclama de forma atómica (se
    reanuda UNA vez aunque respondan dos réplicas) y solo después se pre-aprueba: sin turno que
    retomar no queda ninguna pre-aprobación viva que otra consulta pudiera consumir."""
    from geo_copilot.api.websocket import WSMessage, WSMessageType, connection_manager
    from geo_copilot.platform.estado.aprobaciones import MARGEN_S, almacen
    from geo_copilot.security.hitl import HITLStatus

    session_id = solicitud.session_id
    turno_id = str((solicitud.metadata or {}).get("turno_id") or "")
    turno = await almacen().tomar_turno(turno_id) if turno_id else None
    if turno is not None and turno.get("session_id") not in (None, session_id):
        logger.warning(f"[estado] el turno {turno_id} no es de la sesión {session_id}: no se reanuda")
        turno = None
    consulta = ((turno or {}).get("consulta") or {}).get("query") or ""
    aviso = {"status": "reanudacion", "approval_id": solicitud.id, "turno_id": turno_id or None, "consulta": consulta}
    if respuesta.status not in (HITLStatus.APPROVED, HITLStatus.MODIFIED) or not turno:
        motivo = ("La rechazaste: no se ejecutó nada." if respuesta.status == HITLStatus.REJECTED else
                  "La consulta no se puede retomar: vuelve a enviarla.")
        await connection_manager.send_message(session_id, WSMessage(type=WSMessageType.STATUS, data={
            **aviso, "reanudada": False,
            "message": f"El servidor se reinició mientras esperaba tu aprobación. {motivo}"}))
        return
    if preaprobacion is not None:
        from geo_copilot.api.routes.query import compute_process_timeout
        from geo_copilot.core.config import get_settings

        await almacen().preaprobar(turno_id, preaprobacion, compute_process_timeout(get_settings()) + MARGEN_S)
    tarea = asyncio.create_task(_correr(session_id, turno_id, turno, aviso))
    _REANUDADOS.add(tarea)
    tarea.add_done_callback(_REANUDADOS.discard)


async def _correr(session_id: str, turno_id: str, turno: dict[str, Any], aviso: dict[str, Any]) -> None:
    from fastapi import HTTPException

    from geo_copilot.api.dependencies import get_app_state
    from geo_copilot.api.models import QueryRequest
    from geo_copilot.api.routes.query import (
        ejecutar_consulta,
        entregar_resultado_por_ws,
        turno_observado,
    )
    from geo_copilot.api.websocket import WSMessage, WSMessageType, connection_manager
    from geo_copilot.platform.estado.aprobaciones import almacen
    from geo_copilot.platform.identidad.principal import Principal, fijar_principal

    p = turno.get("principal")
    if p:
        fijar_principal(Principal(sub=p["sub"], org_id=p["org_id"], roles=frozenset(p.get("roles") or ()),
                                  nombre=p.get("nombre") or "", via=p.get("via") or "oidc"))
    await connection_manager.send_message(session_id, WSMessage(type=WSMessageType.STATUS, data={
        **aviso, "reanudada": True,
        "message": "El servidor se reinició mientras esperaba tu aprobación: retomo tu consulta."}))
    estado = get_app_state()
    consulta = aviso.get("consulta") or ""
    try:
        async with turno_observado(session_id, "reanudado") as obs:
            try:
                resp = await ejecutar_consulta(QueryRequest.model_validate(turno["consulta"]), estado.agent_graph,
                                               estado.conversation_manager, reanudada=True, turno_id=turno_id)
                obs["ok"] = resp.status != "failed"
                await entregar_resultado_por_ws(session_id, turno_id, consulta, respuesta=resp, reanudada=True)
            except HTTPException as exc:
                await entregar_resultado_por_ws(session_id, turno_id, consulta, error=str(exc.detail), reanudada=True)
            except Exception as exc:
                logger.error(f"[estado] la reanudación de {session_id} falló: {exc}", exc_info=True)
                await entregar_resultado_por_ws(session_id, turno_id, consulta,
                                                error="No se pudo retomar la consulta.", reanudada=True)
    finally:
        try:
            # lo pre-aprobado que el turno no llegó a pedir (el LLM tomó otro camino) no queda vivo
            await almacen().descartar_preaprobaciones(turno_id)
        except Exception:  # caduca sola (TTL)
            logger.warning(f"[estado] no se pudo descartar la pre-aprobación del turno {turno_id}", exc_info=True)
