"""
RouterAgent - Agente de enrutamiento inteligente.

Analiza las consultas del usuario y decide qué agentes deben procesarlas.
Implementa el patrón Supervisor de LangGraph.
"""

import json

from geo_copilot.agents.base import AgentResponse, BaseAgent
from geo_copilot.core.config import settings
from geo_copilot.core.formatters import (
    format_active_layer_context,
    format_external_data_context,
    format_found_services,
)
from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


class RouterAgent(BaseAgent):
    """
    Agente supervisor que analiza intenciones y enruta consultas.

    Capacidades:
    - Análisis de intent en lenguaje natural
    - Detección de contexto de conversación
    - Enrutamiento a agentes especializados
    - Manejo de follow-ups y referencias a datos previos
    """

    def __init__(self, llm_client: LLMClient | None = None):
        super().__init__(
            name="RouterAgent",
            description="Supervisor de enrutamiento inteligente"
        )
        self.llm_client = llm_client or LLMClient.from_settings(settings)
        self._register_tools()

    def _register_tools(self) -> None:
        """Registrar herramientas del agente."""
        # RouterAgent no usa herramientas externas, el grafo invoca
        # directamente process() que contiene toda la lógica de routing.
        pass

    def get_capabilities(self) -> dict:
        """Retornar capacidades del agente."""
        return {
            # R4.7: en sync con valid_intents (el whitelist real del process).
            # 'spatial_analysis' era un intent FANTASMA — anunciado aquí pero
            # rechazado por la validación; quien consumiera esta lista mentía.
            "intents": [
                "query_data",
                "spatial_operation",  # Operaciones sobre datos en memoria
                "analyze",            # Análisis / estadística / ML en el sandbox de Python
                "apply_symbology",    # Smart Router: cambio de simbología sin re-buscar
                "search_external",
                "select_service",
                "load_external",
                "follow_up",
                "direct_response",
                "clarify",            # A5: preguntar ante ambigüedad real
                "connected_service",  # F3: una tool de un servidor MCP enchufado
                "map_control",        # FH.1: operar el mapa (zoom, visibilidad, orden…)
            ],
            "supported_actions": [
                "respond_directly",
                "query_database",
                "use_context",
                "search_external",
                "select_service",
                "load_external",
                "spatial_operation",  # NUEVO
            ]
        }

    async def process(
        self,
        query: str,
        context: dict | None = None
    ) -> AgentResponse:
        """
        Procesar una consulta y determinar el enrutamiento.

        Args:
            query: Consulta del usuario
            context: Contexto con historial, SQL previo, servicios encontrados, etc.

        Returns:
            AgentResponse con la decisión de enrutamiento
        """
        context = context or {}

        try:
            system_prompt, user_prompt = self._prompts(query, context)
            # A3 (structured outputs): el LLM responde LLAMANDO la función
            # `route` con schema — el JSON llega validado por el proveedor y
            # el brace-slicing (parse_json_from_llm + default) desaparece.
            # La validación SEMÁNTICA (whitelist) sigue abajo, intacta.
            mensajes = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
            result = await self._decidir(mensajes)
            return self._respuesta(result, context)

        except Exception as e:  # captura amplia a propósito: frontera del router (LLM + parseo); se responde con fallo claro, sin adivinar intent
            logger.error(f"RouterAgent error: {e}", exc_info=True)
            return _respuesta_de_error(e)

    def _prompts(self, query: str, context: dict) -> tuple[str, str]:
        """El prompt del sistema (conversación, servicios, datos, mapa, plataforma, esquema) y el
        del usuario."""
        # Construir contexto de conversación
        conversation_context = self._build_conversation_context(context)

        # Construir información de servicios encontrados
        services_context = self._format_found_services(
            context.get("found_services", [])
        )

        # Construir información de datos externos cargados
        external_data_context = self._format_external_data_context(context)

        # Fase A: añadir el estado REAL del mapa que reporta el frontend
        # (capas cargadas incl. Discovery, feature seleccionada, viewport).
        from geo_copilot.core.formatters import (
            format_map_context,
            format_platform_capabilities,
        )
        map_block = format_map_context(context.get("map_context"))
        if map_block:
            external_data_context = (
                f"{external_data_context}\n\n{map_block}".strip()
            )

        # F1.1/C3: autoconocimiento — qué NO puede hacer esta plataforma
        # (sandbox POSIX; qué servicios MCP hay enchufados).
        plat_block = format_platform_capabilities(
            context.get("sandbox_available", True),
            servicios_conectados=context.get("connected_services"),
        )
        if plat_block:
            external_data_context = (
                f"{external_data_context}\n\n{plat_block}".strip()
            )

        # Obtener schema summary si está disponible
        schema_info = context.get("schema_info", "")

        # Construir prompt del sistema
        system_prompt = self._build_system_prompt(
            schema_info=schema_info,
            conversation_context=conversation_context,
            services_context=services_context,
            external_data_context=external_data_context,
            session_region=context.get("session_region"),  # F2.2
        )

        # Construir prompt del usuario. Con la fecha de hoy (hecho): V5 «NDVI de Chía en enero de
        # 2025» → el router dijo «es una fecha futura» (la de su entrenamiento) y lo mandó a buscar
        # en portales; el bucle ReAct ya la tenía (react_system_prompt).
        from datetime import date

        user_prompt = f'Hoy es {date.today().isoformat()}.\nMENSAJE DEL USUARIO: "{query}"'
        return system_prompt, user_prompt

    async def _decidir(self, mensajes: list[LLMMessage]) -> dict:
        """La llamada `route` validada: intent dentro del whitelist (un reintento con el hecho) y
        clarify con su pregunta."""
        from geo_copilot.core.structured_output import structured_call

        result = await structured_call(
            self.llm_client,
            mensajes,
            name="route",
            description=(
                "Reporta la decisión de enrutamiento de la consulta "
                "geoespacial del usuario."
            ),
            parameters=_ESQUEMA_ROUTE,
        )

        intent = result.get("intent")
        if intent not in _VALID_INTENTS:
            # V5 (hello): «dibújame un círculo de 500 m…» → intent «hello__circle» (el nombre de una
            # herramienta, que el proveedor no impidió pese al enum) y el usuario recibía «No pude
            # procesar tu consulta» 2 de cada 3 veces. Un reintento con el hecho; decide el LLM.
            logger.warning(f"[Router] intent inválido {intent!r}: se le pide corregir una vez")
            result = await structured_call(
                self.llm_client,
                [*mensajes, LLMMessage(role="user", content=(
                    f"«{intent}» no es un intent: los intents válidos son {sorted(_VALID_INTENTS)}. Si lo "
                    "pedido lo hace una herramienta de un servicio de \"SERVICIOS MCP CONECTADOS\", el intent "
                    "es `connected_service` (el agente elige la herramienta). Vuelve a llamar `route`."))],
                name="route",
                description="Reporta la decisión de enrutamiento de la consulta geoespacial del usuario.",
                parameters=_ESQUEMA_ROUTE,
            )
            intent = result.get("intent")
        if intent not in _VALID_INTENTS:
            raise ValueError(
                f"Router LLM devolvió intent inválido: {intent!r}. "
                f"Válidos: {sorted(_VALID_INTENTS)}"
            )
        # A5: clarify SIN pregunta es inservible — fallo honesto (no
        # entregamos un turno vacío).
        if intent == "clarify" and not str(result.get("response") or "").strip():
            raise ValueError("Router LLM eligió clarify sin la pregunta en `response`.")
        return dict(result)

    def _respuesta(self, result: dict, context: dict) -> AgentResponse:
        """La decisión de enrutamiento, con la selección de servicio ya resuelta."""
        intent = result.get("intent")
        reasoning = result.get("reasoning", "")
        direct_response = result.get("response")
        entities = result.get("entities", [])
        external_url = result.get("external_url")
        selected_service_number = result.get("selected_service_number")
        target_layer_id = result.get("target_layer_id")  # FRT-04: capa nombrada
        is_multi_step = bool(result.get("is_multi_step", False))
        additional_operations = result.get("additional_operations", [])

        # Si hay operaciones adicionales, garantizamos multi-step.
        if additional_operations:
            is_multi_step = True
            logger.info(
                f"[RouterAgent] Detected additional operations: {additional_operations}"
            )

        # Procesar selección de servicio: si el número está fuera de
        # rango devolvemos error específico (antes era "no hay servicios
        # disponibles" — mensaje engañoso).
        if intent == "select_service":
            resolved_url, error_msg = self._resolve_service_selection(
                selected_service_number=selected_service_number,
                found_services=context.get("found_services", []),
            )
            if error_msg:
                intent = "direct_response"
                direct_response = error_msg
            else:
                external_url = resolved_url

        logger.info(
            f"[RouterAgent] Intent: {intent}, MultiStep: {is_multi_step}"
        )

        return AgentResponse(
            success=True,
            message=f"Routed to: {intent}",
            data={
                "intent": intent,
                "reasoning": reasoning,
                "entities": entities,
                "direct_response": direct_response,
                "external_url": external_url,
                "selected_service_number": selected_service_number,
                "target_layer_id": target_layer_id,  # FRT-04
                "is_complex_query": is_multi_step,
                "additional_operations": additional_operations,
            }
        )

    def _build_system_prompt(
        self,
        schema_info: str,
        conversation_context: str,
        services_context: str,
        external_data_context: str = "",
        session_region: str | None = None,
    ) -> str:
        """Construir el prompt del sistema para análisis de intent.

        Fase 6 #2 — la plantilla vive en ``router_agent/prompts.py`` para
        que sea testeable y versionable de forma aislada del agente.
        """
        from geo_copilot.agents.router_agent.prompts import build_router_system_prompt
        return build_router_system_prompt(
            schema_info=schema_info,
            conversation_context=conversation_context,
            services_context=services_context,
            external_data_context=external_data_context,
            session_region=session_region,
        )

    def _build_conversation_context(self, context: dict) -> str:
        """Construir contexto de conversación para el prompt."""
        parts = []

        # Historial de conversación
        history = context.get("conversation_history", [])
        if history:
            recent = history[-6:]
            parts.append("CONVERSACIÓN RECIENTE:")
            for m in recent:
                role = "Usuario" if m.get('role') == 'user' else "Asistente"
                content = m.get('content', '')[:300]
                parts.append(f"  {role}: {content}")

        # SQL previo
        previous_sql = context.get("previous_sql")
        if previous_sql:
            parts.append(f"\nÚLTIMO SQL EJECUTADO:\n{previous_sql}")

        # Resultados previos
        previous_results = context.get("previous_results")
        if previous_results:
            parts.append(f"\nRESULTADOS PREVIOS: {len(previous_results)} registros")
            if previous_results:
                sample = previous_results[:2]
                parts.append(f"Ejemplo: {json.dumps(sample, default=str)[:500]}")

        return "\n".join(parts) if parts else "Sin contexto previo"

    def _format_found_services(self, found_services: list[dict]) -> str:
        """Formatear servicios encontrados para incluir en el prompt."""
        return format_found_services(found_services)

    def _format_external_data_context(self, context: dict) -> str:
        """Formatear información de datos cargados para incluir en el prompt.

        Smart Router (2026-05-31): combina el bloque legacy de datos
        externos con el nuevo ``format_active_layer_context``, que cubre
        capas internas (heredadas de turno anterior) — antes el router
        no sabía cuándo el usuario tenía una capa interna en el mapa y
        terminaba pidiendo SQL nuevo para ajustes triviales.
        """
        active_source = context.get("active_data_source", "none")
        active_name = context.get("active_source_name") or context.get("external_source_name")
        # Detectar feature_count desde el campo correcto según la fuente.
        if active_source == "external":
            feature_count = context.get("external_feature_count", 0)
        else:
            feature_count = context.get("active_feature_count", 0)

        layer_block = format_active_layer_context(
            active_source=active_source,
            source_name=active_name,
            feature_count=feature_count,
            geometry_type=context.get("active_geometry_type"),
            field_names=context.get("active_field_names"),
        )

        # Conservar el bloque legacy de external (parsea diferente vocabulario
        # en el LLM) solo si NO ya cubrimos con el bloque nuevo.
        if layer_block:
            return layer_block

        return format_external_data_context(
            has_external_data=context.get("has_external_data", False),
            source_name=context.get("external_source_name", "servicio externo"),
            feature_count=context.get("external_feature_count", 0),
        )

    def _resolve_service_selection(
        self,
        selected_service_number: int | str | None,
        found_services: list[dict],
    ) -> tuple[str | None, str | None]:
        """Resolver número → URL del servicio. Devuelve `(url, error)`.

        - `(url, None)`: selección válida, `url` es el endpoint.
        - `(None, "...")`: error específico al usuario (sin servicios disponibles,
          número fuera de rango, número no parseable). El caller debe degradar
          a `intent=direct_response` con el `error` como mensaje.
        """
        if not found_services:
            return None, (
                "No hay servicios disponibles para seleccionar. "
                "Primero busca con 'busca [tema]'."
            )
        if selected_service_number is None:
            return None, (
                "No indicaste el número del servicio. "
                "Escribe el número de la tarjeta que quieres cargar."
            )
        try:
            idx = int(selected_service_number) - 1
        except (ValueError, TypeError):
            return None, (
                f"'{selected_service_number}' no es un número válido. "
                f"Indica un número entre 1 y {len(found_services)}."
            )
        if not (0 <= idx < len(found_services)):
            return None, (
                f"El número {selected_service_number} excede los "
                f"{len(found_services)} servicios listados. "
                f"Elige entre 1 y {len(found_services)}."
            )
        selected = found_services[idx]
        url = selected.get("url")
        if not url:
            return None, (
                f"El servicio #{selected_service_number} ({selected.get('name','?')}) "
                "no tiene una URL utilizable."
            )
        logger.info(
            f"[RouterAgent] Selected service #{selected_service_number}: "
            f"{selected.get('name')}"
        )
        return url, None


_VALID_INTENTS = {
    "direct_response", "query_data", "follow_up",
    "search_external", "select_service", "load_external",
    "spatial_operation", "analyze",
    # Smart Router (2026-05-31): intents para "skip inteligente"
    # cuando el usuario tiene una capa cargada y solo quiere
    # ajustarla. Evita roundtrip por data_agent/gis_agent.
    "apply_symbology",
    # A5 (Fase 4): el agente PREGUNTA cuando la ambigüedad es real
    # (la pregunta va en `response`); el turno termina limpio.
    "clarify",
    # F3: lo resuelve una herramienta de un servicio MCP enchufado
    # (imagery satelital, geocodificación, …) — va al bucle ReAct.
    "connected_service",
    # FH.1: operar el mapa compartido (encuadrar, ocultar, opacidad,
    # orden, etiquetas, quitar) — va al bucle ReAct (map_command).
    "map_control",
}

# el mismo esquema para la llamada y para el reintento de corrección
_ESQUEMA_ROUTE: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": sorted(_VALID_INTENTS)},
        "reasoning": {
            "type": "string",
            "description": "Por qué elegiste este intent.",
        },
        "response": {
            "type": ["string", "null"],
            "description": "Respuesta natural (intent=direct_response) "
            "o pregunta aclaratoria (intent=clarify).",
        },
        "entities": {
            "type": "array", "items": {"type": "string"},
            "description": "Entidades/tablas mencionadas.",
        },
        "selected_service_number": {
            "type": ["integer", "null"],
            "description": "Número (1..N) si intent=select_service.",
        },
        "external_url": {
            "type": ["string", "null"],
            "description": "URL si el usuario proporcionó una (intent=load_external).",
        },
        # FRT-04. Sin esta propiedad, `additionalProperties:
        # False` impedía al proveedor devolverla y el router
        # cableado nunca dirigía la acción a la capa nombrada.
        # El nodo router la valida contra map_layers.
        "target_layer_id": {
            "type": ["string", "null"],
            "description": "[id] EXACTO de la capa de "
            "'CAPAS EN EL MAPA' que el usuario nombró; null si "
            "no nombró ninguna o es ambiguo.",
        },
        "is_multi_step": {"type": "boolean"},
        "additional_operations": {
            "type": "array", "items": {"type": "string"},
            "description": "Operaciones adicionales detectadas en la consulta.",
        },
    },
    "required": ["intent", "reasoning"],
    "additionalProperties": False,
}


def _respuesta_de_error(e: Exception) -> AgentResponse:
    """Fallo claro del router, sin adivinar el intent.

    No adivinamos el intent por keywords. Antes había una
    _analyze_error_for_hints que mapeaba palabras como "buffer",
    "lotes", "busca" → intents specíficos — eso enviaba la query
    por un camino que el usuario nunca pidió. Ahora respondemos
    con un fallo claro y dejamos que el usuario reformule.
    """
    err_type = type(e).__name__
    suggestion = "Intenta reformular tu consulta de manera más específica."
    from geo_copilot.core.llm_client import mensaje_fallo_llm

    # Saturación o cuota agotada del proveedor: el mensaje honesto de cada caso (no «reformula»)
    if mensaje := mensaje_fallo_llm(e):
        suggestion = mensaje
    elif "timeout" in err_type.lower() or "timeout" in str(e).lower():
        suggestion = (
            "La operación tardó demasiado. Intenta con una consulta más simple."
        )
    elif "json" in err_type.lower():
        suggestion = (
            "Hubo un problema interpretando la respuesta. "
            "Reformula tu pregunta o reintenta."
        )

    return AgentResponse(
        success=False,
        message=f"No pude procesar tu consulta. {suggestion}",
        data={
            "intent": "direct_response",
            "action": "respond_directly",
            "direct_response": f"Error: {suggestion}",
            "entities": [],
            "is_complex_query": False,
            "additional_operations": [],
            "error_context": {
                "original_error": str(e)[:100],
                "error_type": err_type,
            },
        }
    )
