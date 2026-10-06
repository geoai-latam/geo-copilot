"""Structured outputs via function-calling forzado (A3, Fase 4 remediación).

Reemplaza el patrón frágil «"Responde SOLO JSON" + parse_json_from_llm +
default» en los 4 parsers críticos (router, planner, symbology, insights).
El LLM se ve FORZADO a llamar una única función con schema — los argumentos
llegan como JSON validado por el proveedor, así que la clase entera de
fallbacks de parseo (brace-slicing, defaults silenciosos) desaparece por
construcción.

Contrato «agentic no fallbacks»:
- si el modelo no llama la función o los args no parsean, se RE-PREGUNTA UNA
  vez con el problema adjunto;
- si vuelve a fallar ⇒ ``StructuredOutputError`` (fallo honesto del caller;
  aquí NUNCA se inventa un valor).

La validación SEMÁNTICA (whitelists de intents, action_types, etc.) sigue
siendo del caller — este módulo solo garantiza «un dict con la forma pedida».
"""

from __future__ import annotations

import json
from typing import Any

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


class StructuredOutputError(ValueError):
    """El LLM no produjo la llamada de función pedida (tras el re-intento)."""


def _extract_args(response, name: str) -> dict | None:
    """Args de la PRIMERA llamada a ``name`` en la respuesta, o None."""
    for tc in response.tool_calls or []:
        call = tc.get("function") or {} if isinstance(tc, dict) else {}
        if call.get("name") != name:
            continue
        try:
            args = json.loads(call.get("arguments") or "{}")
        except (ValueError, TypeError):
            return None
        return args if isinstance(args, dict) else None
    return None


async def structured_call(
    llm,
    messages: list[LLMMessage],
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> dict:
    """Fuerza al LLM a «responder» llamando ``name(parameters)``.

    Devuelve los argumentos de la llamada como dict. Un intento de
    recuperación (re-pregunta con el problema adjunto) y después
    ``StructuredOutputError`` — el caller decide cómo fallar honesto.
    """
    tool = {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }
    force = {"type": "function", "function": {"name": name}}

    work = list(messages)
    perfil = getattr(llm, "profile", None)
    if perfil is not None and getattr(perfil, "forced_tool_choice", True) is False:
        # Modelos que no admiten forzar la herramienta (p. ej. Claude Opus 5.5 / Fable, muchos
        # servidores locales): se pide por instrucción y el cliente manda tool_choice=auto.
        work.append(LLMMessage(role="user", content=(
            f"Responde ÚNICAMENTE llamando a la función `{name}` con el schema dado; sin texto libre.")))
    for attempt in (1, 2):
        response = await llm.chat(
            work, tools=[tool], temperature=temperature,
            max_tokens=max_tokens, tool_choice=force,
        )
        args = _extract_args(response, name)
        if args is not None:
            return args
        problem = (
            f"Tu respuesta anterior no invocó la función `{name}` con argumentos "
            f"JSON válidos. Llama a `{name}` exactamente con el schema dado — "
            f"sin texto libre."
        )
        logger.warning(
            f"[structured_call] intento {attempt}: '{name}' sin tool_call válido"
        )
        if attempt == 1:
            work = work + [LLMMessage(role="user", content=problem)]

    raise StructuredOutputError(
        f"El modelo no produjo una llamada válida a '{name}' tras 2 intentos."
    )
