"""Qué admite cada modelo (hechos del PROVEEDOR, no juicio): el cliente decide con esto qué parámetros
enviar, para que cambiar de modelo sea cambiar ``LLM_MODEL`` y nada más.

Tres fuentes, en este orden:
1. ``LLM_MODEL_PROFILE`` (JSON) — el operador manda; imprescindible en Azure, donde ``LLM_MODEL`` es el
   nombre del *deployment* y no dice qué modelo hay detrás.
2. Inferencia por el nombre del modelo (familias conocidas).
3. Aprendizaje en caliente: si el proveedor rechaza un parámetro (400 «unsupported parameter»), el
   cliente lo quita, lo recuerda para ese cliente y repite UNA vez (``adaptar``). Así un modelo que
   no está en la tabla funciona igual en la segunda llamada.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, replace

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ModelProfile:
    # Muestreo: los modelos de razonamiento (OpenAI o-series/gpt-5, Claude Opus ≥4.7 / 5.x) lo rechazan.
    supports_temperature: bool = True
    # OpenAI de razonamiento exige ``max_completion_tokens``.
    max_tokens_param: str = "max_tokens"
    # Razonamiento: sus tokens de pensar cuentan en el tope de salida → un tope de 80 deja la respuesta vacía.
    reasoning: bool = False
    min_output_tokens: int = 0
    # ``tool_choice`` forzado (structured outputs). Si no, se pide la función por instrucción.
    forced_tool_choice: bool = True
    # Pedir al proveedor UNA herramienta por paso (`parallel_tool_calls=False` /
    # `disable_parallel_tool_use`). Apagado por defecto: MEDIDO con gpt-4.1-mini (Azure), enviarlo
    # empeoró el juicio del router de 18/20 a 9/20 (tests/test_llm_fh1_mapa, A/B). El bucle ya le dice
    # al modelo qué llamadas no ejecutó; se enciende por modelo con LLM_MODEL_PROFILE si conviene.
    parallel_tool_control: bool = False
    # Ventana de contexto (tokens). Informativa para presupuestos.
    context_window: int = 128_000


_RAZONAMIENTO = {"supports_temperature": False, "reasoning": True, "min_output_tokens": 8192}

# (proveedor, patrón del nombre) → rasgos. Primer patrón que casa gana.
_FAMILIAS: list[tuple[set[str], str, dict]] = [
    ({"openai", "azure", "openai_compatible"}, r"^(o\d|gpt-5)",
     dict(_RAZONAMIENTO, max_tokens_param="max_completion_tokens", context_window=200_000)),
    ({"openai", "azure", "openai_compatible"}, r"^gpt-4\.1", {"context_window": 1_000_000}),
    # Claude sin muestreo y con pensamiento: Opus 4.7+, Opus/Sonnet 5.x, Fable.
    ({"anthropic"}, r"^claude-(opus-5-5|fable)",
     dict(_RAZONAMIENTO, forced_tool_choice=False, context_window=1_000_000)),
    ({"anthropic"}, r"^claude-(opus-4-[7-9]|opus-5|sonnet-5|fable)", dict(_RAZONAMIENTO, context_window=1_000_000)),
    ({"anthropic"}, r"^claude-", {"context_window": 200_000}),
]


def inferir(provider: str, model: str, override: str | None = None) -> ModelProfile:
    base: dict = {}
    for proveedores, patron, rasgos in _FAMILIAS:
        if provider in proveedores and re.search(patron, (model or "").lower()):
            base = dict(rasgos)
            break
    if override:
        try:
            extra = json.loads(override)
            validos = {f.name for f in fields(ModelProfile)}
            base.update({k: v for k, v in extra.items() if k in validos})
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"LLM_MODEL_PROFILE no es un JSON válido: {exc}") from exc
    return ModelProfile(**base)


def salida(profile: ModelProfile, pedido: int) -> int:
    """Tope de salida efectivo: nunca por debajo del mínimo de un modelo que razona."""
    return max(pedido, profile.min_output_tokens)


# Rechazos del proveedor → rasgo que se apaga. Los mensajes de OpenAI/Azure/Anthropic nombran el parámetro.
_RECHAZOS: list[tuple[str, dict]] = [
    (r"max_tokens.*(not supported|unsupported)|use 'max_completion_tokens'",
     {"max_tokens_param": "max_completion_tokens"}),
    (r"temperature.*(not supported|unsupported|deprecated|does not support)|"
     r"(unsupported|not supported).*temperature", {"supports_temperature": False}),
    (r"parallel_tool_calls|disable_parallel_tool_use", {"parallel_tool_control": False}),
    (r"tool_choice", {"forced_tool_choice": False}),
]


def adaptar(profile: ModelProfile, error: Exception) -> ModelProfile | None:
    """Perfil corregido si ``error`` es el rechazo de un parámetro que podemos dejar de enviar."""
    texto = str(error)
    if "400" not in texto and "invalid_request" not in texto.lower() and "BadRequest" not in type(error).__name__:
        return None
    for patron, rasgo in _RECHAZOS:
        if re.search(patron, texto, re.IGNORECASE):
            nuevo = replace(profile, **rasgo)
            if nuevo != profile:
                logger.warning(f"[llm] el proveedor rechazó un parámetro; se ajusta el perfil del modelo: {rasgo}")
                return nuevo
    return None
