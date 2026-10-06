"""Los NODOS del grafo: la envoltura de cada agente (progreso, eventos) que LangGraph ejecuta.

Salió de `GeoAgentGraph` (F4 del plan de calidad: graph.py tenía 1.317 líneas), tal cual.
"""

from typing import TYPE_CHECKING, cast

from geo_copilot.core.formatters import format_found_services
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.grafo_estado import GraphState

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph

logger = get_logger("geo_copilot.orchestrator.graph")


def _g():
    """`graph` importa este módulo; lo que las pruebas sustituyen ahí (`get_settings`,
    `_resolve_react_policy`) se resuelve al usarlo."""
    from geo_copilot.orchestrator import graph

    return graph


class NodosMixin:
    """Los NODOS del grafo: la envoltura de cada agente (progreso, eventos) que LangGraph ejecuta."""

    async def _router_node(self, state: GraphState) -> dict:
        """Fase 6 #6 — cuerpo extraído a ``orchestrator/nodes/router.py``.

        El router NO emite eventos al chip de pipeline: es un paso meta
        (clasificación) que el usuario no necesita ver. Las 4 etapas
        visibles (data / gis / sym / ins) las cubren los agentes reales
        + el responder (que se mapea a 'ins').
        """
        from geo_copilot.orchestrator.nodes import router
        return await router.run(cast("GeoAgentGraph", self), state)

    async def _planner_node(self, state: GraphState) -> dict:
        """Fase 6 #6 — cuerpo extraído a ``orchestrator/nodes/planner.py``."""
        from geo_copilot.orchestrator.nodes import planner as planner_node
        return await planner_node.run(cast("GeoAgentGraph", self), state)

    async def _agent_loop_node(self, state: GraphState) -> dict:
        """A2 (hybrid) — el bucle ReAct como nodo. Ver ``nodes/agent_loop.py``."""
        from geo_copilot.orchestrator.nodes import agent_loop as agent_loop_node
        return await agent_loop_node.run(cast("GeoAgentGraph", self), state)

    async def _step_router_node(self, state: GraphState) -> dict:
        """ORC-5 — entry del bucle multi-paso nativo. Ver ``nodes/step_router.py``."""
        from geo_copilot.orchestrator.nodes import step_router as step_router_node
        return await step_router_node.run(cast("GeoAgentGraph", self), state)

    async def _step_finalizer_node(self, state: GraphState) -> dict:
        """ORC-5 — cierre del step. Ver ``nodes/step_finalizer.py``."""
        from geo_copilot.orchestrator.nodes import step_finalizer as step_finalizer_node
        return await step_finalizer_node.run(cast("GeoAgentGraph", self), state)

    async def _emit_agent(self, state: GraphState, agent: str, description: str, status: str) -> None:
        """Notificar al frontend qué nodo del grafo está corriendo.

        El chip ``AgentsPipe`` del cliente escucha estos eventos vía WS
        para mostrar el progreso en vivo de cada consulta (también las
        single-step, que no pasan por el Planner).
        """
        session_id = state.get("session_id") if isinstance(state, dict) else None
        if not session_id:
            return
        try:
            from geo_copilot.platform import events
            await events.sink().agent_step(session_id, agent, description, status)
        except Exception as exc:  # noqa: BLE001
            # Telemetría best-effort: nunca debe tirar la consulta.
            logger.debug(f"[graph] send_agent_step failed: {exc}")

    async def _run_with_progress(
        self,
        state: GraphState,
        agent: str,
        description: str,
        runner,
    ) -> dict:
        """Wrapper común: emite step_started/step_completed alrededor del nodo."""
        await self._emit_agent(state, agent, description, "started")
        try:
            result: dict = await runner()
        except Exception:
            await self._emit_agent(state, agent, description, "failed")
            raise
        await self._emit_agent(state, agent, description, "completed")
        return result

    async def _data_agent_node(self, state: GraphState) -> dict:
        """Fase 6 #6 parte 2 — cuerpo extraido a ``orchestrator/nodes/data_agent.py``."""
        from geo_copilot.orchestrator.nodes import data_agent
        return await self._run_with_progress(
            state, "data_agent", "Identificando entidades y datos",
            lambda: data_agent.run(cast("GeoAgentGraph", self), state),
        )

    async def _gis_agent_node(self, state: GraphState) -> dict:
        """Fase 6 #6 parte 2 — cuerpo extraido a ``orchestrator/nodes/gis_agent.py``."""
        from geo_copilot.orchestrator.nodes import gis_agent
        return await self._run_with_progress(
            state, "gis_agent", "Generando y ejecutando SQL/PostGIS",
            lambda: gis_agent.run(cast("GeoAgentGraph", self), state),
        )

    async def _python_agent_node(self, state: GraphState) -> dict:
        """Fase 6 #6 parte 2 — cuerpo extraido a ``orchestrator/nodes/python_agent.py``."""
        from geo_copilot.orchestrator.nodes import python_agent
        # python_agent encaja en la categoría "gis" del pipeline visual:
        # ambos materializan resultados espaciales.
        return await self._run_with_progress(
            state, "gis_agent", "Operación espacial sobre datos externos",
            lambda: python_agent.run(cast("GeoAgentGraph", self), state),
        )

    async def _symbology_agent_node(self, state: GraphState) -> dict:
        """Fase 6 #6 — cuerpo extraído a ``orchestrator/nodes/symbology.py``."""
        from geo_copilot.orchestrator.nodes import symbology
        return await self._run_with_progress(
            state, "symbology_agent", "Decidiendo simbología y colores",
            lambda: symbology.run(cast("GeoAgentGraph", self), state),
        )

    async def _insights_agent_node(self, state: GraphState) -> dict:
        """Fase 6 #6 — cuerpo extraído a ``orchestrator/nodes/insights.py``."""
        from geo_copilot.orchestrator.nodes import insights
        return await self._run_with_progress(
            state, "insights_agent", "Redactando análisis y respuesta",
            lambda: insights.run(cast("GeoAgentGraph", self), state),
        )

    async def _responder_node(self, state: GraphState) -> dict:
        """Fase 6 #6 — cuerpo extraído a ``orchestrator/nodes/responder.py``."""
        from geo_copilot.orchestrator.nodes import responder
        # El responder cierra siempre la ejecución. Lo mapeamos a la etapa
        # "insights" del pipeline visual (es la última etapa visible del
        # chip ``AgentsPipe``) para que el usuario vea el cierre incluso
        # cuando el grafo cortocircuita sin pasar por insights_agent.
        return await self._run_with_progress(
            state, "responder", "Respondiendo (insights)",
            lambda: responder.run(cast("GeoAgentGraph", self), state),
        )

    def _format_found_services(self, state: GraphState) -> str:
        """Formatear servicios encontrados anteriormente para incluir en el prompt."""
        return format_found_services(state.get("found_services"))
