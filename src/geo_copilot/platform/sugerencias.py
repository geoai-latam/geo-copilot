"""FH.8 — sugerencias de siguiente paso tras cada respuesta.

El LLM las propone viendo HECHOS: lo que pidió el usuario, lo que se le respondió, lo que
hay en el mapa y qué herramientas existen. El código no inventa ninguna ni las filtra por
palabras: solo las acota (2–3, cortas) y, si el LLM falla, no hay sugerencias (son una ayuda,
nunca sustituyen la respuesta).
"""

from __future__ import annotations

import asyncio
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

MAX_SUGERENCIAS = 3
_MAX_CARACTERES = 90
_TIEMPO_MAXIMO_S = 8.0


def _herramientas() -> str:
    """Las herramientas que ESTE usuario puede usar (F6: un visor no calcula; sugerirle un
    buffer o un área era ofrecerle algo que se le iba a denegar — V3 F6, E6.4)."""
    from geo_copilot.orchestrator.capabilities_core import ensure_core
    from geo_copilot.platform.capabilities import registry, rol_minimo
    from geo_copilot.platform.identidad.principal import principal_actual

    ensure_core()
    p = principal_actual()
    caps = [c for c in registry().available(None) if p is None or p.puede(rol_minimo(c))]
    return "\n".join(f"  - {c.blurb}" for c in caps)[:3000]


async def sugerir(llm: Any, *, consulta: str, respuesta: str, map_context: dict | None) -> list[str]:
    """2–3 pedidos que el usuario podría hacer a continuación, en su idioma. [] si no hay."""
    from geo_copilot.core.formatters import format_map_context
    from geo_copilot.core.llm_client import LLMMessage
    from geo_copilot.core.utils import parse_json_from_llm

    mapa = format_map_context(map_context) or "(el mapa está vacío)"
    prompt = f"""El usuario de un copiloto geoespacial pidió: «{consulta}»
Se le respondió: «{respuesta[:1500]}»

{mapa}

Lo que el copiloto sabe hacer:
{_herramientas()}

Primero decide si hay un análisis EN CURSO sobre lo que hay en el mapa. Si el mapa está vacío,
o el usuario dio la conversación por terminada, o no hay un siguiente paso claro, la respuesta
es una lista VACÍA (es una buena respuesta; no inventes un análisis nuevo).

Solo si lo hay: hasta {MAX_SUGERENCIAS} pedidos CORTOS (menos de {_MAX_CARACTERES} caracteres) que
este usuario podría hacer a continuación, escritos como él los escribiría, en su idioma; cada
uno útil para seguir SU análisis, posible con lo que hay en el mapa y esas herramientas, sin
repetir lo ya hecho y sin marcadores («X», «N»): si falta un valor, esa no.

Devuelve SOLO un JSON: {{"sugerencias": ["...", "..."]}}"""
    try:
        resp = await asyncio.wait_for(llm.chat([LLMMessage(role="user", content=prompt)]), _TIEMPO_MAXIMO_S)
        datos = parse_json_from_llm(resp.content)
    except Exception:  # una ayuda opcional: sin ella la respuesta sigue igual
        logger.warning("[sugerencias] no se pudieron generar", exc_info=True)
        return []
    brutas = datos.get("sugerencias") if isinstance(datos, dict) else None
    if not isinstance(brutas, list):
        return []
    limpias: list[str] = []
    for s in brutas:
        texto = " ".join(str(s).split())
        if texto and len(texto) <= _MAX_CARACTERES * 2 and texto.lower() not in {x.lower() for x in limpias}:
            limpias.append(texto)
    return limpias[:MAX_SUGERENCIAS]
