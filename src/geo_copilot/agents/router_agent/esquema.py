"""El ESQUEMA del router: los intents válidos, la función `route` que llama el LLM y la respuesta
ante un fallo (sin adivinar el intent).
"""

from geo_copilot.agents.base import AgentResponse

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
