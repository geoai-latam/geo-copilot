"""El RESULTADO del turno: el camino ReAct completo, el contrato de respuesta de la API y
el HITL por interrupt (pausa y reanudación).

Salió de `GeoAgentGraph` (F4 del plan de calidad: graph.py tenía 1.317 líneas), tal cual.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from langgraph.errors import GraphRecursionError

from geo_copilot.core.error_sanitizer import sanitize_error
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.grafo_estado import _build_reasoning_trace

if TYPE_CHECKING:
    from geo_copilot.agents.insights_agent.agent import InsightsAgent
    from geo_copilot.orchestrator.grafo_estado import GraphState
    from geo_copilot.orchestrator.graph import GeoAgentGraph

logger = get_logger("geo_copilot.orchestrator.graph")


def _g():
    """`graph` importa este módulo; lo que las pruebas sustituyen ahí (`get_settings`,
    `_resolve_react_policy`) se resuelve al usarlo."""
    from geo_copilot.orchestrator import graph

    return graph


def _respuesta_react(result: dict, ok: bool, geojson: Any, raw_data: list,
                     decision_trace: list, visualization: Any) -> dict:
    """El contrato de respuesta de la API para un turno del bucle ReAct."""
    return {
        "success": ok,
        "partial": False,
        "message": result.get("final_response", ""),
        "intent": result.get("intent"),
        "sql": result.get("sql"),
        "data": result.get("data") or {"results": raw_data},
        "geojson": geojson,
        "symbology": result.get("symbology"),
        "layer_name": result.get("layer_name"),
        # FRT-04: la capa objetivo que la tool ReAct resolvió por NOMBRE. Sin
        # esto la API cae a la capa activa y el cliente re-estila la activa,
        # no la nombrada (bug destapado por el E2E de integración nivel 3).
        "target_layer_id": result.get("target_layer_id"),
        "result_layer_ref": result.get("result_layer_ref"),
        "agent_messages": [],
        # F1.2/F4.T3: la traza de decisiones del bucle es la traza de razonamiento.
        "reasoning_trace": decision_trace,
        "decision_trace": decision_trace,
        "visualization": visualization,
        "analiticos": result.get("analiticos"),
        "map_commands": result.get("map_commands"),
        "found_services": result.get("found_services"),
        "new_search_executed": bool(result.get("new_search_executed")),
        "external_geojson": result.get("external_geojson"),
        "external_source_name": result.get("external_source_name"),
        "has_external_data": result.get("has_external_data", False),
        "external_imagery": result.get("external_imagery"),
        "imagery_previas": result.get("imagery_previas"),
        "python_code": result.get("python_code"),
        "pending_operations": None,
        "step_results": None,
        "a2a_log": result.get("a2a_log") or [],
    }


class ResultadoMixin:
    """El resultado del turno: camino ReAct, contrato de respuesta de la API y HITL por interrupt."""

    if TYPE_CHECKING:  # lo que el mixin usa de GeoAgentGraph
        insights_agent: InsightsAgent
        compiled_interrupt: Any
        compiled: Any

    async def _run_react_mode(self, initial_state: Mapping[str, Any]) -> dict:
        """F4.5: ejecuta el turno con el bucle ReAct (agent_loop) y mapea su
        salida al MISMO shape de respuesta que el camino normal, para que la API
        no note diferencia. El bucle elige herramientas dinámicamente (capability
        listing) en vez de un intent fijo; reusa los nodos existentes.
        """
        from geo_copilot.orchestrator.nodes import agent_loop
        try:
            result = await agent_loop.run(cast("GeoAgentGraph", self), cast("GraphState", initial_state))
        except Exception as e:  # noqa: BLE001
            return {
                "success": False, "message": sanitize_error(e, context="react.agent_loop"),
                "sql": None, "data": None, "geojson": None,
            }

        geojson = result.get("geojson") or result.get("external_geojson")
        raw_data = result.get("raw_data") or []
        decision_trace = result.get("decision_trace") or []
        # Éxito salvo que la última decisión del bucle sea un error (breaker/LLM).
        ok = not (decision_trace and decision_trace[-1].get("kind") == "error")

        # R1.4: la visualización/data del CANAL ANALÍTICO del bucle (tabla/
        # stats/gráfico del sandbox) gana sobre la re-inferencia — mismo
        # contrato que _map_final_state en el grafo cableado.
        visualization = result.get("visualization")
        if visualization is None:
            try:
                visualization = await self.insights_agent.infer_visualization_type(
                    query=initial_state.get("query"), data=raw_data,
                    geojson=geojson, sql=result.get("sql"),
                )
            except Exception:  # noqa: BLE001 — visualización es best-effort
                visualization = None

        return _respuesta_react(result, ok, geojson, raw_data, decision_trace, visualization)

    async def _ejecutar_grafo(self, initial_state: Mapping[str, Any], settings: Any) -> dict:
        """El turno por el grafo clásico, con su límite de recursión y sus errores dichos."""
        try:

            # C3a-1: límite de recursión explícito proporcional al plan.
            # El bucle multi-paso (step_router→agente→step_finalizer) consume
            # ~3-4 supersteps por paso; con el default de LangGraph (25) un
            # plan legítimo de 4-5 pasos podía lanzar GraphRecursionError. Lo
            # dimensionamos a partir de ``max_plan_steps`` con holgura.
            max_steps = getattr(settings, "max_plan_steps", 5)
            recursion_limit = max(25, max_steps * 8 + 15)

            # Ejecutar grafo
            final_state = await self.compiled.ainvoke(
                initial_state,
                config={"recursion_limit": recursion_limit},
            )
            return self._map_final_state(final_state)

        except GraphRecursionError:
            # C3a-1: antes esto se tragaba en el except genérico como un
            # "Error: ..." opaco. Lo reportamos de forma explícita.
            logger.error(
                "Graph recursion limit alcanzado — el plan excede los pasos "
                "ejecutables. Revisa max_plan_steps / posible bucle."
            )
            return {
                "success": False,
                "message": (
                    "El plan resultó demasiado largo para ejecutarse de una vez. "
                    "Divide la consulta en pasos más pequeños."
                ),
                "sql": None,
                "data": None,
                "geojson": None,
            }
        except Exception as e:  # noqa: BLE001 — frontera del grafo; sanitize_error registra el traceback
            return {
                "success": False,
                "message": sanitize_error(e, context="graph.process"),
                "sql": None,
                "data": None,
                "geojson": None,
            }

    def _map_final_state(self, final_state: dict) -> dict:
        """Mapea el estado final del grafo al shape de respuesta de la API.

        Extraído de ``process()`` para reusarlo desde el camino normal, el de
        interrupt, y el resume — una sola fuente de verdad del contrato.
        """
        final_data = final_state.get("final_data") or {}
        had_error = bool(final_state.get("error"))
        plan_failed = bool(final_state.get("plan_partial_failure"))
        # A2: un turno hybrid que cerró por circuit-breaker/error del bucle
        # ReAct es un fallo — misma regla que _run_react_mode (última decisión
        # de la traza con kind='error').
        _dt = final_state.get("decision_trace") or []
        react_failed = bool(_dt) and _dt[-1].get("kind") == "error"
        return {
            "success": not (had_error or plan_failed or react_failed),
            "partial": plan_failed and not had_error,
            "message": final_state.get("final_response", ""),
            "intent": final_state.get("intent"),
            "sql": final_state.get("sql"),
            # Canal analítico: si el nodo python_agent emitió `data`
            # (chart/table/stats del sandbox), respétalo; si no, envuelve
            # raw_data como hasta ahora (path geométrico / query_data).
            "data": final_state.get("data") or {"results": final_state.get("raw_data", [])},
            "geojson": final_state.get("geojson"),
            "symbology": final_state.get("symbology"),
            "layer_name": final_state.get("layer_name"),
            # FRT-04: la capa objetivo que el router resolvió por NOMBRE. Sin esto
            # la API cae a la capa activa y el cliente re-estila la activa, no la
            # nombrada (mismo hueco que _run_react_mode, camino cableado).
            "target_layer_id": final_state.get("target_layer_id"),
            "result_layer_ref": final_state.get("result_layer_ref"),
            "agent_messages": final_state.get("messages", []),
            # A2: si el turno pasó por el bucle ReAct (hybrid), su traza de
            # decisiones es la traza de razonamiento — más rica que la de
            # mensajes (que el bucle no emite).
            "reasoning_trace": (
                final_state.get("decision_trace")
                or _build_reasoning_trace(final_state)
            ),
            "decision_trace": final_state.get("decision_trace"),
            # La visualización del nodo (analítica) gana sobre la que infiere el
            # responder; para el resto cae al valor inferido en final_data.
            "visualization": final_state.get("visualization") or final_data.get("visualization"),
            "analiticos": final_state.get("analiticos"),
            "map_commands": final_state.get("map_commands"),
            "found_services": final_state.get("found_services"),
            "new_search_executed": bool(final_state.get("new_search_executed")),
            "external_geojson": final_state.get("external_geojson"),
            "external_source_name": final_state.get("external_source_name"),
            "has_external_data": final_state.get("has_external_data", False),
            "external_imagery": final_state.get("external_imagery"),
            "imagery_previas": final_state.get("imagery_previas"),
            "python_code": final_state.get("python_code"),
            "pending_operations": final_state.get("pending_operations"),
            "step_results": final_state.get("step_results"),
            "a2a_log": final_state.get("a2a_log") or [],
        }

    def _interrupt_config(self, session_id: str) -> dict:
        # F4.2 (revisión adversarial): interrupt mode REQUIERE un session_id único
        # como thread_id; sin él, dos turnos colisionarían en el mismo checkpoint
        # (un usuario reanudaría el SQL pendiente de otro). No coalescemos a
        # 'default' en silencio — fallamos honesto.
        if not session_id:
            raise ValueError("session_id requerido para HITL interrupt mode")
        max_steps = getattr(_g().get_settings(), "max_plan_steps", 5)
        return {
            "configurable": {"thread_id": session_id},
            "recursion_limit": max(25, max_steps * 8 + 15),
        }

    @staticmethod
    def _waiting_approval(interrupts, session_id: str) -> dict:
        """Respuesta cuando el grafo se PAUSÓ esperando aprobación humana."""
        payload = {}
        approval_id = f"interrupt_{session_id}"
        try:
            if interrupts:
                payload = interrupts[0].value
                approval_id = getattr(interrupts[0], "id", None) or approval_id
        except Exception:  # noqa: BLE001
            payload = {}
        return {
            "success": False,
            "status": "waiting_approval",
            # F4.2: pending_approval_id permite a la API marcar WAITING_APPROVAL y
            # al cliente saber qué aprobar (vía graph.resume_hitl en el endpoint).
            "pending_approval_id": approval_id,
            "requires_approval": True,
            "approval": payload,  # {action_type, title, preview/sql, risks, ...}
            "message": "Esperando aprobación humana para continuar.",
            "session_id": session_id,
        }

    async def _run_interrupt_mode(self, initial_state: Mapping[str, Any], session_id: str) -> dict:
        """Corre el turno por el grafo CON checkpointer. Si un nodo llamó a
        ``interrupt()`` (HITL), retorna ``waiting_approval`` con el payload; si
        no, mapea el estado final como siempre."""
        try:
            final_state = await self.compiled_interrupt.ainvoke(
                initial_state, config=self._interrupt_config(session_id))
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": sanitize_error(exc, context="hitl-interrupt"),
                    "sql": None, "data": None, "geojson": None}
        interrupts = final_state.get("__interrupt__")
        if interrupts:
            return self._waiting_approval(interrupts, session_id)
        return self._map_final_state(final_state)

    async def resume_hitl(self, session_id: str, decision) -> dict:
        """Reanuda un turno pausado por HITL con la decisión del usuario.

        ``decision`` puede ser un ``HITLResponse``, un dict (``status``/
        ``modified_content``) o un string de estado. Devuelve el resultado final
        (o ``waiting_approval`` si el plan requiere otra aprobación)."""
        if self.compiled_interrupt is None:
            return {"success": False,
                    "message": "HITL interrupt no está habilitado en este despliegue."}
        from langgraph.types import Command
        decision_value = decision.model_dump() if hasattr(decision, "model_dump") else decision
        try:
            final_state = await self.compiled_interrupt.ainvoke(
                Command(resume=decision_value), config=self._interrupt_config(session_id))
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "message": sanitize_error(
                        exc, context="hitl-interrupt.resume",
                        user_message="No se pudo reanudar la consulta. Inténtalo de nuevo."),
                    "sql": None, "data": None, "geojson": None}
        interrupts = final_state.get("__interrupt__")
        if interrupts:
            return self._waiting_approval(interrupts, session_id)
        return self._map_final_state(final_state)
