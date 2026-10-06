"""
Tests para InsightsAgent (Fase 3).

Pruebas de MapGenerator, ChartGenerator, TableFormatter,
NarrativeGenerator y InsightsAgent.
"""

import json

import pytest

from geo_copilot.agents.insights_agent import (
    ChartGenerator,
    InsightsAgent,
    MapGenerator,
    NarrativeGenerator,
    TableFormatter,
)
from geo_copilot.agents.insights_agent.agent import OutputFormat
from geo_copilot.agents.insights_agent.chart_generator import ChartType
from geo_copilot.agents.insights_agent.map_generator import ColorScheme, MapStyle
from geo_copilot.agents.insights_agent.narrative_generator import NarrativeStyle
from geo_copilot.agents.insights_agent.table_formatter import TableFormat

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_geojson():
    """GeoJSON de ejemplo con puntos."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-74.0817, 4.6097]},
                "properties": {"name": "Punto A", "value": 100, "category": "tipo1", "distance_m": 150}
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-74.0900, 4.6200]},
                "properties": {"name": "Punto B", "value": 200, "category": "tipo2", "distance_m": 300}
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-74.0700, 4.6000]},
                "properties": {"name": "Punto C", "value": 150, "category": "tipo1", "distance_m": 450}
            },
        ]
    }


@pytest.fixture
def sample_polygon_geojson():
    """GeoJSON de ejemplo con polígonos."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-74.1, 4.6], [-74.0, 4.6], [-74.0, 4.7], [-74.1, 4.7], [-74.1, 4.6]]]
                },
                "properties": {"name": "Zona A", "area": 1000, "coverage_percent": 85}
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-74.0, 4.6], [-73.9, 4.6], [-73.9, 4.7], [-74.0, 4.7], [-74.0, 4.6]]]
                },
                "properties": {"name": "Zona B", "area": 1500, "coverage_percent": 60}
            },
        ]
    }


@pytest.fixture
def sample_records():
    """Lista de registros de ejemplo."""
    return [
        {"municipio": "Bogotá", "poblacion": 8000000, "area_km2": 1775},
        {"municipio": "Medellín", "poblacion": 2500000, "area_km2": 380},
        {"municipio": "Cali", "poblacion": 2200000, "area_km2": 564},
        {"municipio": "Barranquilla", "poblacion": 1200000, "area_km2": 154},
        {"municipio": "Cartagena", "poblacion": 1000000, "area_km2": 609},
    ]


@pytest.fixture
def map_generator():
    """Instancia de MapGenerator."""
    return MapGenerator()


@pytest.fixture
def chart_generator():
    """Instancia de ChartGenerator."""
    return ChartGenerator()


@pytest.fixture
def table_formatter():
    """Instancia de TableFormatter."""
    return TableFormatter()


@pytest.fixture
def narrative_generator():
    """Instancia de NarrativeGenerator."""
    return NarrativeGenerator()


@pytest.fixture
def insights_agent():
    """Instancia de InsightsAgent."""
    return InsightsAgent()


# ============================================================================
# Tests MapGenerator
# ============================================================================

class TestMapGenerator:
    """Tests para MapGenerator."""

    def test_create_point_map(self, map_generator, sample_geojson):
        """Test creación de mapa de puntos."""
        result = map_generator.create_point_map(
            sample_geojson,
            popup_fields=["name", "value"],
            title="Test Map"
        )

        assert result["type"] == "point_map"
        assert result["title"] == "Test Map"
        assert result["stats"]["feature_count"] == 3
        assert "center" in result
        assert "zoom" in result
        assert result["data"] == sample_geojson

    def test_create_choropleth_map(self, map_generator, sample_polygon_geojson):
        """Test creación de mapa coroplético."""
        result = map_generator.create_choropleth_map(
            sample_polygon_geojson,
            value_field="area",
            title="Coverage Map"
        )

        assert result["type"] == "choropleth_map"
        assert result["options"]["value_field"] == "area"
        assert result["stats"]["feature_count"] == 2
        assert result["stats"]["value_min"] == 1000
        assert result["stats"]["value_max"] == 1500

    def test_create_heatmap(self, map_generator, sample_geojson):
        """Test creación de mapa de calor."""
        result = map_generator.create_heatmap(
            sample_geojson,
            weight_field="value",
            title="Heatmap"
        )

        assert result["type"] == "heatmap"
        assert result["stats"]["point_count"] == 3
        assert len(result["data"]["points"]) == 3

    def test_create_cluster_map(self, map_generator, sample_geojson):
        """Test creación de mapa de clusters."""
        result = map_generator.create_cluster_map(
            sample_geojson,
            popup_fields=["name"],
            title="Cluster Map"
        )

        assert result["type"] == "cluster_map"
        assert result["options"]["cluster_radius"] == 50

    def test_create_multi_layer_map(self, map_generator, sample_geojson, sample_polygon_geojson):
        """Test creación de mapa multicapa."""
        layers = [
            {"name": "Points", "geojson": sample_geojson},
            {"name": "Polygons", "geojson": sample_polygon_geojson},
        ]

        result = map_generator.create_multi_layer_map(layers, title="Multi-Layer")

        assert result["type"] == "multi_layer_map"
        assert result["stats"]["layer_count"] == 2

    def test_empty_geojson(self, map_generator):
        """Test con GeoJSON vacío."""
        empty_geojson = {"type": "FeatureCollection", "features": []}
        result = map_generator.create_point_map(empty_geojson)

        assert result["type"] == "empty"
        assert result["stats"]["feature_count"] == 0

    def test_render_to_html(self, map_generator, sample_geojson):
        """Test renderizado a HTML."""
        map_config = map_generator.create_point_map(sample_geojson, title="HTML Test")
        html = map_generator.render_to_html(map_config)

        assert "<!DOCTYPE html>" in html
        assert "leaflet" in html.lower()
        assert "HTML Test" in html

    def test_calculate_center(self, map_generator, sample_geojson):
        """Test cálculo de centro geográfico."""
        features = sample_geojson["features"]
        center = map_generator._calculate_center(features)

        # Centro aproximado de los 3 puntos
        assert 4.6 < center[0] < 4.65
        assert -74.1 < center[1] < -74.0

    def test_geojson_string_input(self, map_generator, sample_geojson):
        """Test con GeoJSON como string."""
        geojson_str = json.dumps(sample_geojson)
        result = map_generator.create_point_map(geojson_str)

        assert result["stats"]["feature_count"] == 3


# ============================================================================
# Tests ChartGenerator
# ============================================================================

class TestChartGenerator:
    """Tests para ChartGenerator."""

    # NOTE Sprint E (2026-05-25): el schema de ChartGenerator pasó de
    # ``{type, data:{x:[], y:[]}, layout:{...}}`` a
    # ``{chart_type, x_key, y_key, data:[{record}], stats}``. Era
    # incompatible con ``Chart.tsx`` del frontend y disparaba el warning
    # amarillo "el backend no especificó x_key/y_key" en todos los charts.
    # Estos asserts validan el shape nuevo (records-list + chart_type).

    def test_create_bar_chart(self, chart_generator, sample_records):
        """Test creación de gráfico de barras."""
        result = chart_generator.create_bar_chart(
            sample_records,
            x_field="municipio",
            y_field="poblacion",
            title="Población por Municipio"
        )

        assert result["chart_type"] == ChartType.BAR.value
        assert result["x_key"] == "municipio"
        assert result["y_key"] == "poblacion"
        assert len(result["data"]) == 5
        assert result["stats"]["record_count"] == 5
        assert result["stats"]["max"] == 8000000

    def test_create_horizontal_bar_chart(self, chart_generator, sample_records):
        """Test gráfico de barras horizontal — ahora es flag, no chart_type."""
        result = chart_generator.create_bar_chart(
            sample_records,
            x_field="municipio",
            y_field="poblacion",
            horizontal=True
        )

        assert result["chart_type"] == "bar"
        assert result["horizontal"] is True

    def test_create_pie_chart(self, chart_generator, sample_records):
        """Test creación de gráfico circular."""
        result = chart_generator.create_pie_chart(
            sample_records,
            label_field="municipio",
            value_field="poblacion"
        )

        assert result["chart_type"] == ChartType.PIE.value
        assert result["x_key"] == "municipio"
        assert result["y_key"] == "poblacion"
        assert len(result["data"]) == 5
        assert result["stats"]["total"] == sum(r["poblacion"] for r in sample_records)

    def test_create_donut_chart(self, chart_generator, sample_records):
        """Test creación de gráfico donut — ahora es flag, no chart_type."""
        result = chart_generator.create_pie_chart(
            sample_records,
            label_field="municipio",
            value_field="poblacion",
            donut=True
        )

        assert result["chart_type"] == "pie"
        assert result["donut"] is True

    def test_create_line_chart(self, chart_generator):
        """Test creación de gráfico de líneas (multi-series)."""
        data = [
            {"year": 2020, "value1": 100, "value2": 80},
            {"year": 2021, "value1": 120, "value2": 90},
            {"year": 2022, "value1": 140, "value2": 100},
        ]

        result = chart_generator.create_line_chart(
            data,
            x_field="year",
            y_fields=["value1", "value2"]
        )

        assert result["chart_type"] == ChartType.LINE.value
        assert result["x_key"] == "year"
        assert result["y_key"] == "value1"
        assert result["y_fields"] == ["value1", "value2"]
        assert len(result["data"]) == 3
        assert result["stats"]["point_count"] == 3
        assert result["stats"]["series_count"] == 2

    def test_create_scatter_chart(self, chart_generator, sample_records):
        """Test creación de gráfico de dispersión."""
        result = chart_generator.create_scatter_chart(
            sample_records,
            x_field="area_km2",
            y_field="poblacion"
        )

        assert result["chart_type"] == ChartType.SCATTER.value
        assert result["x_key"] == "area_km2"
        assert result["y_key"] == "poblacion"
        assert len(result["data"]) == 5
        assert "correlation" in result["stats"]

    def test_create_histogram(self, chart_generator, sample_records):
        """Test creación de histograma."""
        result = chart_generator.create_histogram(
            sample_records,
            value_field="poblacion",
            bins=5
        )

        assert result["chart_type"] == ChartType.HISTOGRAM.value
        # Sprint E: histograma se pre-binea a bar chart con bin_label / count.
        assert result["x_key"] == "poblacion"
        assert result["y_key"] == "frecuencia"
        assert "mean" in result["stats"]
        assert "median" in result["stats"]

    def test_create_box_plot(self, chart_generator, sample_records):
        """Test creación de box plot."""
        result = chart_generator.create_box_plot(
            sample_records,
            value_field="poblacion"
        )

        assert result["chart_type"] == ChartType.BOX.value
        # Sin group_field, hay un solo box: data = [{name, values}].
        assert len(result["data"]) == 1
        assert result["data"][0]["name"] == "poblacion"

    def test_create_treemap(self, chart_generator, sample_records):
        """Test creación de treemap."""
        result = chart_generator.create_treemap(
            sample_records,
            label_field="municipio",
            value_field="poblacion"
        )

        assert result["chart_type"] == ChartType.TREEMAP.value
        assert result["stats"]["node_count"] == 5

    def test_sort_and_limit(self, chart_generator, sample_records):
        """Test ordenamiento y límite en gráficos."""
        result = chart_generator.create_bar_chart(
            sample_records,
            x_field="municipio",
            y_field="poblacion",
            sort_by="-poblacion",
            limit=3
        )

        assert len(result["data"]) == 3
        # Verificar que está ordenado descendentemente — los records ya están
        # ordenados, el primero tiene la población mayor.
        assert result["data"][0]["poblacion"] > result["data"][1]["poblacion"]

    def test_geojson_input(self, chart_generator, sample_geojson):
        """Test con datos GeoJSON."""
        result = chart_generator.create_bar_chart(
            sample_geojson,
            x_field="name",
            y_field="value"
        )

        assert result["stats"]["record_count"] == 3

    def test_render_to_html(self, chart_generator, sample_records):
        """Test renderizado a HTML."""
        chart_config = chart_generator.create_bar_chart(
            sample_records,
            x_field="municipio",
            y_field="poblacion"
        )
        html = chart_generator.render_to_html(chart_config)

        assert "<!DOCTYPE html>" in html
        assert "plotly" in html.lower()


# ============================================================================
# Tests TableFormatter
# ============================================================================

class TestTableFormatter:
    """Tests para TableFormatter."""

    def test_format_table_html(self, table_formatter, sample_records):
        """Test formateo de tabla HTML."""
        result = table_formatter.format_table(
            sample_records,
            columns=["municipio", "poblacion"],
            title="Tabla de Municipios",
            format_type=TableFormat.HTML
        )

        assert result["total_rows"] == 5
        assert len(result["columns"]) == 2
        assert "<table" in result["rendered"]

    def test_format_table_markdown(self, table_formatter, sample_records):
        """Test formateo de tabla Markdown."""
        result = table_formatter.format_table(
            sample_records,
            columns=["municipio", "poblacion"],
            format_type=TableFormat.MARKDOWN
        )

        assert "|" in result["rendered"]
        assert "---" in result["rendered"]

    def test_format_table_csv(self, table_formatter, sample_records):
        """Test formateo de tabla CSV."""
        result = table_formatter.format_table(
            sample_records,
            columns=["municipio", "poblacion"],
            format_type=TableFormat.CSV
        )

        assert '","' in result["rendered"]

    def test_format_table_text(self, table_formatter, sample_records):
        """Test formateo de tabla texto."""
        result = table_formatter.format_table(
            sample_records,
            columns=["municipio", "poblacion"],
            format_type=TableFormat.TEXT
        )

        assert "Bogotá" in result["rendered"]
        assert "-" in result["rendered"]

    def test_create_summary_table(self, table_formatter):
        """Test creación de tabla resumen."""
        data = [
            {"region": "Norte", "ventas": 100},
            {"region": "Norte", "ventas": 150},
            {"region": "Sur", "ventas": 200},
            {"region": "Sur", "ventas": 250},
        ]

        result = table_formatter.create_summary_table(
            data,
            group_by="region",
            metrics=[
                {"field": "ventas", "agg": "sum", "label": "Total Ventas"},
                {"field": "ventas", "agg": "avg", "label": "Promedio"},
            ]
        )

        assert result["total_rows"] == 2

    def test_create_ranking_table(self, table_formatter, sample_records):
        """Test creación de tabla ranking."""
        result = table_formatter.create_ranking_table(
            sample_records,
            rank_field="poblacion",
            display_fields=["municipio"],
            top_n=3
        )

        assert result["total_rows"] == 3

    def test_create_comparison_table(self, table_formatter):
        """Test creación de tabla comparativa."""
        dataset1 = [{"value": 100}, {"value": 200}]
        dataset2 = [{"value": 150}, {"value": 250}]

        result = table_formatter.create_comparison_table(
            [dataset1, dataset2],
            compare_fields=["value"],
            dataset_labels=["Set A", "Set B"]
        )

        assert result["total_rows"] > 0

    def test_column_labels(self, table_formatter, sample_records):
        """Test etiquetas personalizadas de columnas."""
        result = table_formatter.format_table(
            sample_records,
            columns=["municipio", "poblacion"],
            column_labels={"municipio": "Ciudad", "poblacion": "Habitantes"}
        )

        assert "Ciudad" in result["headers"]
        assert "Habitantes" in result["headers"]

    def test_truncation(self, table_formatter):
        """Test truncamiento de filas."""
        large_data = [{"id": i, "value": i * 10} for i in range(200)]
        formatter = TableFormatter(max_rows=50)

        result = formatter.format_table(large_data)

        assert result["truncated"] is True
        assert len(result["rows"]) == 50

    def test_empty_data(self, table_formatter):
        """Test con datos vacíos."""
        result = table_formatter.format_table([])

        assert result["total_rows"] == 0
        assert "Sin datos" in result.get("message", "") or "No hay datos" in result.get("rendered", "")


# ============================================================================
# Tests NarrativeGenerator
# ============================================================================

class TestNarrativeGenerator:
    """Tests para NarrativeGenerator."""

    def test_generate_proximity_narrative(self, narrative_generator):
        """Test generación de narrativa de proximidad."""
        data = {
            "stats": {
                "feature_count": 150,
                "min_distance": 50,
                "max_distance": 500,
                "avg_distance": 275,
            },
            "config": {
                "source_entity": "parcelas",
                "target_entity": "vías",
                "distance": 500,
            }
        }

        result = narrative_generator._generate_from_template("proximity", data, None, NarrativeStyle.EXECUTIVE)

        assert "150" in result
        assert "500" in result
        assert "proximidad" in result.lower()

    def test_generate_aggregation_narrative(self, narrative_generator):
        """Test generación de narrativa de agregación."""
        data = {
            "stats": {
                "feature_count": 1000,
                "group_count": 10,
                "avg_per_group": 100,
                "max_value": 500,
                "max_name": "Bogotá",
                "min_value": 20,
                "min_name": "Villavicencio",
            },
            "config": {
                "data_entity": "parcelas",
                "admin_entity": "municipios",
            },
            "top_items": []
        }

        result = narrative_generator._generate_from_template("aggregation", data, None, NarrativeStyle.EXECUTIVE)

        assert "1,000" in result or "1000" in result
        assert "Bogotá" in result

    def test_generate_coverage_narrative(self, narrative_generator):
        """Test generación de narrativa de cobertura."""
        data = {
            "stats": {
                "coverage_percent": 75.5,
                "covered_count": 8,
                "total_areas": 10,
                "covered_area": 1500.5,
            },
            "config": {
                "service_entity": "hospitales",
                "area_entity": "municipios",
            }
        }

        result = narrative_generator._generate_from_template("coverage", data, None, NarrativeStyle.EXECUTIVE)

        assert "75.5%" in result
        assert "cobertura" in result.lower()

    def test_generate_quick_summary(self, narrative_generator):
        """Test generación de resumen rápido."""
        data = {
            "stats": {
                "feature_count": 500,
                "coverage_percent": 80.5,
            }
        }

        summary = narrative_generator.generate_quick_summary(data, max_sentences=2)

        assert "500" in summary
        # Count sentences by looking at period followed by space or end
        import re
        sentence_count = len(re.findall(r'\.\s|$', summary.strip()))
        assert sentence_count <= 3

    def test_style_bullet_points(self, narrative_generator):
        """Test aplicación de estilo bullets."""
        narrative = "Punto uno.\nPunto dos.\nPunto tres."
        styled = narrative_generator._apply_style(narrative, NarrativeStyle.BULLET_POINTS)

        assert "-" in styled

    def test_style_executive_no_fabrica_resumen(self, narrative_generator):
        """R2.4: el estilo EXECUTIVE ya NO fabrica un 'Resumen Ejecutivo' con
        regex de negritas (pseudo-síntesis mecánica) — la narrativa va tal
        cual; el resumen real lo redacta el LLM."""
        narrative = "Este es un análisis. **Dato importante: 100**. Conclusión."
        styled = narrative_generator._apply_style(narrative, NarrativeStyle.EXECUTIVE)

        assert "Resumen Ejecutivo" not in styled
        assert styled == narrative

    def test_describe_trend_solo_hechos(self, narrative_generator):
        """R2.4: el cambio temporal se reporta como HECHO (+X%), sin calificar
        'significativa'/'requiere atención' por umbral mágico."""
        trend_up = narrative_generator._describe_trend({"percent_change": 15})
        assert "+15.0%" in trend_up
        assert "significativa" not in trend_up.lower()

        trend_down = narrative_generator._describe_trend({"percent_change": -15})
        assert "-15.0%" in trend_down
        assert "atención" not in trend_down.lower()

        trend_stable = narrative_generator._describe_trend({"percent_change": 0})
        assert "0%" in trend_stable

    def test_conclusions_sin_recomendaciones_fabricadas(self, narrative_generator):
        """R2.4: coverage<50 ya no fabrica 'se recomienda expandir la
        infraestructura' — solo el hecho."""
        out = narrative_generator._generate_conclusions(
            {"stats": {"feature_count": 10, "coverage_percent": 30}}
        )
        assert "recomienda" not in out.lower()
        assert "30" in out

    def test_insights_sin_juicio_de_concentracion(self, narrative_generator):
        """R2.4: ratio>3 ya no afirma 'Concentración detectada' — solo el
        número."""
        out = narrative_generator._generate_additional_insights(
            {"stats": {"max_value": 100, "avg_per_group": 10}}
        )
        assert "concentración" not in out.lower()
        assert "10.0x" in out


# ============================================================================
# Tests InsightsAgent
# ============================================================================

class TestInsightsAgent:
    """Tests para InsightsAgent."""

    def test_agent_initialization(self, insights_agent):
        """Test inicialización del agente."""
        assert insights_agent.name == "InsightsAgent"
        assert insights_agent.map_generator is not None
        assert insights_agent.chart_generator is not None
        assert insights_agent.table_formatter is not None
        assert insights_agent.narrative_generator is not None

    def test_agent_tools(self, insights_agent):
        """Test herramientas registradas."""
        assert "generate_map" in insights_agent.tools
        assert "generate_chart" in insights_agent.tools
        assert "format_table" in insights_agent.tools
        assert "generate_narrative" in insights_agent.tools
        assert "create_report" in insights_agent.tools

    @pytest.mark.asyncio
    async def test_generate_insights_full_report(self, insights_agent, sample_geojson):
        """Test generación de insights completos."""
        analysis_result = {
            "geojson": sample_geojson,
            "stats": {
                "feature_count": 3,
                "min_distance": 150,
                "max_distance": 450,
                "avg_distance": 300,
            },
            "config": {
                "source_entity": "parcelas",
                "target_entity": "vías",
                "distance": 500,
            }
        }

        result = await insights_agent.generate_insights(
            analysis_result,
            analysis_type="proximity",
            output_format=OutputFormat.FULL_REPORT,
            title="Test Proximity Analysis"
        )

        assert result["title"] == "Test Proximity Analysis"
        assert "map" in result["components"]
        assert "narrative" in result["components"]
        assert "html" in result

    @pytest.mark.asyncio
    async def test_generate_insights_map_only(self, insights_agent, sample_geojson):
        """Test generación solo de mapa."""
        analysis_result = {"geojson": sample_geojson, "stats": {"feature_count": 3}}

        result = await insights_agent.generate_insights(
            analysis_result,
            analysis_type="proximity",
            output_format=OutputFormat.MAP_ONLY
        )

        assert "map" in result["components"]
        assert "narrative" not in result["components"]

    @pytest.mark.asyncio
    async def test_generate_insights_narrative_only(self, insights_agent, sample_geojson):
        """Test generación solo de narrativa."""
        analysis_result = {
            "geojson": sample_geojson,
            "stats": {"feature_count": 3}
        }

        result = await insights_agent.generate_insights(
            analysis_result,
            analysis_type="proximity",
            output_format=OutputFormat.NARRATIVE_ONLY
        )

        assert "narrative" in result["components"]
        assert "map" not in result["components"]

    @pytest.mark.asyncio
    async def test_generate_insights_aggregation(self, insights_agent, sample_polygon_geojson):
        """Test insights para análisis de agregación."""
        analysis_result = {
            "geojson": sample_polygon_geojson,
            "stats": {
                "feature_count": 2,
                "group_count": 2,
            }
        }

        result = await insights_agent.generate_insights(
            analysis_result,
            analysis_type="aggregation",
            output_format=OutputFormat.FULL_REPORT
        )

        assert result["analysis_type"] == "aggregation"
        assert "map" in result["components"]

    @pytest.mark.asyncio
    async def test_generate_insights_coverage(self, insights_agent, sample_polygon_geojson):
        """Test insights para análisis de cobertura."""
        analysis_result = {
            "geojson": sample_polygon_geojson,
            "stats": {
                "coverage_percent": 72.5,
                "covered_count": 8,
                "total_areas": 10,
            }
        }

        result = await insights_agent.generate_insights(
            analysis_result,
            analysis_type="coverage",
            output_format=OutputFormat.FULL_REPORT
        )

        assert "map" in result["components"]
        assert "narrative" in result["components"]

    # NOTE Sprint E (2026-05-25): se borraron los siguientes tests porque
    # ejercitaban APIs eliminadas del producto:
    #
    # - ``_generate_recommendations`` pasó a ser async + LLM-driven (antes
    #   eran plantillas con umbrales mágicos `coverage_percent<50` →
    #   "Prioridad alta"). Sin LLM mock, el método devuelve string vacío.
    #   La validación equivalente requiere un mock del LLMClient y vive en
    #   el e2e ``tests/manual_e2e/sprint_e_insights.py``.
    # - ``_find_numeric_field`` / ``_find_categorical_field``: las heurísticas
    #   semánticas se eliminaron — el LLM las decide en
    #   ``_llm_design_visualizations`` viendo el schema completo. Los
    #   helpers técnicos sobrevivientes (``_first_numeric_field`` /
    #   ``_first_string_field``) son solo fallback de último recurso y no
    #   tienen semántica que testear unitariamente.

    def test_extract_records(self, insights_agent, sample_geojson):
        """Test extracción de registros."""
        records = insights_agent._extract_records(sample_geojson)

        assert len(records) == 3
        assert "name" in records[0]

    def test_extract_top_items(self, insights_agent, sample_geojson):
        """Test extracción de top items."""
        result = {"geojson": sample_geojson}
        top_items = insights_agent._extract_top_items(result, limit=2)

        assert len(top_items) == 2
        assert top_items[0]["value"] >= top_items[1]["value"]

    def test_markdown_to_html(self, insights_agent):
        """Test conversión de Markdown a HTML."""
        markdown = "## Título\n\n**Texto importante**\n\n- Item 1\n- Item 2"
        html = insights_agent._markdown_to_html(markdown)

        assert "<h3>" in html
        assert "<strong>" in html
        assert "<li>" in html

    @pytest.mark.asyncio
    async def test_dashboard_output(self, insights_agent, sample_geojson):
        """Test salida tipo dashboard."""
        analysis_result = {
            "geojson": sample_geojson,
            "stats": {"feature_count": 3}
        }

        result = await insights_agent.generate_insights(
            analysis_result,
            analysis_type="proximity",
            output_format=OutputFormat.DASHBOARD
        )

        assert "html" in result
        assert "grid" in result["html"]


# ============================================================================
# Tests de Integración
# ============================================================================

class TestInsightsIntegration:
    """Tests de integración para InsightsAgent."""

    @pytest.mark.asyncio
    async def test_full_workflow(self, sample_geojson):
        """Test flujo completo de generación de insights."""
        # Simular resultado de análisis de GISAgent
        analysis_result = {
            "geojson": sample_geojson,
            "sql": "SELECT * FROM parcelas WHERE ST_DWithin(...)",
            "stats": {
                "feature_count": 3,
                "min_distance": 150,
                "max_distance": 450,
                "avg_distance": 300,
            },
            "config": {
                "source_entity": "parcelas",
                "target_entity": "vías principales",
                "distance": 500,
            },
            "context": "Análisis de parcelas cercanas a vías principales en Bogotá"
        }

        # Offline explícito: con `InsightsAgent()` el agente se auto-inicializa
        # desde settings y llamaba al LLM REAL sin estar marcado `-m llm`. En
        # local pasaba (hay API key en el .env); en CI, sin key, fallaba.
        agent = InsightsAgent(llm_client=False)
        result = await agent.generate_insights(
            analysis_result,
            analysis_type="proximity",
            output_format=OutputFormat.FULL_REPORT,
            include_recommendations=True
        )

        # Verificar todos los componentes
        assert "map" in result["components"]
        assert "narrative" in result["components"]
        assert "html" in result

        # Verificar que el HTML es válido
        html = result["html"]
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

        # Sin LLM no hay recomendaciones: el agente prefiere no decir nada a
        # inventarlas (`_generate_recommendations`), y la narrativa declara
        # honestamente que salió de la plantilla.
        narrative = result["components"]["narrative"]
        assert narrative["generated_with"] == "template"
        assert "recommendations" not in narrative

    @pytest.mark.asyncio
    async def test_recommendations_attached_when_generated(
        self, sample_geojson, monkeypatch
    ):
        """Cuando hay recomendaciones, se adjuntan a la narrativa y al texto."""
        agent = InsightsAgent(llm_client=False)

        async def fake_recommendations(analysis_type, stats):
            return "- Priorizar las parcelas a menos de 200 m."

        monkeypatch.setattr(agent, "_generate_recommendations", fake_recommendations)
        result = await agent.generate_insights(
            {"geojson": sample_geojson, "stats": {"feature_count": 3}},
            analysis_type="proximity",
            output_format=OutputFormat.FULL_REPORT,
            include_recommendations=True,
        )

        narrative = result["components"]["narrative"]
        assert narrative["recommendations"] == "- Priorizar las parcelas a menos de 200 m."
        assert "### Recomendaciones" in narrative["narrative"]

    @pytest.mark.asyncio
    async def test_empty_data_handling(self):
        """Test manejo de datos vacíos."""
        agent = InsightsAgent()
        analysis_result = {
            "geojson": {"type": "FeatureCollection", "features": []},
            "stats": {"feature_count": 0}
        }

        result = await agent.generate_insights(
            analysis_result,
            analysis_type="proximity"
        )

        # Debe manejar graciosamente datos vacíos
        assert result is not None
        assert result["metadata"]["record_count"] == 0


class TestNarrativeGeneratorLLM:
    """Regresión B2: la ruta LLM llamaba ``llm_client.generate()`` (inexistente).

    ``LLMClient`` solo expone ``chat()``; toda narrativa LLM lanzaba
    AttributeError, caía a plantilla en silencio y aun así reportaba
    ``generated_with: "llm"``. Estos tests fijan el contrato real.
    """

    class _FakeLLM:
        """LLM falso que registra llamadas a chat()."""

        def __init__(self, content="Narrativa generada por el LLM.", fail=False):
            self.content = content
            self.fail = fail
            self.chat_calls = []

        async def chat(self, messages, tools=None, temperature=0.1, max_tokens=4096):
            from geo_copilot.core.llm_client import LLMResponse
            self.chat_calls.append(messages)
            if self.fail:
                raise RuntimeError("LLM caído")
            return LLMResponse(content=self.content, model="fake")

    @pytest.mark.asyncio
    async def test_llm_path_uses_chat_and_reports_llm(self):
        fake = self._FakeLLM()
        gen = NarrativeGenerator(llm_client=fake)

        result = await gen.generate_narrative(
            "proximity", {"stats": {"feature_count": 3}}, use_llm=True
        )

        assert len(fake.chat_calls) == 1, "debe llamar chat() exactamente una vez"
        assert result["narrative"] == "Narrativa generada por el LLM."
        assert result["generated_with"] == "llm"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_and_reports_template(self):
        fake = self._FakeLLM(fail=True)
        gen = NarrativeGenerator(llm_client=fake)

        result = await gen.generate_narrative(
            "proximity", {"stats": {"feature_count": 3}}, use_llm=True
        )

        # Cae a plantilla, pero la metadata debe decir la verdad.
        assert result["generated_with"] == "template"
        assert result["narrative"]

    @pytest.mark.asyncio
    async def test_no_llm_uses_template(self):
        gen = NarrativeGenerator(llm_client=None)

        result = await gen.generate_narrative(
            "proximity", {"stats": {"feature_count": 3}}, use_llm=True
        )

        assert result["generated_with"] == "template"


class TestHTMLReportRecordCount:
    """Regresión B8: el reporte HTML crasheaba con ValueError cuando
    ``metadata.record_count`` faltaba (``'{:,}'.format('N/A')``).
    """

    def _renderer(self):
        from geo_copilot.agents.insights_agent.html_report import HTMLReportMixin

        class _R(HTMLReportMixin):
            pass

        return _R()

    def _output_format(self):
        from geo_copilot.agents.insights_agent.agent import OutputFormat

        return OutputFormat.FULL_REPORT

    def test_render_without_record_count_does_not_crash(self):
        renderer = self._renderer()
        result = {
            "title": "Análisis sin metadata",
            "analysis_type": "general",
            "components": {},
            # sin "metadata" → record_count ausente
        }

        html = renderer._render_full_report(result, self._output_format())

        assert "Análisis sin metadata" in html
        assert "N/A" in html  # se muestra N/A en vez de crashear

    def test_render_with_numeric_record_count_formats_thousands(self):
        renderer = self._renderer()
        result = {
            "title": "Con conteo",
            "analysis_type": "general",
            "components": {},
            "metadata": {"record_count": 12345},
        }

        html = renderer._render_full_report(result, self._output_format())

        assert "12,345" in html


class TestInsightsProcessForwardsQuery:
    """Regresión C3d-1: process() debe pasar la query a generate_insights."""

    @pytest.mark.asyncio
    async def test_query_is_forwarded(self):
        from unittest.mock import AsyncMock

        from geo_copilot.agents.insights_agent import InsightsAgent

        agent = InsightsAgent(llm_client=None)
        captured = {}

        async def _spy(**kwargs):
            captured.update(kwargs)
            return {"components": {}}

        agent.generate_insights = _spy

        await agent.process(
            query="muestra densidad por barrio",
            context={"analysis_result": {"stats": {}}, "analysis_type": "generic"},
        )

        assert captured.get("query") == "muestra densidad por barrio"
