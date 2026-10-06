"""
StepFinalizer node — cierre del bucle nativo de multi-step (ORC-5).

Después de que el agente ejecuta el step, este nodo:
1. Inspecciona el state para determinar si el step tuvo éxito.
2. Construye un ``StepResult`` y lo agrega a ``state.step_results``.
3. Detecta casos terminales (cancelación, pending_selection, error
   fatal, plan completado) y los enruta a ``responder``.
4. Si quedan más steps y todo va bien, incrementa
   ``current_step_index`` y rutea de vuelta a ``step_router``.

Antes este trabajo lo hacía el for-loop Python dentro de
``PlanExecutor.execute_plan``. Ahora vive en el grafo compilado.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from geo_copilot.core.colors import log_agent
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Cerrar el step actual y decidir si volver al loop o salir.

    INVARIANTE DE TERMINACIÓN (F3.1): todo camino que vuelve al bucle (es decir,
    cuyo ``route_from_step_finalizer`` devuelve ``step_router``) DEBE incrementar
    ``current_step_index``. Los caminos que NO lo incrementan (pending_selection,
    early-exit) enrutan SIEMPRE a un nodo terminal (``responder``). Así
    ``current_step_index`` crece estrictamente y el bucle termina en ≤ len(plan)
    vueltas. Si refactorizas, preserva esta invariante o el grafo puede colgar.
    """
    plan = state.get("execution_plan") or []
    idx = state.get("current_step_index", 0) or 0
    prev_results = list(state.get("step_results") or [])
    session_id = state.get("session_id", "")

    # B4: ``step_router`` pisó ``query`` con el fragmento del step. Aquí
    # restauramos la consulta original del turno para que el responder y
    # insights razonen sobre lo que pidió el usuario. Es seguro hacerlo
    # incondicionalmente: si quedan más steps, ``step_router`` volverá a
    # pisar ``query`` con el siguiente fragmento.
    restored_query = state.get("original_query") or state.get("query")
    _restore = _restauracion(state)

    # Caso bordo: si entramos sin step válido (plan vacío o ya terminado),
    # no hay nada que finalizar — dejamos pasar al responder.
    if not plan or idx >= len(plan):
        return {
            "current_agent": "step_finalizer",
            "query": restored_query,
            "current_step_index": idx,  # explícito (invariante: idx siempre presente)
            "messages": [],
            **_restore,
        }

    step = plan[idx]
    step_id, description, query_fragment = _datos_del_paso(step, idx)

    # F3.1: ¿este step quedó BLOQUEADO por dependencias no disponibles? Entonces NO se
    # ejecutó (step_router lo enrutó aquí directo): SALTADO honesto, y se avanza.
    from geo_copilot.orchestrator.plan_deps import blocked_dependencies
    blocked = blocked_dependencies(step, idx, plan, prev_results)
    if blocked:
        return await _paso_saltado(blocked, step_id, description, query_fragment, idx, plan,
                                   prev_results, session_id, restored_query, _restore)

    step_success, step_error, pending_selection, has_geojson = _evaluar_paso(state)
    new_step_results = prev_results + [_registrar(step_id, description, query_fragment, step_success,
                                                  step_error, pending_selection, has_geojson, idx, plan)]

    # WS: notificar fin del step.
    status_str = "completed" if step_success else ("skipped" if pending_selection else "failed")
    await _notificar(session_id, idx, len(plan), description or query_fragment[:50], status_str,
                     {"success": step_success, "skipped": pending_selection, "error": step_error})

    # Decidir terminación.
    updates: dict = {
        "current_agent": "step_finalizer",
        "step_results": new_step_results,
        "query": restored_query,  # B4: restaurar query original del turno
        "messages": [],
        **_restore,  # ORQ-11: restaurar external_geojson aislado por step_router
    }

    # Pausa por selección pendiente — construir el mensaje al usuario y salir.
    if pending_selection:
        updates.update(_pausa_por_seleccion(plan, idx, state.get("found_services") or []))
        return updates

    # Cancelación o error fatal — terminar el plan.
    if state.get("cancelled"):
        logger.info("[StepFinalizer] cancelado por el usuario — saliendo")
        return updates

    return _avanzar(updates, step_success, step_id, idx, plan)


def _restauracion(state: GraphState) -> dict:
    """ORQ-11 (auditoría): restaurar los datos externos que step_router aisló para
    un paso query_database (guardados en _saved_*). Antes se anulaban y NUNCA se
    restauraban → se perdían para el resto del plan. Se limpia _saved_* para no
    re-restaurar. Si no hubo aislamiento, _saved es None y no cambia nada."""
    if state.get("_saved_external_geojson") is not None:
        return {
            "external_geojson": state.get("_saved_external_geojson"),
            "has_external_data": state.get("_saved_has_external_data", False),
            "_saved_external_geojson": None,
            "_saved_has_external_data": False,
        }
    return {}


def _datos_del_paso(step: object, idx: int) -> tuple[str, str, str]:
    """(step_id, description, query_fragment) de un paso, sea dict u objeto."""
    if isinstance(step, dict):
        return (step.get("step_id", f"step_{idx + 1}"), step.get("description", ""),
                step.get("query_fragment", ""))
    return (getattr(step, "step_id", f"step_{idx + 1}"), getattr(step, "description", ""),
            getattr(step, "query_fragment", ""))


async def _notificar(session_id: str, idx: int, total: int, action: str, status: str, result: dict) -> None:
    """WS: el progreso del step (si notificar falla, el plan sigue)."""
    try:
        from geo_copilot.platform import events
        if session_id:
            await events.sink().step_progress(
                session_id=session_id,
                step_index=idx,
                total_steps=total,
                action=action,
                status=status,
                result=result,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[StepFinalizer] WS notify failed: {exc}")


async def _paso_saltado(blocked: list, step_id: str, description: str, query_fragment: str, idx: int,
                        plan: list, prev_results: list, session_id: str, restored_query: object,
                        _restore: dict) -> dict:
    """F3.1: el step NO se ejecutó (dependencias no disponibles). Se registra como SALTADO
    honesto y se avanza — sin inspeccionar el state (trae datos del step anterior, no de éste)."""
    skip_msg = (
        f"Saltado: depende de {', '.join(blocked)}, que no produjo resultados."
    )
    skipped_entry = {
        "step_id": step_id,
        "description": description,
        "query_fragment": query_fragment,
        "success": False,
        "skipped": True,
        "error": None,
        "skip_message": skip_msg,
        "pending_selection": False,
        "retry_count": 0,
    }
    new_results = prev_results + [skipped_entry]
    log_agent(
        "StepFinalizer",
        f"Step {idx + 1}/{len(plan)} SALTADO",
        f"id={step_id}, deps_no_disp={blocked}",
    )
    logger.info(f"[StepFinalizer] step {step_id} saltado por deps {blocked}")
    await _notificar(session_id, idx, len(plan), description or query_fragment[:50], "skipped",
                     {"success": False, "skipped": True, "error": skip_msg})
    return {
        "current_agent": "step_finalizer",
        "step_results": new_results,
        "query": restored_query,
        "current_step_index": idx + 1,  # avanzar; el loop sigue con el resto
        "error": None,  # un skip no es un error fatal
        "messages": [],
        **_restore,
    }


def _evaluar_paso(state: GraphState) -> tuple[bool, object, bool, bool]:
    """El resultado del step según el state que dejó el agente:
    (éxito, error, selección pendiente, trajo geojson)."""
    has_error = bool(state.get("error"))
    has_geojson = bool(state.get("geojson") or state.get("external_geojson"))
    has_raw_data = bool(state.get("raw_data"))
    has_symbology = bool(state.get("symbology"))
    has_response = bool(state.get("final_response"))
    # R1.2: el canal ANALÍTICO (intent analyze / spatial_operation con salida
    # tabla/stats/chart) escribe en `visualization`, no en geojson/raw_data.
    # Antes este juicio era ciego a ese canal y un paso analítico exitoso se
    # marcaba "no produjo output útil". Solo cuenta para los intents de
    # sandbox: step_router limpia la visualization heredada al inicio de esos
    # pasos, así que si está presente aquí la produjo ESTE paso.
    # imagery escribe SU visualization (tabla NDVI/cambio) — step_router no la
    # limpia porque el nodo la sobreescribe siempre, así que su presencia con
    # intent=imagery es output fresco de ESTE paso. Sin esto, al no fijar el nodo
    # `final_response` en multi-paso (M6), el paso quedaba "sin output útil".
    has_analysis = bool(state.get("visualization")) and state.get("intent") in (
        "analyze", "spatial_operation", "imagery",
    )
    found_services = state.get("found_services") or []
    new_search = bool(state.get("new_search_executed"))

    # ``pending_selection``: el step trajo una lista de servicios pero el
    # usuario aún no eligió uno. Pausamos el plan exactamente como antes.
    pending_selection = bool(new_search and found_services and not has_geojson)

    if pending_selection:
        return False, None, True, has_geojson  # no es error técnico; el plan se pausa.
    if has_error:
        return False, state.get("error"), False, has_geojson
    step_success = (
        has_geojson or has_raw_data or has_symbology or has_response
        or has_analysis
    )
    return step_success, (None if step_success else "Step no produjo output útil"), False, has_geojson


def _registrar(step_id: str, description: str, query_fragment: str, step_success: bool, step_error: object,
               pending_selection: bool, has_geojson: bool, idx: int, plan: list) -> dict:
    """La entrada de ``step_results`` del step ejecutado, y su rastro en el log."""
    step_result_entry = {
        "step_id": step_id,
        "description": description,
        "query_fragment": query_fragment,
        "success": step_success,
        "skipped": False,
        "error": step_error,
        "pending_selection": pending_selection,
        "retry_count": 0,
    }
    log_agent(
        "StepFinalizer",
        f"Step {idx + 1}/{len(plan)} {'OK' if step_success else 'FAIL'}",
        f"id={step_id}, has_geojson={has_geojson}, error={bool(step_error)}",
    )
    logger.info(
        f"[StepFinalizer] step_id={step_id}, success={step_success}, "
        f"pending_selection={pending_selection}, error={step_error!r}"
    )
    return step_result_entry


def _pausa_por_seleccion(plan: list, idx: int, found_services: list) -> dict:
    """El mensaje para que el usuario elija un servicio, y los pasos que quedan pendientes."""
    remaining = plan[idx + 1:]
    pending_queries: list[str] = []
    pending_operations: list[dict] = []
    for s in remaining:
        if isinstance(s, dict):
            pending_queries.append(s.get("query_fragment", ""))
            pending_operations.append({
                "step_id": s.get("step_id"),
                "description": s.get("description"),
                "query": s.get("query_fragment", ""),
            })
        else:
            pending_queries.append(getattr(s, "query_fragment", ""))
            pending_operations.append({
                "step_id": getattr(s, "step_id", None),
                "description": getattr(s, "description", ""),
                "query": getattr(s, "query_fragment", ""),
            })

    services_list = "**Servicios encontrados:**\n" + "\n".join(
        f"  **{i + 1}.** {svc.get('name', svc.get('title', 'Sin nombre'))}"
        for i, svc in enumerate(found_services)
    )
    example = (
        f"carga el 2, {', '.join(q for q in pending_queries if q)}"
        if pending_queries else "carga el 2"
    )
    pause_msg = (
        f"{services_list}\n\n"
        f"**Para continuar, escribe un comando como:**\n"
        f"`{example}`\n\n"
        f"_(Reemplaza '2' por el número del servicio que deseas)_"
    )
    logger.info(
        f"[StepFinalizer] plan pausado: {len(pending_operations)} steps pendientes"
    )
    return {
        "plan_paused": True,
        "pending_operations": pending_operations,
        "final_response": pause_msg,
        # No avanzamos idx: ``plan_paused`` es TERMINAL en esta corrida del
        # grafo (route → responder). El reanudado ocurre en un /query nuevo
        # que re-inyecta ``pending_operations``, no re-entrando aquí.
        "current_step_index": idx,
    }


def _avanzar(updates: dict, step_success: bool, step_id: str, idx: int, plan: list) -> dict:
    """F3.1: un fallo de step ya NO aborta el plan. Registramos el fallo y
    avanzamos: los steps que dependan de éste se saltarán (blocked_dependencies);
    los independientes siguen ejecutándose. Limpiamos ``error`` del state para
    que no se interprete como fallo fatal (el fallo vive en step_results y el
    responder arma el desglose parcial). El abort total era demasiado brusco:
    mataba ramas independientes por el fallo de una sola."""
    if not step_success:
        logger.warning(
            f"[StepFinalizer] step {step_id} FALLÓ — continúo con el resto del "
            f"plan; los dependientes se saltarán (quedan {len(plan) - idx - 1})"
        )
        updates["error"] = None  # el fallo vive en step_results, no es fatal

    # Avanzar el índice (éxito o fallo). El conditional edge rutea a step_router
    # si quedan steps, o a responder si el plan terminó.
    new_idx = idx + 1
    updates["current_step_index"] = new_idx
    if new_idx < len(plan):
        logger.info(f"[StepFinalizer] avanzando a step {new_idx + 1}/{len(plan)}")
    else:
        logger.info(f"[StepFinalizer] plan completado: {len(plan)} pasos")
    return updates


def route_from_step_finalizer(state: GraphState) -> str:
    """Conditional edge desde ``step_finalizer``.

    - ``responder`` si: cancelado, plan pausado, o plan terminado.
    - ``step_router`` si: quedan más steps por ejecutar/saltar.

    F3.1: un fallo (o salto por dependencia) de un step ya NO termina el plan.
    El bucle continúa hasta procesar TODOS los steps; los dependientes del que
    falló se saltarán (``blocked_dependencies``) y las ramas independientes
    siguen. El desglose éxito/fallo/saltado lo arma el responder desde
    ``step_results``. ``state.error`` lo limpia ``step_finalizer`` tras un fallo,
    así que aquí solo es terminal un error fatal externo al loop.
    """
    if state.get("cancelled") or state.get("plan_paused") or state.get("error"):
        return "responder"

    plan = state.get("execution_plan") or []
    idx = state.get("current_step_index", 0) or 0

    if idx >= len(plan):
        return "responder"

    return "step_router"
