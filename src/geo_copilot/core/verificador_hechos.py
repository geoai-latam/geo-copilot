"""¿La respuesta directa del router afirma hechos del mundo? (plan de fiabilidad F1).

El router puede contestar él mismo (`direct_response`) sin consultar nada. Una sola señal dentro de su
prompt de ~23 k caracteres no basta: con la pregunta sobre la licencia de Open-Meteo marcaba «no requiere
fuente» y respondía, falso, que se puede usar comercialmente. Aquí un juicio corto y enfocado mira el
BORRADOR de la respuesta (no la pregunta) y lo decide el LLM, sin listas de palabras.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.prompts import cargar_prompt

logger = get_logger("geo_copilot.core.verificador_hechos")

_PROMPT = cargar_prompt("verificador_hechos")
_ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "afirma_hechos": {"type": "boolean"},
        "motivo": {"type": "string", "description": "Una frase: qué hecho afirma, o por qué no afirma ninguno."},
    },
    "required": ["afirma_hechos", "motivo"],
    "additionalProperties": False,
}


async def afirma_hechos(llm: Any, query: str, respuesta: str) -> bool:
    """True si el borrador afirma hechos que deberían salir de una fuente.

    Ante un fallo del LLM devuelve False: se conserva el camino de siempre (la respuesta directa) en vez
    de inventar un juicio."""
    if llm is None or not (respuesta or "").strip():
        return False
    from geo_copilot.core.structured_output import structured_call

    try:
        r = await structured_call(
            llm,
            [LLMMessage(role="user", content=_PROMPT.format(query=query, respuesta=respuesta))],
            name="verificar",
            description="Reporta si la respuesta afirma hechos del mundo que deberían salir de una fuente.",
            parameters=_ESQUEMA,
            max_tokens=300,
        )
    except Exception as exc:  # noqa: BLE001 — sin juicio, el camino de siempre
        logger.warning(f"[verificador_hechos] sin juicio ({type(exc).__name__}); se conserva la respuesta directa")
        return False
    logger.info(f"[verificador_hechos] afirma_hechos={r.get('afirma_hechos')} · {str(r.get('motivo', ''))[:160]}")
    return bool(r.get("afirma_hechos"))
