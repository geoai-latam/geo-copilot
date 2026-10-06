"""FH.6 — la simbología que el USUARIO elige a mano, calculada con el MISMO código que usa
el agente (`SymbologyAgent.generate_symbology`): cortes de clase, colores de la rampa,
categorías. Aquí no decide nadie: el diseño (tipo, campo, método, clases, rampa, color)
viene del usuario; el código solo calcula. Así el editor y el agente nunca clasifican
distinto.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
from geo_copilot.agents.symbology_agent.styles import COLOR_PALETTES

#: Lo que el usuario puede fijar en el editor (campos del StyleSpec).
CAMPOS_EDITABLES = ("symbology_type", "classification_field", "classification_method",
                    "num_classes", "color_scheme", "fill_color")


def rampas() -> dict[str, list[str]]:
    """Las rampas disponibles con sus colores (las mismas que usa el agente)."""
    return {getattr(k, "value", str(k)): list(v) for k, v in COLOR_PALETTES.items()}


async def clasificar_manual(geojson: dict, diseno: dict[str, Any], titulo: str | None = None) -> dict:
    """El StyleSpec (como dict) de un diseño elegido a mano sobre estos datos."""
    agente = SymbologyAgent(llm_client=None)  # sin LLM: solo el cálculo
    features = [f for f in (geojson.get("features") or []) if isinstance(f, dict)]
    tipos = {f["geometry"].get("type") for f in features if f.get("geometry")}
    props = [f.get("properties") or {} for f in features if f.get("properties")]
    analisis = {
        "empty": not features,
        "feature_count": len(features),
        "geometry_type": agente._get_primary_geometry_type(tipos),
        "geometry_types": list(tipos),
        "fields": agente._analyze_fields(props),
        "design": {k: diseno.get(k) for k in ("symbology_type", "classification_field", "classification_method",
                                              "color_scheme", "num_classes") if diseno.get(k) is not None},
    }
    preferencias = {"base_color": diseno["fill_color"]} if diseno.get("fill_color") else None
    config = await agente.generate_symbology(geojson, analisis, query="", preferences=preferencias)
    estilo = config.model_dump(mode="json")
    if titulo:
        estilo["layer_title"] = titulo
    estilo["reasoning"] = None  # nadie razonó: lo eligió el usuario
    return estilo
