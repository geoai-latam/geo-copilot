"""HITL vía LangGraph interrupt() + checkpointer (Fase 4 / F4.2) — SPIKE.

QUÉ ES Y QUÉ NO ES (decisión de diseño, validada con un workflow de diseño):

Esto es un SPIKE flag-gated, NO el cutover de producción. Provee y PRUEBA el
mecanismo de HITL nativo de LangGraph (``interrupt()`` para pausar el grafo +
``Command(resume=...)`` para reanudar desde un checkpoint), con la misma semántica
de guardrail que el HITL actual (approve / reject / modify). Reusa los modelos
existentes ``HITLResponse``/``HITLStatus`` — no inventa modelos nuevos.

POR QUÉ ESTÁ DIFERIDO EL CUTOVER COMPLETO (no es pereza; es seguridad):
  1. El HITL actual NO es busy-wait: ``process()`` es una corutina de larga vida
     que suspende en un ``asyncio.Event`` mientras un ``POST /approval`` separado
     (mismo proceso, mismo HITLManager) lo resuelve. Ya soporta approve/reject/
     modify/timeout para el camino normal Y el ReAct. Funciona y tiene 11 tests.
  2. ``MemorySaver`` (en memoria) no da NINGUNA durabilidad sobre ese Event; el
     único beneficio real de interrupt+checkpointer es la suspensión durable
     entre reinicios, que requiere un checkpointer persistente (Postgres), no
     MemorySaver.
  3. Hay 9 sitios de HITL (nodo gis_agent + 8 en ``agents/``), no 1. Migrar uno
     y voltear un flag global dejaría 8 en blocking → inconsistencia silenciosa.
  4. ``interrupt()`` RE-EJECUTA el nodo desde el principio al reanudar. El nodo
     gis_agent genera el SQL ANTES del HITL; al reanudar regeneraría el SQL (no
     determinista) y aprobaría algo distinto de lo mostrado. Retrofitear su
     closure ``_attempt``/RetryExecutor es delicado y requiere persistir el SQL
     en el estado checkpointeado primero.
  5. El estado mutable de ReAct/Plan (``working_state`` en react_tools) NO es
     estado del grafo y se PIERDE al reanudar por interrupt.
  6. El contrato de respuesta (``await asyncio.wait_for(process())`` → send_result)
     se rompe si ``process()`` retorna temprano con ``__interrupt__``.

Por eso: el flag ``hitl_mode`` default 'blocking' (kill-switch), el nodo de
producción intacto, y aquí solo las primitivas + su prueba aislada. El cutover
(9 sitios, idempotencia de re-ejecución, checkpointer durable, contrato de
respuesta) es una fase dedicada.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.core.logging import get_logger
from geo_copilot.security.hitl import HITLResponse, HITLStatus

logger = get_logger(__name__)


def raise_hitl_interrupt(payload: dict[str, Any]) -> Any:
    """Pausa el grafo pidiendo aprobación humana (LangGraph ``interrupt()``).

    ``payload`` describe la acción sensible para el frontend (sql/code, riesgos,
    preview, etc.). Al reanudar con ``Command(resume=value)``, ESTA función
    devuelve ``value`` — normalízalo con ``extract_resume``.

    Importe local de ``langgraph`` para no acoplar el resto del código a su API
    (insulación ante drift de versión).
    """
    from langgraph.types import interrupt
    return interrupt(payload)


def extract_resume(value: Any) -> HITLResponse:
    """Normaliza el valor de reanudación a un ``HITLResponse``.

    Acepta un ``HITLResponse``, un dict (``status``/``modified_content``/
    ``feedback``), o un string de estado. Ante algo inesperado, degrada a
    ``EXPIRED`` (honesto: no aprueba por defecto una acción sensible).
    """
    if isinstance(value, HITLResponse):
        return value
    if isinstance(value, dict):
        raw_status = value.get("status", "")
        try:
            status = HITLStatus(str(raw_status))
        except ValueError:
            status = HITLStatus.EXPIRED
        return HITLResponse(
            request_id=str(value.get("request_id", "")),
            status=status,
            modified_content=value.get("modified_content"),
            feedback=str(value.get("feedback", "")),
        )
    try:
        return HITLResponse(request_id="", status=HITLStatus(str(value)))
    except ValueError:
        # No aprobar por defecto algo sensible si la reanudación es ambigua.
        return HITLResponse(request_id="", status=HITLStatus.EXPIRED)


async def request_hitl_decision(
    graph: Any,
    settings: Any,
    *,
    action_type: Any,
    title: str,
    description: str,
    details: dict | None = None,
    risks: list | None = None,
    preview: str | None = None,
    session_id: str | None = None,
) -> HITLResponse:
    """Pide la decisión HITL por el mecanismo CONFIGURADO (gateway unificado).

    - ``hitl_mode='interrupt'``: pausa el grafo (LangGraph ``interrupt()``) con el
      payload; al reanudar (``Command(resume=...)``) devuelve la decisión. SOLO
      válido corriendo dentro del grafo compilado con checkpointer.
    - ``'blocking'`` (default): el ``HITLManager`` bloqueante de siempre.

    Misma firma/semántica para ambos → el sitio de HITL no cambia su lógica
    downstream (REJECTED/EXPIRED/MODIFIED).
    """
    if getattr(settings, "hitl_mode", "blocking") == "interrupt":
        payload = {
            "action_type": getattr(action_type, "value", str(action_type)),
            "title": title,
            "description": description,
            "details": details or {},
            "risks": risks or [],
            "preview": preview,
            "session_id": session_id,
        }
        respuesta: HITLResponse = extract_resume(raise_hitl_interrupt(payload))
        return respuesta
    aprobacion: HITLResponse = await graph.hitl_manager.request_approval(
        action_type=action_type,
        title=title,
        description=description,
        details=details,
        risks=risks,
        preview=preview,
        session_id=session_id,
    )
    return aprobacion


def build_checkpointer(settings: Any):
    """Checkpointer para ``hitl_mode='interrupt'``.

    v1: ``MemorySaver`` (in-process) — habilita el resume dentro del mismo proceso
    (el /query pausa y retorna, el /approval reanuda), que es el caso primario del
    HITL. ``hitl_checkpointer='postgres'`` es el hook de DURABILIDAD entre
    reinicios; su wiring (pool async + setup) es follow-up — por ahora cae a
    MemorySaver con aviso para no romper.
    """
    from langgraph.checkpoint.memory import MemorySaver

    if getattr(settings, "hitl_checkpointer", "memory") == "postgres":
        logger.warning(
            "[hitl] checkpointer 'postgres' aún no cableado (requiere pool async "
            "durable) → usando MemorySaver in-process. Durabilidad entre reinicios "
            "es follow-up."
        )
    return MemorySaver()
