"""
Responder node — extraído de ``graph.py`` en Fase 6 #6.

Compila la respuesta final del grafo: invoca al ``InsightsAgent`` para
inferir el tipo de visualización (mapa, tabla, gráfico) y arma el
``final_data`` que el handler de la API serializa al cliente.

Es el último nodo del grafo: todos los caminos terminales convergen
aquí (success y la mayoría de errores).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from geo_copilot.core.colors import log_agent
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:  # noqa: C901, PLR0912, PLR0915
    """Compilar la respuesta final del grafo."""
    raw_data = state.get("raw_data") or []
    geojson = state.get("geojson")

    log_agent(
        "Responder", "Compilando respuesta final...",
        f"Data rows: {len(raw_data)}, Has GeoJSON: {geojson is not None}",
    )
    logger.info("[Responder] Compiling final response...")

    # Canal analítico (intent 'analyze'): si el nodo python_agent YA decidió la
    # visualización (chart/table/stats del sandbox), respétala — NO re-inferir
    # desde raw_data (que en analítica es []), que borraba el gráfico/tabla real
    # y además gastaba una llamada LLM inútil. Para el resto (query_data / geo)
    # el InsightsAgent decide qué visualización casa con los datos.
    node_visualization = state.get("visualization")
    if node_visualization:
        visualization = node_visualization
    else:
        # R4.6: el responder es el nodo TERMINAL — un hiccup del LLM aquí no
        # puede tumbar TODA la respuesta (datos + mapa ya listos). Sin
        # visualization se degrada a None y el cliente usa su default.
        try:
            visualization = await graph.insights_agent.infer_visualization_type(
                query=state.get("query"),
                data=raw_data,
                geojson=geojson,
                sql=state.get("sql"),
            )
        except Exception as exc:  # noqa: BLE001 — best-effort en nodo terminal
            logger.warning(f"[Responder] infer_visualization_type falló: {exc}")
            visualization = None

    final_data = {
        "sql": state.get("sql"),
        "data": raw_data,
        "geojson": geojson,
        "symbology": state.get("symbology"),
        "layer_name": state.get("layer_name"),
        "row_count": len(raw_data),
        "visualization": visualization,
        # F2.3: veredicto sobre un resultado vacío (None si hubo filas). El
        # cliente puede mostrar un aviso si is_bug → el 0 quizá no es confiable.
        "empty_result_verdict": state.get("empty_result_verdict"),
    }

    # Detectar fallo parcial / total de un plan multi-paso. Antes el
    # responder ignoraba ``step_results``: si un paso intermedio fallaba
    # pero el paso anterior dejó ``geojson`` en el estado, el grafo
    # entregaba success=True con los datos previos, ocultando el fallo.
    # Ahora reescribimos el mensaje y marcamos ``plan_partial_failure``
    # para que ``GeoAgentGraph.process`` propague success=False.
    step_results = state.get("step_results") or []
    succeeded = [s for s in step_results if s.get("success")]
    failed_steps = [
        s for s in step_results
        if not s.get("success") and not s.get("skipped") and not s.get("pending_selection")
    ]
    # F3.1: pasos SALTADOS porque dependían de uno fallido (consecuencia, no
    # fallo en sí). Se reportan aparte para honestidad.
    skipped_steps = [s for s in step_results if s.get("skipped")]
    plan_partial_failure = False
    plan_message_override: str | None = None

    if (failed_steps or skipped_steps) and not state.get("plan_paused"):
        # Un fallo REAL marca el plan como parcialmente fallido (los saltos son
        # su consecuencia). El grafo propaga success=False si esto es True.
        plan_partial_failure = bool(failed_steps)

        # Narración VERSÁTIL del desenlace con el LLM (en vez del mensaje
        # técnico "falló N paso(s)..."): lidera con lo que sí se logró y sugiere
        # un siguiente paso. has_output = hubo algo mostrable.
        has_output = bool(geojson or raw_data or node_visualization)
        try:
            from geo_copilot.orchestrator.nodes.insights import narrate_plan_outcome
            plan_message_override = await narrate_plan_outcome(
                graph,
                state.get("query", ""),
                succeeded,
                failed_steps,
                skipped_steps,
                has_output,
            )
        except Exception as exc:  # noqa: BLE001 — narrar es best-effort
            logger.warning("[Responder] narrate_plan_outcome falló: %s", exc)
            plan_message_override = (
                "Te muestro lo que sí se pudo resolver."
                if has_output
                else "No pude completar esta operación; ¿probamos otro enfoque?"
            )

    if state.get("error"):
        # SEC-ERROR-LEAK: NO mostrar state['error'] crudo (puede traer nombres
        # de tablas/columnas PostGIS o tracebacks). Los mensajes honestos ya
        # redactados por los nodos pasan tal cual; los crudos se genericar.
        from geo_copilot.core.error_sanitizer import sanitize_error_message
        response: str | None = sanitize_error_message(state["error"], context="responder.final_error")
    elif plan_message_override is not None:
        response = plan_message_override
    else:
        response = state.get("final_response")
        if not response:
            # E2 (audit 2026-06-14): el camino de DATOS (conteo/consulta) llega
            # aquí SIN pasar por el nodo insights (gis_agent -> step_finalizer ->
            # responder), así que ``final_response`` viene vacío y antes caía a
            # "Consulta procesada" — ocultando la respuesta (p. ej. el número del
            # conteo). Narramos el resultado vía el InsightsAgent/LLM (LLM-pilar:
            # el LLM narra hechos reales, no se fabrica). Best-effort.
            if (raw_data or geojson) and not state.get("error"):
                try:
                    from geo_copilot.orchestrator.nodes.insights import narrate_result
                    response = await narrate_result(graph, state)
                except Exception as exc:  # noqa: BLE001 — narrar es best-effort
                    logger.warning(f"[Responder] narración de resultado falló: {exc}")
            elif node_visualization and not state.get("error"):
                # Canal ANALÍTICO: el resultado está en la visualización
                # (tabla/stats/chart), no en raw_data → narramos esos hechos en
                # vez de caer a "Consulta procesada.".
                try:
                    from geo_copilot.orchestrator.nodes.insights import narrate_analysis
                    response = await narrate_analysis(
                        graph, state.get("query", ""), node_visualization
                    )
                except Exception as exc:  # noqa: BLE001 — narrar es best-effort
                    logger.warning(f"[Responder] narración de análisis falló: {exc}")
            response = response or "Consulta procesada."

    # Degradación honesta de 'analyze' sin capa (marcada por el router node):
    # el intent era analítico pero no había capa que analizar, así que se
    # trajeron los datos. Avisamos en vez de mostrarlos como si fueran el
    # análisis pedido.
    if state.get("analyze_degraded_no_layer") and not state.get("error"):
        response = (
            "No había una capa activa para analizar, así que traje los datos. "
            "Pídeme ahora el análisis sobre esta capa (p. ej. «agrupa en "
            "clusters», «correlación entre X e Y», «distribución de áreas»).\n\n"
            + (response or "")
        )

    if visualization:
        logger.debug(f"[Responder] Visualization type: {visualization.get('type')}")

    if plan_partial_failure:
        logger.warning(
            f"[Responder] Plan finalizó con fallos: "
            f"{len(failed_steps)}/{len(step_results)} pasos fallaron"
        )

    return {
        "current_agent": "responder",
        "final_response": response,
        "final_data": final_data,
        "plan_partial_failure": plan_partial_failure,
        "messages": [],
    }
