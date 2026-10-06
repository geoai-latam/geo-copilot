"""Las RUTAS del grafo: a qué nodo va el turno después de cada agente (las aristas condicionales).

Salió de `GeoAgentGraph` (F4 del plan de calidad: graph.py tenía 1.317 líneas), tal cual.
"""

from typing import Any

from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.grafo_estado import GraphState

logger = get_logger("geo_copilot.orchestrator.graph")


def _g():
    """`graph` importa este módulo; lo que las pruebas sustituyen ahí (`get_settings`,
    `_resolve_react_policy`) se resuelve al usarlo."""
    from geo_copilot.orchestrator import graph

    return graph


class RutasMixin:
    """Las RUTAS del grafo: a qué nodo va el turno después de cada agente (las aristas condicionales)."""

    def _route_from_router(self, state: GraphState) -> str:
        """Determinar siguiente agente desde router: salidas tempranas → planificación → por intent."""
        intent = state.get("intent", "")
        # Smart Router: detectar TODA capa activa (interna o externa,
        # incluyendo previous_geojson heredado).
        # La MISMA regla que el nodo router (una sola definición).
        from geo_copilot.orchestrator.layer_resolution import hay_capa_vectorial

        settings = _g().get_settings()
        # A2 (hybrid): con react_policy='hybrid', las consultas COMPLEJAS van
        # al bucle ReAct (elige y encadena herramientas dinámicamente) en vez
        # del planner cableado. Las simples siguen el grafo clásico. En HITL
        # 'interrupt' el bucle no está integrado (resume re-ejecutaría el
        # razonamiento) → se conserva el planner.
        hybrid = (
            _g()._resolve_react_policy(settings) == "hybrid"
            and getattr(settings, "hitl_mode", "blocking") != "interrupt"
        )
        return (
            self._ruta_temprana(state, intent, hybrid)
            or self._ruta_de_planificacion(state, settings, hybrid)
            or self._ruta_por_intent(intent, hay_capa_vectorial(state), hybrid)
        )

    @staticmethod
    def _ruta_temprana(state: GraphState, intent: str | None, hybrid: bool) -> str | None:
        """Respuesta ya hecha por el router, o intents que siempre resuelve el bucle ReAct."""
        # Si el router ya generó una respuesta directa (o una pregunta
        # aclaratoria A5) — el responder la entrega tal cual.
        # FH.9: en hybrid, una aclaración la decide el bucle, que además de preguntar con
        # texto puede PEDIR EN EL MAPA (request_map_input) o ver que el mapa ya la resuelve
        # (V5: «¿qué hay cerca?» → el router pedía el lugar por texto, 6/6, aun con el punto
        # ya marcado). El router no se toca: su prompt es frágil (A/B «crúzalas»).
        if intent == "clarify" and state.get("final_response") and hybrid:
            logger.info("[Router] clarify - hybrid → agent_loop (puede pedir en el mapa)")
            return "agent_loop"
        if state.get("final_response") and intent in ("direct_response", "clarify"):
            logger.info(f"[Router] {intent} - going to responder")
            return "responder"

        # F3: las herramientas de los servicios MCP enchufados viven en el bucle
        # ReAct (el LLM elige cuál y con qué argumentos): ahí va, en cualquier
        # política — el planner cableado no las conoce.
        if intent == "connected_service":
            logger.info("[Router] connected_service - going to agent_loop (ReAct)")
            return "agent_loop"
        # FH.1: operar el mapa compartido (zoom, visibilidad, orden…) es una
        # herramienta del bucle (map_command), igual en cualquier política.
        if intent == "map_control":
            logger.info("[Router] map_control - going to agent_loop (ReAct)")
            return "agent_loop"
        return None

    @staticmethod
    def _ruta_de_planificacion(state: GraphState, settings: Any, hybrid: bool) -> str | None:
        """AUTONOMÍA: multi-step (operaciones adicionales o consulta compleja) → bucle o planner."""
        # Obtener additional_operations del último mensaje del router
        messages = state.get("messages", [])
        additional_operations = []
        if messages:
            last_msg: Any = messages[-1] if messages else {}
            if last_msg.get("agent") == "router" and last_msg.get("data"):
                additional_operations = last_msg["data"].get("additional_operations", [])

        if additional_operations and len(additional_operations) > 0 and settings.enable_planning:
            if hybrid:
                logger.info("[Router] Additional operations - hybrid → agent_loop (ReAct)")
                return "agent_loop"
            logger.info(f"[Router] Additional operations detected: {additional_operations} - going to planner")
            return "planner"

        if state.get("is_complex_query", False) and settings.enable_planning:
            if hybrid:
                logger.info("[Router] Complex query - hybrid → agent_loop (ReAct)")
                return "agent_loop"
            logger.info("[Router] Complex query - going to planner for multi-step execution")
            return "planner"
        return None

    @staticmethod
    def _ruta_sobre_capa(intent: str | None, has_active_layer: bool, hybrid: bool) -> str | None:
        """Smart Router: skip inteligente sobre la capa cargada (estilo, operación, análisis)."""
        if intent == "apply_symbology":
            if has_active_layer:
                logger.info("[Router] apply_symbology with active layer - skipping data/gis, going to symbology_agent")
                return "symbology_agent"
            # ORQ-12 (auditoría): sin capa activa NO generamos SQL a partir de una
            # petición de ESTILO ("ponlo rojo") — el router node ya fijó un
            # final_response honesto; vamos al responder para entregarlo.
            logger.info("[Router] apply_symbology sin capa activa → responder (mensaje honesto)")
            return "responder"

        # spatial_operation: ahora funciona con CUALQUIER fuente activa
        # (interna, externa, o previous heredada). Antes solo aceptaba
        # external — el usuario tenía que volver a hacer SQL para
        # operar sobre datos internos visibles en el mapa.
        if intent == "spatial_operation":
            # V5 en Chrome (FH): «el área de cada lote» iba directo al sandbox (script,
            # aprobaciones, capa duplicada) sin que el LLM viera las herramientas EXACTAS
            # del workspace (ws_add_measure, ws_buffer…). En hybrid decide el bucle, que
            # tiene ambas (spatial_operation sigue siendo una de sus herramientas).
            if has_active_layer and hybrid:
                logger.info("[Router] spatial_operation on active layer - hybrid → agent_loop (ReAct)")
                return "agent_loop"
            if has_active_layer:
                logger.info("[Router] spatial_operation on active layer - going to python_agent")
                return "python_agent"
            logger.warning("[Router] spatial_operation sin capa activa - going to responder")
            return "responder"

        # analyze: análisis/estadística (clustering, correlación, regresión,
        # vecino más cercano, distribuciones, agregaciones) SOBRE la capa
        # cargada — corre en el sandbox de Python igual que spatial_operation,
        # pero produce tabla/estadísticas/gráfico (no necesariamente geometría).
        # Sin capa activa degradamos a data_agent (traer datos) en vez de cortar.
        if intent == "analyze":
            if has_active_layer:
                logger.info("[Router] analyze on active layer - going to python_agent")
                return "python_agent"
            # A2 (hybrid): analyze SIN capa es implícitamente multi-paso
            # (traer datos → analizar). El bucle ReAct lo resuelve completo en
            # un turno (query_database + analyze_layer) en vez de degradar.
            if hybrid:
                logger.info("[Router] analyze sin capa - hybrid → agent_loop (ReAct)")
                return "agent_loop"
            logger.info("[Router] analyze sin capa activa - degrading to data_agent")
            return "data_agent"
        return None

    @classmethod
    def _ruta_por_intent(cls, intent: str | None, has_active_layer: bool, hybrid: bool) -> str:
        """El intent simple → su agente (el responder si no hay ninguno)."""
        # select_service SIMPLE (sin otras operaciones) va a data_agent
        if intent == "select_service":
            logger.info("[Router] Simple service selection - going to data_agent")
            return "data_agent"
        sobre_capa = cls._ruta_sobre_capa(intent, has_active_layer, hybrid)
        if sobre_capa:
            return sobre_capa
        if intent == "follow_up":
            logger.info("[Router] Follow-up - going to insights_agent")
            return "insights_agent"
        elif intent == "query_data":
            # ORQ (auditoría): se quitó "spatial_analysis" — el RouterAgent NUNCA
            # lo emite (no está en valid_intents; get_capabilities lo documenta
            # como intent fantasma eliminado y agent.py lanza ValueError ante un
            # intent fuera del whitelist). Era código muerto que confundía sobre
            # las capacidades reales.
            logger.info("[Router] Query data - going to data_agent")
            return "data_agent"
        elif intent in ("search_external", "load_external"):
            logger.info(f"[Router] {intent} - going to data_agent for external data")
            return "data_agent"
        else:
            logger.info(f"[Router] Intent '{intent}' - going to responder")
            return "responder"

    def _route_from_step_router(self, state: GraphState) -> str:
        """ORC-5 — proxy al routing puro definido en ``nodes/step_router.py``."""
        from geo_copilot.orchestrator.nodes.step_router import route_from_step_router
        return route_from_step_router(state)

    def _route_from_step_finalizer(self, state: GraphState) -> str:
        """ORC-5 — proxy al routing puro definido en ``nodes/step_finalizer.py``."""
        from geo_copilot.orchestrator.nodes.step_finalizer import route_from_step_finalizer
        return route_from_step_finalizer(state)

    # ORC-5: ``_in_multi_step`` chequea si estamos dentro del bucle del
    # planner. Cuando ``True``, el routing del agente termina en
    # ``step_finalizer`` (que decide loop/exit). Cuando ``False``,
    # el routing es single-step como antes.
    def _in_multi_step(self, state: GraphState) -> bool:
        plan = state.get("execution_plan") or []
        idx = state.get("current_step_index", 0) or 0
        # Estamos en multi-step si hay plan Y el step actual existe.
        # Una vez idx >= len(plan), ya salimos via step_finalizer → responder.
        return bool(plan) and idx < len(plan)

    def _route_from_data_agent(self, state: GraphState) -> str:
        """Determinar siguiente agente desde data_agent."""
        if state.get("error"):
            return "step_finalizer" if self._in_multi_step(state) else "responder"
        if not self._in_multi_step(state) and self._respondio_el_bucle(state):
            return "responder"

        # En multi-step:
        # - ``query_data`` (action_type=query_database): data_agent valida,
        #   pero gis_agent es quien ejecuta el SQL → encadenar.
        # - ``search_external`` / ``select_service``: data_agent es el
        #   ejecutor final del step → ir directo al finalizer.
        # - ``load_external`` (data_agent puede traer geojson directo): ir
        #   al finalizer porque el agente ya hizo el trabajo del step.
        if self._in_multi_step(state):
            intent = state.get("intent", "")
            if intent == "query_data":
                return "gis_agent"
            return "step_finalizer"

        # Si ya tenemos datos externos (geojson), ir directo a simbología
        if state.get("geojson"):
            logger.info("[DataAgent] External data obtained, skipping GISAgent -> SymbologyAgent")
            return "symbology_agent"

        # Si DataAgent ya generó una respuesta final (ej: lista de servicios encontrados),
        # ir directo al responder sin pasar por GISAgent
        if state.get("final_response"):
            logger.info("[DataAgent] Final response already set, going to responder")
            return "responder"

        # Si no hay datos externos ni respuesta, continuar con GISAgent para consulta SQL
        return "gis_agent"

    @staticmethod
    def _respondio_el_bucle(state: GraphState) -> bool:
        """El nodo pasó el turno al bucle ReAct (F5) y el bucle ya respondió: su respuesta es LA
        respuesta. Si el grafo seguía (simbología → insights), el insights re-narraba sobre el
        último resultado parcial y pisaba al bucle (V3 F5: «Encontré 0 sedes» tras agotar pasos)."""
        return state.get("current_agent") == "agent_loop" and bool(state.get("final_response"))

    def _route_from_gis_agent(self, state: GraphState) -> str:
        """Determinar siguiente agente desde gis_agent."""
        if state.get("error"):
            return "step_finalizer" if self._in_multi_step(state) else "responder"
        if self._in_multi_step(state):
            return "step_finalizer"
        if self._respondio_el_bucle(state):
            return "responder"
        if not state.get("geojson"):
            return "responder"
        return "symbology_agent"

    def _route_from_python_agent(self, state: GraphState) -> str:
        """Determinar siguiente agente desde python_agent."""
        if state.get("error"):
            return "step_finalizer" if self._in_multi_step(state) else "responder"
        if self._in_multi_step(state):
            return "step_finalizer"
        if not state.get("geojson"):
            return "responder"
        return "symbology_agent"

    def _route_from_symbology_agent(self, state: GraphState) -> str:
        """Determinar siguiente agente desde symbology_agent.

        Siempre encadena con ``insights_agent`` para generar narrativa/
        metadata visual. Después del insights, ``_route_from_insights_agent``
        decide si volver al finalizer (multi-step) o ir al responder.
        """
        return "insights_agent"

    def _route_from_insights_agent(self, state: GraphState) -> str:
        """ORC-5: en multi-step termina en step_finalizer; single-step va a responder."""
        if self._in_multi_step(state):
            return "step_finalizer"
        return "responder"
