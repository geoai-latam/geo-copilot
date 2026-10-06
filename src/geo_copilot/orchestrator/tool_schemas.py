"""Schemas de herramientas del agente — FACHADA sobre el registro (S1.3).

Hasta F1 este archivo escribía a mano, en formato de function-calling de OpenAI,
el catálogo de herramientas del bucle ReAct. Hoy cada herramienta es una
`Capability` registrada (`orchestrator/capabilities_core.py` para las `core.*`;
los servidores MCP registrarán las suyas en F3) y los schemas se GENERAN del
registro. Esta fachada conserva la API que usaban los llamadores y los tests.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.orchestrator.capabilities_core import ensure_core
from geo_copilot.platform.capabilities import ANSWER_TOOL, registry

__all__ = ["ANSWER_TOOL", "TOOL_NAMES", "get_tool_schemas", "is_known_tool", "tool_schema"]

ensure_core()

#: Nombres de las herramientas del núcleo + `answer`, fijados al importar.
#: Para una comprobación en vivo (incluye capacidades registradas después), usar
#: `is_known_tool`.
TOOL_NAMES: frozenset[str] = registry().names()


def get_tool_schemas(graph: Any = None) -> list[dict]:
    """Catálogo para `tools=` del LLM. Con `graph`, solo las disponibles."""
    return registry().tool_schemas(graph)


def is_known_tool(name: str | None) -> bool:
    """¿`name` es una herramienta registrada (o `answer`)?"""
    return bool(name) and (name == ANSWER_TOOL or registry().get(name) is not None)


def tool_schema(name: str) -> dict | None:
    """Schema de una herramienta por nombre, o `None` si no existe."""
    if name == ANSWER_TOOL:
        return next(s for s in registry().tool_schemas() if s["function"]["name"] == ANSWER_TOOL)
    cap = registry().get(name)
    return cap.tool_schema() if cap else None
