"""
StepRouter node — entrada del bucle nativo de multi-step (ORC-5, 2026-05-30).

Antes el ``PlanExecutor`` corría un for-loop Python que para cada step:
1. Llamaba a ``_node("<x>").run(graph, state)`` saltándose el grafo
   compilado de LangGraph.
2. Re-implementaba el dispatch ``action_type → agente``, duplicando la
   lógica del routing del grafo principal.

Ahora el bucle vive dentro del grafo compilado. ``step_router`` es el
entry-point del loop: lee el step actual del plan, prepara el estado
(query, intent, fuente de datos activa), y deja que el routing
condicional del grafo dispatchee al agente apropiado. ``step_finalizer``
cierra cada vuelta y decide si volver o salir.

Resultado:
- El routing del executor pasa por LangGraph (telemetría/WS uniformes).
- ``RetryExecutor`` y ``_run_with_progress`` se aplican automáticamente
  en los nodos de agente cuando éstos los implementan.
- La lógica de dispatch (action_type → intent) existe en UN solo lugar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from geo_copilot.core.colors import log_agent
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


# Mapeo de action_type del plan → intent del state. El intent es lo que
# los nodos de agente downstream leen para decidir su comportamiento.
# `select_service` y `search_external` mapean a sus respectivos intents
# para que `data_agent.run` actúe igual que cuando viene del router.
#
# Smart Router (2026-05-31): el action_type ``symbology`` del planner
# mapea al intent ``apply_symbology`` — convergente con el flujo single-
# step iniciado por el router. ``step_finalizer`` y los routing edges
# reconocen ese intent y dispatchan a ``symbology_agent`` directamente.
_ACTION_TO_INTENT = {
    "query_database": "query_data",
    "spatial_operation": "spatial_operation",
    "analyze": "analyze",
    "symbology": "apply_symbology",
    "apply_symbology": "apply_symbology",
    "search_external": "search_external",
    "select_service": "select_service",
    # A5: paso-pregunta. TERMINAL — pausa el plan con la pregunta al usuario.
    "ask_user": "clarify",
}

# ORQ-04: ordinales españoles → número, para resolver un select_service dentro
# de un plan multi-paso ("carga el segundo"). Es parsing determinista de un
# número que el usuario YA dio (el QUÉ-cargar ya lo decidió el planner al emitir
# el paso); no es juicio suplantando al LLM.
_ORDINALS = {
    "primer": 1, "primero": 1, "primera": 1,
    "segundo": 2, "segunda": 2,
    "tercer": 3, "tercero": 3, "tercera": 3,
    "cuarto": 4, "cuarta": 4,
    "quinto": 5, "quinta": 5,
}


def _extract_service_number(fragment: str, n_services: int) -> int | None:
    """Número de servicio (1..N) referido en el fragmento del step, o None."""
    import re
    frag = (fragment or "").lower()
    m = re.search(r"\d+", frag)
    if m:
        return int(m.group())
    if "ultim" in frag and n_services:  # "último"/"última"
        return n_services
    for word, num in _ORDINALS.items():
        if word in frag:
            return num
    return None


def _select_service_failure(idx: int, step_id: str, description: str, msg: str) -> dict:
    """Paso select_service no resoluble → fallo HONESTO (no 'validado' en
    silencio). Mismo mecanismo que un action_type desconocido: intent no
    ruteable → step_finalizer registra el fallo y salta los pasos dependientes."""
    return {
        "current_agent": "step_router",
        "current_step_index": idx,
        "intent": "invalid_action_type",
        "error": f"{msg} (paso '{description or step_id}')",
    }


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Preparar el estado para el step actual del plan multi-paso.

    Lee ``state.execution_plan[state.current_step_index]`` y configura
    el estado para que el siguiente nodo del grafo (decidido por
    ``_route_from_step_router`` en graph.py) ejecute el step. NO ejecuta
    nada por sí mismo — la ejecución es responsabilidad de los nodos de
    agente, igual que en single-step.

    Emite ``step_progress(started)`` al WS antes de salir.
    """
    plan = state.get("execution_plan") or []
    idx = state.get("current_step_index", 0) or 0
    session_id = state.get("session_id", "")

    # Estados terminales que el caller del loop ya marcó — no hacemos
    # nada y dejamos que ``_route_from_step_router`` mande a responder.
    if state.get("cancelled"):
        logger.info("[StepRouter] cancelled flag set — terminando loop")
        return {"current_agent": "step_router"}

    if not plan or idx >= len(plan):
        logger.info(
            f"[StepRouter] plan completado o vacío "
            f"(idx={idx}, plan_len={len(plan)}) — terminando loop"
        )
        return {"current_agent": "step_router"}

    step = plan[idx]
    # Normalizar el step: el planner puede devolver dataclass PlanStep o dict.
    # R2.5: sin default 'general' — un action_type ausente se trata igual que
    # uno desconocido (paso fallido honesto más abajo).
    if isinstance(step, dict):
        step_id = step.get("step_id", f"step_{idx + 1}")
        query_fragment = step.get("query_fragment", "")
        action_type = step.get("action_type")
        description = step.get("description", "")
        numero_decidido = step.get("service_number")
    else:
        step_id = getattr(step, "step_id", f"step_{idx + 1}")
        query_fragment = getattr(step, "query_fragment", "")
        action_type = getattr(step, "action_type", None)
        description = getattr(step, "description", "")
        numero_decidido = getattr(step, "service_number", None)

    # F3.1: ¿alguna dependencia de este step falló/se saltó? Si sí, NO lo
    # ejecutamos sobre datos obsoletos: lo dejamos pasar a step_finalizer, que
    # registra el "saltado" honesto y avanza. (route_from_step_router recalcula
    # lo mismo y rutea a step_finalizer.)
    from geo_copilot.orchestrator.plan_deps import blocked_dependencies
    blocked = blocked_dependencies(step, idx, plan, state.get("step_results"))
    if blocked:
        logger.info(
            f"[StepRouter] step {step_id} BLOQUEADO por dependencias no "
            f"disponibles {blocked} — se saltará (no se ejecuta sobre datos viejos)"
        )
        return {"current_agent": "step_router"}

    log_agent(
        "StepRouter",
        f"Step {idx + 1}/{len(plan)}: {description[:60]}",
        f"action_type={action_type}",
    )
    logger.info(
        f"[StepRouter] step_id={step_id}, action_type={action_type}, "
        f"query_fragment={query_fragment[:80]!r}"
    )

    # WS: notificar inicio del step.
    try:
        from geo_copilot.platform import events
        if session_id:
            await events.sink().step_progress(
                session_id=session_id,
                step_index=idx,
                total_steps=len(plan),
                action=description or query_fragment[:50],
                status="started",
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[StepRouter] WS notify failed: {exc}")

    # Preparar el state para que el agente downstream actúe sobre este
    # step. La query del step PISA la query original del turno.
    #
    # R2.5: un action_type fuera del vocabulario NO se adivina como
    # query_data (eso convertía "no sé qué acción es" en "consulta la BD" en
    # silencio). Se marca el paso como fallido honesto: el error queda en el
    # state, route_from_step_router manda a step_finalizer (intent
    # inesperado), y el finalizer registra el fallo con este mensaje; los
    # pasos dependientes se saltan por el mecanismo normal.
    intent = _ACTION_TO_INTENT.get(action_type or "")
    if intent is None:
        logger.warning(
            f"[StepRouter] step {step_id} con action_type desconocido "
            f"{action_type!r} — paso fallido honesto (no se adivina query_data)"
        )
        return {
            "current_agent": "step_router",
            "current_step_index": idx,
            "intent": "invalid_action_type",
            "error": (
                f"Paso no ejecutable: tipo de acción desconocido "
                f"{action_type!r} (paso '{description or step_id}')"
            ),
        }
    # A5: `ask_user` es TERMINAL — la pregunta va al usuario y el plan se
    # PAUSA aquí (mismo mecanismo que pending_selection: plan_paused +
    # pending_operations con los pasos restantes; el responder entrega la
    # pregunta tal cual, sin narrar fallos).
    if intent == "clarify":
        remaining = plan[idx + 1:]
        pending_operations = [
            {
                "step_id": (s.get("step_id") if isinstance(s, dict)
                            else getattr(s, "step_id", None)),
                "description": (s.get("description") if isinstance(s, dict)
                                else getattr(s, "description", "")),
                "query": (s.get("query_fragment") if isinstance(s, dict)
                          else getattr(s, "query_fragment", "")),
            }
            for s in remaining
        ]
        logger.info(
            f"[StepRouter] step {step_id} ask_user — plan pausado con pregunta "
            f"({len(pending_operations)} pasos pendientes)"
        )
        return {
            "current_agent": "step_router",
            "current_step_index": idx,
            "intent": "clarify",
            "final_response": query_fragment or description,
            "plan_paused": True,
            "pending_operations": pending_operations or None,
            "error": None,
        }

    # ORQ-04: select_service dentro de un plan multi-paso. El router single-step
    # y el bucle ReAct resuelven el número→URL antes de despachar a data_agent;
    # aquí, sin esa resolución, data_agent.run(intent=select_service) no recibía
    # external_url y caía a "validar la conexión de BD" en silencio (paso marcado
    # como ok SIN cargar nada → los pasos dependientes del geojson se saltaban).
    # Resolvemos el número contra found_services y despachamos como load_external
    # (que data_agent SÍ maneja) — misma semántica que ReAct.
    external_url_override: str | None = None
    if intent == "select_service":
        services = state.get("found_services") or []
        if not services:
            return _select_service_failure(
                idx, step_id, description,
                "No hay una lista de servicios de una búsqueda previa para "
                "seleccionar en este paso")
        # El número lo decide el planner (campo estructurado); el texto solo si no lo dio (planes
        # antiguos guardados en sesión).
        num = (numero_decidido if isinstance(numero_decidido, int) and not isinstance(numero_decidido, bool)
               else _extract_service_number(query_fragment, len(services)))
        if num is None:
            return _select_service_failure(
                idx, step_id, description,
                f"No identifiqué el número del servicio a cargar en "
                f"'{query_fragment[:60]}'")
        if not (1 <= num <= len(services)):
            return _select_service_failure(
                idx, step_id, description,
                f"El número {num} excede los {len(services)} servicios "
                f"encontrados (elige entre 1 y {len(services)})")
        url = (services[num - 1] or {}).get("url")
        if not url:
            return _select_service_failure(
                idx, step_id, description,
                f"El servicio #{num} no tiene una URL cargable")
        external_url_override = url
        intent = "load_external"  # data_agent carga por URL
        logger.info(
            f"[StepRouter] select_service #{num} → load_external {url}"
        )

    updates: dict = {
        "current_agent": "step_router",
        "query": query_fragment,
        "intent": intent,
        "current_step_index": idx,  # idempotente, pero explícito
        # Limpiar error/last_error del step previo — cada step empieza limpio.
        "error": None,
    }
    if external_url_override:
        updates["external_url"] = external_url_override

    # F3.1 (revisión adversarial): los pasos que GENERAN datos frescos
    # (query/search/select) empiezan con pizarra limpia de OUTPUT. Sin esto, el
    # geojson/raw_data de un paso anterior persistía y step_finalizer lo contaba
    # como output de ESTE paso (éxito falso); p. ej. un search-only quedaba como
    # "éxito" mostrando el mapa del paso previo, o no disparaba pending_selection.
    # Los pasos que TRANSFORMAN (spatial_operation/apply_symbology) CONSUMEN el
    # geojson de entrada, así que a ésos NO se les limpia.
    if action_type in ("query_database", "search_external", "select_service"):
        updates["geojson"] = None
        updates["raw_data"] = None
        updates["sql"] = None
        updates["symbology"] = None
        updates["layer_name"] = None
        # R1.2: datos frescos invalidan el análisis previo — sin esto, la
        # visualization de un paso analítico anterior contaría como output
        # de ESTE paso (éxito falso) y llegaría rancia al responder.
        updates["visualization"] = None
        updates["data"] = None

    # R1.2: los pasos de sandbox (spatial/analyze) producen SU PROPIA
    # visualization; se limpia la heredada para que el juicio de éxito del
    # finalizer mida lo que ESTE paso produjo (el geojson de entrada se
    # conserva — es su insumo). symbology NO limpia nada: no produce
    # visualization y la del análisis previo debe sobrevivir hasta el
    # responder (p.ej. "trae, analiza y colorea").
    if action_type in ("spatial_operation", "analyze"):
        updates["visualization"] = None
        updates["data"] = None

    # query_database opera SIEMPRE sobre la BD interna: si hay datos
    # externos cargados, los aislamos para este step (sin perderlos del state
    # global — se GUARDAN aquí y el step_finalizer los RESTAURA, ORQ-11).
    if action_type == "query_database":
        if state.get("external_geojson") is not None:
            updates["_saved_external_geojson"] = state.get("external_geojson")
            updates["_saved_has_external_data"] = state.get("has_external_data", False)
        updates["external_geojson"] = None
        updates["has_external_data"] = False
        updates["active_data_source"] = "internal"

    # spatial_operation NECESITA un geojson sobre el que operar. Si la
    # fuente activa es interna, dejamos el geojson del state intacto;
    # si es externa, dejamos external_geojson intacto. El python_agent
    # node sabe leer ambos.

    logger.info(
        f"[StepRouter] step {step_id} → intent={intent}, "
        f"active_source={updates.get('active_data_source', state.get('active_data_source'))}"
    )
    return updates


def route_from_step_router(state: GraphState) -> str:
    """Conditional edge desde ``step_router`` al agente apropiado.

    Lee ``intent`` del state (que ``step_router`` acaba de setear) y
    devuelve el nombre del nodo destino. Reusa la misma semántica que
    ``_route_from_router`` para que no haya divergencia de routing.

    Casos terminales (plan completo / cancelado) van directo a
    ``step_finalizer`` que decidirá si ir a responder o reintentar.
    """
    plan = state.get("execution_plan") or []
    idx = state.get("current_step_index", 0) or 0

    if state.get("cancelled"):
        return "step_finalizer"
    if not plan or idx >= len(plan):
        return "step_finalizer"

    # F3.1: step bloqueado por dependencias no disponibles → finalizer (saltar).
    from geo_copilot.orchestrator.plan_deps import blocked_dependencies
    if blocked_dependencies(plan[idx], idx, plan, state.get("step_results")):
        return "step_finalizer"

    intent = state.get("intent", "")

    if intent == "query_data":
        # query_database: arranca por data_agent (valida); luego data_agent
        # rutea a gis_agent por el flujo normal.
        return "data_agent"
    if intent in ("spatial_operation", "analyze"):
        return "python_agent"
    if intent in ("apply_symbology", "symbology"):
        # Smart Router: ambos labels mapean a symbology_agent.
        return "symbology_agent"
    if intent in ("search_external", "select_service", "load_external"):
        return "data_agent"
    if intent == "clarify":
        # A5: el step_router ya pausó el plan con la pregunta — el finalizer
        # registra el paso y route_from_step_finalizer sale por plan_paused.
        return "step_finalizer"

    # Cualquier otro intent — log y degradar a step_finalizer para
    # marcar el step como fallido sin colgar el grafo.
    logger.warning(f"[StepRouter] intent inesperado {intent!r} — saltando step")
    return "step_finalizer"
