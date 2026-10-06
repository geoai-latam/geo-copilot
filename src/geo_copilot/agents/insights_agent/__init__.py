"""
InsightsAgent: Agente de visualización y narrativas.

Genera mapas interactivos, gráficos estadísticos, tablas formateadas
y narrativas automáticas a partir de resultados de análisis espaciales.
"""

from geo_copilot.agents.insights_agent.agent import InsightsAgent
from geo_copilot.agents.insights_agent.chart_generator import ChartGenerator
from geo_copilot.agents.insights_agent.map_generator import MapGenerator
from geo_copilot.agents.insights_agent.narrative_generator import NarrativeGenerator
from geo_copilot.agents.insights_agent.table_formatter import TableFormatter

__all__ = [
    "InsightsAgent",
    "MapGenerator",
    "ChartGenerator",
    "TableFormatter",
    "NarrativeGenerator",
]
