"""
Planner node — extraído de ``graph.py`` en Fase 6 #6.

Genera el plan multi-paso (cuando ``is_complex_query=True``). El
``PlanExecutor`` lo ejecuta en un nodo posterior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from geo_copilot.core.colors import log_agent
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Generar el plan de ejecución para una consulta multi-paso."""
    log_agent("PlannerAgent", "Generando plan multi-paso...", f"Query: {state['query'][:60]}...")
    logger.info("[Planner] Generating execution plan...")

    session_id = state.get("session_id", "")

    try:
        schema_info = await graph._get_cached_schema()
        context = {
            "has_external_data": state.get("has_external_data", False),
            "external_source_name": state.get("external_source_name"),
            "found_services": state.get("found_services", []),
            "schema_info": schema_info,
            # Capas YA cargadas en el mapa: el planner las usa para NO planear
            # pasos que re-consultan una entidad que ya está cargada (causa
            # típica de "falló un paso"). Ver _build_planner_prompt.
            "map_context": state.get("map_context"),
        }
        response = await graph.planner_agent.process(query=state["query"], context=context)

        if response.success and response.data:
            plan_data = response.data
            steps = plan_data.get("steps", [])
            reasoning = plan_data.get("reasoning", "")

            # C3a-3: la notificación WS va en su PROPIO try/except. Antes
            # estaba dentro del try de generación, así que un hiccup de WS
            # (desconexión, serialización) tiraba todo al except y descartaba
            # un plan válido devolviendo execution_plan=None.
            if session_id:
                try:
                    from geo_copilot.platform import events
                    await events.sink().plan_created(session_id, steps, reasoning)
                except Exception as ws_exc:  # noqa: BLE001
                    logger.debug(f"[Planner] WS send_plan_created failed: {ws_exc}")

            return {
                "current_agent": "planner",
                "execution_plan": steps,
                "plan_reasoning": reasoning,
                "current_step_index": 0,
                # B4: capturar la query original ANTES de que step_router
                # la pise con los fragmentos de cada step.
                "original_query": state["query"],
                "messages": [{
                    "agent": "planner",
                    "content": f"Plan generado: {len(steps)} pasos - {reasoning[:100]}",
                    "data": plan_data,
                    "success": True,
                }],
            }

        # Error generando plan.
        return {
            "current_agent": "planner",
            "error": response.message,
            "execution_plan": None,
            "messages": [{
                "agent": "planner",
                "content": f"Error: {response.message}",
                "data": None,
                "success": False,
            }],
        }

    except Exception as exc:  # noqa: BLE001 — frontera del nodo (LLM); sanitize_error registra el traceback
        # SEC-ERROR-LEAK: no propagar str(exc) crudo al estado (llega al
        # cliente vía el responder). sanitize_error loguea el detalle y
        # devuelve un mensaje honesto sin internals.
        from geo_copilot.core.error_sanitizer import sanitize_error
        return {
            "current_agent": "planner",
            "error": sanitize_error(exc, context="planner.build_plan"),
            "execution_plan": None,
            "messages": [],
        }
