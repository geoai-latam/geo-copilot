"""
Generador de gráficos estadísticos.

Schema de salida (Sprint E) — compatible con `Chart.tsx` del frontend:

    {
        "chart_type": "bar" | "line" | "pie" | "histogram" | "scatter" | ...,
        "title": str,
        "x_key": str | None,            # nombre del campo eje X
        "y_key": str | None,            # nombre del campo eje Y
        "data": [{x_key: ..., y_key: ...}, ...],  # records reales (NO arrays extraídos)
        "stats": {...},
    }

Antes el shape era `{type, data:{x:[], y:[]}, layout:{x_label,y_label}}` —
incompatible con el frontend que exige `data: [{record}], x_key, y_key`.
La incompatibilidad hacía que TODOS los gráficos mostraran warning amarillo
"el backend no especificó x_key/y_key" tras Sprint A. Ahora se renderizan.
"""

import json
from enum import Enum
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# F4: los gráficos categóricos y los numéricos viven en sus módulos (mixins).
from geo_copilot.agents.insights_agent.graficos_categoricos import (
    GraficosCategoricosMixin,
)
from geo_copilot.agents.insights_agent.graficos_numericos import (
    GraficosNumericosMixin,
)


class ChartType(str, Enum):
    """Tipos de gráficos disponibles."""
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    LINE = "line"
    PIE = "pie"
    DONUT = "donut"
    SCATTER = "scatter"
    HISTOGRAM = "histogram"
    BOX = "box"
    AREA = "area"
    TREEMAP = "treemap"


class ChartGenerator(GraficosCategoricosMixin, GraficosNumericosMixin):
    """
    Generador de gráficos estadísticos con Plotly.

    Soporta:
    - Gráficos de barras (vertical/horizontal)
    - Gráficos de líneas y áreas
    - Gráficos circulares (pie/donut)
    - Scatter plots
    - Histogramas y box plots
    - Treemaps para datos jerárquicos
    """

    # Paleta de colores por defecto
    DEFAULT_COLORS = [
        "#636EFA", "#EF553B", "#00CC96", "#AB63FA",
        "#FFA15A", "#19D3F3", "#FF6692", "#B6E880",
        "#FF97FF", "#FECB52"
    ]

    def __init__(self, color_palette: list[str] | None = None):
        """
        Inicializar generador de gráficos.

        Args:
            color_palette: Paleta de colores personalizada
        """
        self.color_palette = color_palette or self.DEFAULT_COLORS








    def render_to_html(self, chart_config: dict[str, Any]) -> str:
        """Renderizar a HTML standalone (para PDF / reports offline).

        Convierte el schema nuevo (`{chart_type, x_key, y_key, data:[{record}]}`)
        a un payload de Plotly y lo inyecta en HTML standalone.
        """
        chart_type = chart_config.get("chart_type") or chart_config.get("type", "bar")
        title = chart_config.get("title", "Gráfico")

        plotly_data = json.dumps(self._convert_to_plotly(chart_config))
        plotly_layout = json.dumps({
            "title": title,
            "xaxis": {"title": chart_config.get("x_label", "")},
            "yaxis": {"title": chart_config.get("y_label", "")},
            "showlegend": chart_config.get("color_field") is not None
                          or (chart_config.get("y_fields") and len(chart_config["y_fields"]) > 1),
        })

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        #chart {{ width: 100%; height: 500px; }}
        .stats {{ margin-top: 20px; padding: 15px; background: #f5f5f5; border-radius: 4px; }}
    </style>
</head>
<body>
    <div id="chart"></div>
    <div class="stats">
        <h4>Estadísticas</h4>
        <pre>{json.dumps(chart_config.get('stats', {}), indent=2)}</pre>
    </div>
    <script>
        var data = {plotly_data};
        var layout = {plotly_layout};
        Plotly.newPlot('chart', data, layout, {{responsive: true}});
    </script>
</body>
</html>
"""
        return html

    def _extract_records(self, data: list | dict) -> list[dict]:
        """Extraer registros de diferentes formatos de datos."""
        if isinstance(data, list):
            return data

        # Si es GeoJSON
        if "features" in data:
            return [f.get("properties", {}) for f in data.get("features", [])]

        # Si es un dict con data
        if "data" in data:
            return self._extract_records(data["data"])

        return [data]



    def _convert_to_plotly(self, config: dict) -> list:
        """Convertir el nuevo schema (`x_key/y_key/data:[{...}]`) a Plotly.

        Lee `data` como lista de records y extrae arrays con `x_key`/`y_key`.
        """
        chart_type = config.get("chart_type") or config.get("type", "bar")
        records = config.get("data") or []
        x_key = config.get("x_key")
        y_key = config.get("y_key")

        if chart_type == "bar":
            x_vals = [r.get(x_key) for r in records] if x_key else []
            y_vals = [r.get(y_key) for r in records] if y_key else []
            return [{
                "type": "bar",
                "x": y_vals if config.get("horizontal") else x_vals,
                "y": x_vals if config.get("horizontal") else y_vals,
                "orientation": "h" if config.get("horizontal") else "v",
            }]

        if chart_type in ("pie", "donut"):
            labels = [r.get(x_key) for r in records] if x_key else []
            values = [r.get(y_key) for r in records] if y_key else []
            return [{
                "type": "pie",
                "labels": labels,
                "values": values,
                "hole": 0.4 if (chart_type == "donut" or config.get("donut")) else 0,
            }]

        if chart_type in ("line", "area"):
            x_vals = [r.get(x_key) for r in records] if x_key else []
            traces = []
            y_fields = config.get("y_fields") or ([y_key] if y_key else [])
            for yf in y_fields:
                traces.append({
                    "type": "scatter",
                    "mode": "lines",
                    "x": x_vals,
                    "y": [r.get(yf) for r in records],
                    "name": yf,
                    "fill": "tozeroy" if (chart_type == "area" or config.get("fill")) else None,
                })
            return traces

        if chart_type == "scatter":
            return [{
                "type": "scatter",
                "mode": "markers",
                "x": [r.get(x_key) for r in records] if x_key else [],
                "y": [r.get(y_key) for r in records] if y_key else [],
                "text": [r.get(config.get("label_field")) for r in records] if config.get("label_field") else [],
            }]

        if chart_type == "histogram":
            # Si tenemos raw_values usamos plotly histogram; si solo bins,
            # convertimos a bar.
            raw = config.get("raw_values")
            if raw:
                return [{"type": "histogram", "x": raw}]
            return [{
                "type": "bar",
                "x": [r.get(x_key) for r in records],
                "y": [r.get(y_key) for r in records],
            }]

        if chart_type == "box":
            return [{
                "type": "box",
                "y": box.get("values", []),
                "name": box.get("name", ""),
            } for box in records]

        if chart_type == "treemap":
            return [{
                "type": "treemap",
                "labels": [r.get(x_key) for r in records],
                "values": [r.get(y_key) for r in records],
                "parents": [r.get(config.get("parent_field"), "") for r in records],
            }]

        return [{"type": "bar", "x": [], "y": []}]

    def _empty_chart_config(self, title: str | None = None) -> dict[str, Any]:
        """Configuración para gráfico vacío."""
        return {
            "chart_type": "empty",
            "title": title or "Sin datos",
            "x_key": None,
            "y_key": None,
            "data": [],
            "stats": {"record_count": 0},
            "message": "No hay datos para mostrar",
        }
