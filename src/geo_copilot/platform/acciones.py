"""FH.8 — acciones contextuales: las capacidades del registro que se pueden aplicar a lo que
el usuario señala en el mapa (un elemento, lo seleccionado, un dibujo, una capa).

Sale SOLO de lo que cada capacidad declara (`geo_inputs`: qué recibe del mapa y qué tipos de
geometría tienen sentido): una tool nueva de un MCP G1 aparece sola en el menú de los tipos
que acepta, sin código de frontend. El menú ejecuta la MISMA capacidad que usa el agente.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.platform.capabilities import Capability, registry

#: Lo que el menú puede poner en un argumento: una capa / lo seleccionado / su geometría.
_DEL_MAPA = {"geometry", "layer_ref", "dataset", "bbox"}


def _plano(tipo: str) -> str:
    return tipo.removeprefix("Multi")


def admite(spec: dict[str, Any], geometria: str | None) -> bool:
    """¿Este argumento acepta algo del mapa de ese tipo de geometría? Sin tipos declarados, cualquiera."""
    if not set(spec.get("accepts") or []) & _DEL_MAPA:
        return False
    tipos = spec.get("geometry_types")
    return not tipos or geometria is None or _plano(geometria) in {_plano(t) for t in tipos}


def _habilitada(cap: Capability) -> bool:
    if not cap.provider.startswith("mcp:"):
        return True
    from geo_copilot.platform.mcp.hub import hub_actual

    hub = hub_actual()
    est = hub.tools.get(cap.tool_name) if hub is not None else None
    return est is not None and est.habilitada


def objetivo(cap: Capability) -> str | None:
    """El argumento que recibe lo señalado: el PRIMERO que declara recibir algo del mapa."""
    return next((k for k, v in cap.geo_inputs.items() if set(v.get("accepts") or []) & _DEL_MAPA), None)


def _titulo(texto: str, maximo: int = 80) -> str:
    """La primera frase (una descripción de servidor cortada a media frase se lee mal)."""
    import re

    frase = re.split(r"(?<=\.)\s", texto.strip(), maxsplit=1)[0].rstrip(".")
    return frase if len(frase) <= maximo else frase[: maximo - 1].rstrip() + "…"


def aplicables(geometria: str | None) -> list[dict[str, Any]]:
    """Las capacidades aplicables a algo de ese tipo de geometría, con su formulario."""
    from geo_copilot.platform.capabilities import rol_minimo
    from geo_copilot.platform.identidad.principal import principal_actual

    quien = principal_actual()
    salida = []
    for cap in registry().available(None):
        arg = objetivo(cap)
        if arg is None or cap.risk == "write" or not _habilitada(cap):
            continue  # escribir en un sistema externo se pide por el chat (HITL)
        if quien is not None and not quien.puede(rol_minimo(cap)):
            continue  # F6: no se ofrece lo que a este usuario se le negaría (un visor no calcula)
        if not admite(cap.geo_inputs[arg], geometria):
            continue
        servidor = cap.provider.removeprefix("mcp:") if cap.provider.startswith("mcp:") else "core"
        titulo = _titulo(cap.blurb.split("] ", 1)[-1] if cap.blurb.startswith("[") else cap.blurb)
        salida.append({
            "server": servidor,
            "tool": cap.id.rsplit(".", 1)[-1],
            "herramienta": cap.tool_name,
            "titulo": titulo,
            "description": cap.description,
            "input_schema": cap.parameters,
            "geo": {"inputs": cap.geo_inputs},
            "objetivo": arg,
            "riesgo": cap.risk,
            "costo": cap.cost,
            "estado": "habilitada",
        })
    return salida
