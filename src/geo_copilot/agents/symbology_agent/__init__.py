"""
SymbologyAgent - Agente especializado en simbología y visualización cartográfica.

Analiza los datos y genera configuraciones de simbología apropiadas
basadas en el tipo de datos, distribución estadística y contexto geográfico.
"""

from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
from geo_copilot.agents.symbology_agent.styles import (
    ClassificationMethod,
    ColorScheme,
    SymbologyConfig,
    SymbolType,
)

__all__ = [
    "SymbologyAgent",
    "SymbologyConfig",
    "ColorScheme",
    "SymbolType",
    "ClassificationMethod",
]
