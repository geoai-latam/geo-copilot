"""
InsightsAgent: Agente de visualización y narrativas.

Coordina la generación de mapas, gráficos, tablas y narrativas
a partir de resultados de análisis espaciales.
"""

from enum import Enum
from typing import Any

from geo_copilot.agents.base import AgentResponse, BaseAgent
from geo_copilot.agents.insights_agent.chart_generator import ChartGenerator
from geo_copilot.agents.insights_agent.diseno_viz import DisenoVizMixin
from geo_copilot.agents.insights_agent.encaje import EncajeMixin
from geo_copilot.agents.insights_agent.html_report import HTMLReportMixin
from geo_copilot.agents.insights_agent.inferencia import InferenciaMixin
from geo_copilot.agents.insights_agent.map_generator import MapGenerator, MapStyle
from geo_copilot.agents.insights_agent.narrative_generator import NarrativeGenerator, NarrativeStyle
from geo_copilot.agents.insights_agent.productos import ProductosMixin
from geo_copilot.agents.insights_agent.seguimiento import SeguimientoMixin
from geo_copilot.agents.insights_agent.table_formatter import TableFormatter
from geo_copilot.core.config import settings
from geo_copilot.core.llm_client import LLMClient
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


class OutputFormat(str, Enum):
    """Formatos de salida del InsightsAgent."""
    FULL_REPORT = "full_report"      # Todo: mapa, gráficos, tabla, narrativa
    MAP_ONLY = "map_only"            # Solo mapa
    CHARTS_ONLY = "charts_only"      # Solo gráficos
    TABLE_ONLY = "table_only"        # Solo tabla
    NARRATIVE_ONLY = "narrative"     # Solo narrativa
    DASHBOARD = "dashboard"          # Layout tipo dashboard


class InsightsAgent(EncajeMixin, InferenciaMixin, SeguimientoMixin, DisenoVizMixin, ProductosMixin, HTMLReportMixin, BaseAgent):
    """
    Agente especializado en visualización y generación de insights.

    Capacidades:
    - Generar mapas interactivos (puntos, coropléticos, heatmaps)
    - Crear gráficos estadísticos (barras, líneas, pie, scatter)
    - Formatear tablas (HTML, Markdown, CSV)
    - Generar narrativas automáticas
    - Combinar todo en reportes completos
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        map_style: MapStyle = MapStyle.CARTODB_POSITRON,
        narrative_style: NarrativeStyle = NarrativeStyle.EXECUTIVE,
        *,
        agent_hub: Any = None,
    ):
        """Inicializar InsightsAgent.

        Args:
            llm_client: Cliente LLM. `None` → auto-init desde settings.
                Pasar `False` fuerza modo offline (sin LLM, sin diseño visual).
            map_style: Estilo de mapa por defecto.
            narrative_style: Estilo de narrativa por defecto.
            agent_hub: AgentHub para A2A. ``None`` cuando el InsightsAgent
                se usa standalone (tests legacy). Permite que el InsightsAgent
                consulte a otros agentes — en particular,
                ``SymbologyAgent.suggest_palette_for`` para que los charts
                que genera tengan colores consistentes con el mapa.
        """
        super().__init__(
            name="InsightsAgent",
            description="Agente de visualización y generación de insights"
        )

        # Auto-init si no llega (consistente con DataAgent/RouterAgent/Symbology).
        if llm_client is False:
            self.llm_client = None
        elif llm_client is None:
            self.llm_client = LLMClient.from_settings(settings)
        else:
            self.llm_client = llm_client

        self.agent_hub = agent_hub
        self.map_generator = MapGenerator(default_style=map_style)
        self.chart_generator = ChartGenerator()
        self.table_formatter = TableFormatter()
        self.narrative_generator = NarrativeGenerator(
            llm_client=self.llm_client,
            default_style=narrative_style,
        )

        self._register_tools()

    def _register_tools(self) -> None:
        """Registrar herramientas del agente."""
        self.tools = {
            "generate_map": self._tool_generate_map,
            "generate_chart": self._tool_generate_chart,
            "format_table": self._tool_format_table,
            "generate_narrative": self._tool_generate_narrative,
            "create_report": self._tool_create_report,
        }

    async def process(self, query: str, context: dict | None = None) -> AgentResponse:
        """
        Procesar una solicitud de visualización.

        Args:
            query: Consulta en lenguaje natural
            context: Contexto con datos de análisis previo

        Returns:
            AgentResponse con visualizaciones generadas
        """
        if not context or "analysis_result" not in context:
            return AgentResponse(
                success=False,
                message="Se requiere contexto con 'analysis_result' para generar visualizaciones"
            )

        analysis_result = context["analysis_result"]
        analysis_type = context.get("analysis_type", "generic")
        output_format = context.get("output_format", OutputFormat.FULL_REPORT)

        try:
            insights = await self.generate_insights(
                analysis_result=analysis_result,
                analysis_type=analysis_type,
                output_format=output_format,
                title=context.get("title"),
                include_recommendations=context.get("include_recommendations", True),
                # C3d-1: pasar la query del usuario para que el LLM diseñe
                # las visualizaciones con su intención. Antes no se pasaba y
                # _llm_design_visualizations siempre corría con query vacío.
                query=query,
            )

            return AgentResponse(
                success=True,
                message=f"Insights generados exitosamente: {len(insights.get('components', {}))} componentes",
                data=insights
            )

        except Exception as e:  # captura amplia a propósito: frontera del agente: LLM + generadores de charts/mapas; el fallo se devuelve como AgentResponse
            logger.error(f"Error generating insights: {e}", exc_info=True)
            return AgentResponse(
                success=False,
                message=f"Error generando insights: {str(e)}"
            )

    # ==========================================================================
    # A2A — capacidades públicas para que otros agentes consulten al InsightsAgent.
    # Se invocan via ``hub.call(target="insights_agent", method="...")``.
    # ==========================================================================

    # Pares (singular, plural) por tipo de geometría — evita
    # la heurística frágil ``label[:-1]`` que producía "geometrías mixta"
    # para colecciones mixtas con feature_count=1.
    _GEOM_LABELS: dict[str, tuple[str, str]] = {
        "point": ("punto", "puntos"),
        "multipoint": ("punto", "puntos"),
        "linestring": ("línea", "líneas"),
        "multilinestring": ("línea", "líneas"),
        "polygon": ("polígono", "polígonos"),
        "multipolygon": ("polígono", "polígonos"),
        "geometrycollection": ("geometría", "geometrías mixtas"),
    }

    # Set de viz_types que evaluate_visualization_fit reconoce. Si el
    # caller pasa algo fuera del set, devolvemos appropriate=False con
    # reason explícita — antes caíamos al "ok sin restricciones" final
    # que bypasseaba el override (issue real cuando el LLM elegía
    # "graduated_color" mistyped).
    _KNOWN_VIZ_TYPES: frozenset[str] = frozenset({
        "heatmap", "cluster", "choropleth", "graduated_colors",
        "graduated_symbols", "unique_values", "single_symbol", "point_map",
    })

    def get_capabilities(self) -> dict:
        """
        Retornar las capacidades del agente.

        Returns:
            Diccionario con capacidades categorizadas
        """
        return {
            "visualization": {
                "maps": [
                    "point_map",
                    "choropleth_map",
                    "heatmap",
                    "cluster_map",
                    "multi_layer_map",
                ],
                "charts": [
                    "bar_chart",
                    "horizontal_bar",
                    "pie_chart",
                    "donut_chart",
                    "line_chart",
                    "area_chart",
                    "scatter_chart",
                    "histogram",
                    "box_plot",
                    "treemap",
                ],
                "tables": [
                    "html_table",
                    "markdown_table",
                    "csv_export",
                    "summary_table",
                    "ranking_table",
                    "comparison_table",
                ],
            },
            "narrative": {
                "styles": ["technical", "executive", "casual", "bullets"],
                "languages": ["es"],
                "features": [
                    "automatic_insights",
                    "recommendations",
                    "trend_analysis",
                    "gap_analysis",
                ],
            },
            "output_formats": [
                "full_report",
                "map_only",
                "charts_only",
                "table_only",
                "narrative_only",
                "dashboard",
            ],
        }

    async def generate_insights(
        self,
        analysis_result: dict[str, Any],
        analysis_type: str,
        output_format: OutputFormat = OutputFormat.FULL_REPORT,
        title: str | None = None,
        include_recommendations: bool = True,
        query: str = "",
    ) -> dict[str, Any]:
        """Generar insights completos a partir de resultados de análisis.

        Args:
            analysis_result: Resultado del análisis (GeoJSON, estadísticas, etc.)
            analysis_type: Tipo de análisis (proximity, aggregation, coverage, ...)
            output_format: Formato de salida deseado
            title: Título del reporte
            include_recommendations: Incluir recomendaciones
            query: Query original del usuario (el LLM la usa para decidir viz)

        Returns:
            Insights generados con visualizaciones y narrativas.
        """
        logger.info(f"Generating insights for {analysis_type} analysis")

        title = title or self._generate_title(analysis_type, analysis_result)

        geojson_data = analysis_result.get("geojson") or analysis_result.get("data")
        stats = analysis_result.get("stats", {})

        # El LLM diseña el plan visual completo: tipo de mapa, lista de
        # gráficos a generar con sus x_key/y_key. Antes había if/elif por
        # analysis_type fijo que ignoraba la query del usuario.
        design = await self._llm_design_visualizations(
            query=query,
            analysis_type=analysis_type,
            geojson_data=geojson_data,
            analysis_result=analysis_result,
            stats=stats,
        )

        # A2A (2026-05-31): consultar al SymbologyAgent qué paleta usar
        # para los charts de esta sesión, así el color del chart matchea
        # el color del mapa en vez de cada componente eligiendo por su
        # cuenta. Hoy se expone via ``result["theme"]`` para que el
        # frontend lo consuma; cuando un caller necesite la paleta para
        # crear el chart, leer ``theme.palette_hex``.
        theme = await self._a2a_select_palette(
            design=design,
            geojson_data=geojson_data,
            analysis_result=analysis_result,
        )

        result: dict[str, Any] = {
            "title": title,
            "analysis_type": analysis_type,
            "output_format": output_format.value,
            "components": {},
            "design": design,
            "theme": theme,  # paleta consistente via A2A SymbologyAgent
            "metadata": {
                "record_count": stats.get("feature_count", 0),
                "generated_components": [],
            }
        }

        # Mapa
        if output_format in [OutputFormat.FULL_REPORT, OutputFormat.MAP_ONLY, OutputFormat.DASHBOARD]:
            if geojson_data:
                map_config = await self._generate_appropriate_map(
                    analysis_type, geojson_data, stats, title, design=design
                )
                result["components"]["map"] = map_config
                result["metadata"]["generated_components"].append("map")

        # Charts
        if output_format in [OutputFormat.FULL_REPORT, OutputFormat.CHARTS_ONLY, OutputFormat.DASHBOARD]:
            charts = await self._generate_appropriate_charts(
                analysis_type, analysis_result, stats, design=design
            )
            if charts:
                result["components"]["charts"] = charts
                result["metadata"]["generated_components"].append("charts")

        if output_format in [OutputFormat.FULL_REPORT, OutputFormat.TABLE_ONLY, OutputFormat.DASHBOARD]:
            table = self._generate_appropriate_table(
                analysis_type, analysis_result, stats, design=design
            )
            if table:
                result["components"]["table"] = table
                result["metadata"]["generated_components"].append("table")

        if output_format in [OutputFormat.FULL_REPORT, OutputFormat.NARRATIVE_ONLY]:
            narrative = await self._generate_narrative_for_analysis(
                analysis_type, analysis_result, stats, title, include_recommendations
            )
            result["components"]["narrative"] = narrative
            result["metadata"]["generated_components"].append("narrative")

        # Generar HTML combinado si es reporte completo o dashboard
        if output_format in [OutputFormat.FULL_REPORT, OutputFormat.DASHBOARD]:
            result["html"] = self._render_full_report(result, output_format)

        logger.info(f"Generated insights with {len(result['metadata']['generated_components'])} components")
        return result

    # Sprint E: eliminadas las heurísticas semánticas `_find_*_field` que
    # decidían qué campo usar para análisis (numérico/categórico/fecha)
    # tomando el PRIMERO que matcheaba. Ahora la elección semántica la hace
    # el LLM en `_llm_design_visualizations`.
    #
    # Los helpers debajo son SOLO técnicos (no semánticos): se usan como
    # fallback de último recurso cuando no hay diseño LLM disponible, p.ej.
    # para `_extract_top_items` y `_generate_appropriate_table` en código
    # que aún no recibe el design.

    # Tool wrappers
    async def _tool_generate_map(self, **kwargs) -> dict:
        """Tool wrapper para generación de mapas."""
        return self.map_generator.create_point_map(**kwargs)

    async def _tool_generate_chart(self, **kwargs) -> dict:
        """Tool wrapper para generación de gráficos."""
        chart_type = kwargs.pop("chart_type", "bar")
        if chart_type == "bar":
            return self.chart_generator.create_bar_chart(**kwargs)
        elif chart_type == "pie":
            return self.chart_generator.create_pie_chart(**kwargs)
        elif chart_type == "line":
            return self.chart_generator.create_line_chart(**kwargs)
        return {}

    async def _tool_format_table(self, **kwargs) -> dict:
        """Tool wrapper para formateo de tablas."""
        return self.table_formatter.format_table(**kwargs)

    async def _tool_generate_narrative(self, **kwargs) -> dict:
        """Tool wrapper para generación de narrativas."""
        return await self.narrative_generator.generate_narrative(**kwargs)

    async def _tool_create_report(self, **kwargs) -> dict:
        """Tool wrapper para creación de reportes."""
        return await self.generate_insights(**kwargs)
