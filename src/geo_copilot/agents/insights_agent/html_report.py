"""
HTML report rendering — extraído de ``InsightsAgent`` en Fase 6 #7.

``InsightsAgent`` tenía un método ``_render_full_report`` de ~220 líneas
que generaba un HTML completo (head + CSS + scripts + secciones) más un
``_markdown_to_html`` auxiliar. Era el bloque más obeso del agente y
mezclaba responsabilidades (orquestación + presentación).

Estos dos métodos viven ahora en un mixin (``HTMLReportMixin``) que
``InsightsAgent`` consume vía herencia múltiple. Mantener la firma de
método en vez de pasar a funciones libres minimiza el cambio (los
``self.*`` references al resto del agente — ``self.chart_generator``,
los helpers de campos — siguen funcionando intactos).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from geo_copilot.agents.insights_agent.agent import OutputFormat
    from geo_copilot.agents.insights_agent.chart_generator import ChartGenerator


class HTMLReportMixin:
    """Mixin con la generación del reporte HTML completo del InsightsAgent.

    Asume que la clase consumidora expone:

    * ``self.chart_generator`` con ``_convert_to_plotly(chart) -> dict``.
    * Cualquier otra utilidad usada por los componentes (tablas/charts
      generators) en el agente.
    """

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        chart_generator: ChartGenerator


    def _render_full_report(self, result: dict[str, Any], output_format: OutputFormat) -> str:  # noqa: C901, PLR0912
        """Renderizar reporte completo en HTML."""
        from geo_copilot.agents.insights_agent.agent import OutputFormat as _OF

        title = result.get("title", "Reporte de Análisis")
        components = result.get("components", {})

        # B8: ``record_count`` puede faltar → default "N/A" (str); aplicar
        # el formato ``:,`` directamente sobre un str lanza ValueError y
        # tumba todo el render del reporte. Pre-formateamos defensivamente.
        _rc = result.get("metadata", {}).get("record_count")
        record_count_str = f"{_rc:,}" if isinstance(_rc, (int, float)) else "N/A"

        # Layout según formato.
        if output_format == _OF.DASHBOARD:
            layout_style = "display: grid; grid-template-columns: 1fr 1fr; gap: 20px;"
        else:
            layout_style = "max-width: 1200px; margin: 0 auto;"

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }}
        .report-container {{ {layout_style} }}
        .report-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 20px;
            grid-column: 1 / -1;
        }}
        .report-header h1 {{ margin: 0; font-size: 28px; }}
        .report-header .subtitle {{ opacity: 0.9; margin-top: 10px; }}
        .section {{
            background: white;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .section h2 {{
            color: #333;
            border-bottom: 2px solid #667eea;
            padding-bottom: 10px;
            margin-top: 0;
        }}
        .map-container {{ height: 400px; border-radius: 8px; overflow: hidden; }}
        .chart-container {{ height: 350px; }}
        .narrative {{ line-height: 1.8; color: #444; }}
        .narrative h3 {{ color: #667eea; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-card .value {{ font-size: 24px; font-weight: bold; color: #667eea; }}
        .stat-card .label {{ font-size: 12px; color: #666; margin-top: 5px; }}
    </style>
</head>
<body>
    <div class="report-container">
        <div class="report-header">
            <h1>{title}</h1>
            <div class="subtitle">
                Tipo de análisis: {result.get("analysis_type", "N/A")} |
                Elementos analizados: {record_count_str}
            </div>
        </div>
"""

        # Sección de mapa.
        if "map" in components:
            html += """
        <div class="section" style="grid-column: 1 / -1;">
            <h2>Visualización Espacial</h2>
            <div id="map" class="map-container"></div>
        </div>
"""

        # Sección de estadísticas rápidas.
        stats = result.get("metadata", {})
        if stats:
            html += """
        <div class="section">
            <h2>Estadísticas Clave</h2>
            <div class="stats-grid">
"""
            for key, value in stats.items():
                if key != "generated_components" and not key.startswith("_"):
                    label = key.replace("_", " ").title()
                    if isinstance(value, (int, float)):
                        html += f"""
                <div class="stat-card">
                    <div class="value">{value:,.0f}</div>
                    <div class="label">{label}</div>
                </div>
"""
            html += """
            </div>
        </div>
"""

        # Sección de gráficos.
        if "charts" in components and components["charts"]:
            for i, chart in enumerate(components["charts"][:4]):  # Max 4 charts
                html += f"""
        <div class="section">
            <h2>{chart.get("title", f"Gráfico {i + 1}")}</h2>
            <div id="chart{i}" class="chart-container"></div>
        </div>
"""

        # Sección de tabla.
        if "table" in components:
            table = components["table"]
            html += f"""
        <div class="section" style="grid-column: 1 / -1;">
            <h2>Datos Detallados</h2>
            {table.get("rendered", "<p>Sin datos</p>")}
        </div>
"""

        # Sección de narrativa.
        if "narrative" in components:
            narrative = components["narrative"]
            html += f"""
        <div class="section" style="grid-column: 1 / -1;">
            <h2>Análisis y Conclusiones</h2>
            <div class="narrative">
                {self._markdown_to_html(narrative.get("narrative", ""))}
            </div>
        </div>
"""

        # Scripts para mapa y gráficos.
        html += """
    </div>
    <script>
"""

        # Inicializar mapa.
        if "map" in components:
            map_config = components["map"]
            center = map_config.get("center", [4.6097, -74.0817])
            zoom = map_config.get("zoom", 6)
            geojson = json.dumps(map_config.get("data", {}))

            html += f"""
        var map = L.map('map').setView([{center[0]}, {center[1]}], {zoom});
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '&copy; OpenStreetMap contributors'
        }}).addTo(map);

        var geojsonData = {geojson};
        if (geojsonData.features && geojsonData.features.length > 0) {{
            var layer = L.geoJSON(geojsonData, {{
                style: {{ color: '#667eea', weight: 2, fillOpacity: 0.5 }},
                pointToLayer: function(f, ll) {{
                    return L.circleMarker(ll, {{ radius: 8, fillColor: '#667eea', color: '#fff', weight: 2, fillOpacity: 0.8 }});
                }},
                onEachFeature: function(f, l) {{
                    if (f.properties) {{
                        // SEC-6c: build the popup with DOM nodes and
                        // ``textContent`` so feature property values from
                        // the DB/external sources cannot escape into HTML.
                        var container = document.createElement('div');
                        for (var k in f.properties) {{
                            var line = document.createElement('div');
                            var label = document.createElement('b');
                            label.textContent = k + ': ';
                            line.appendChild(label);
                            line.appendChild(document.createTextNode(String(f.properties[k])));
                            container.appendChild(line);
                        }}
                        l.bindPopup(container);
                    }}
                }}
            }}).addTo(map);
            map.fitBounds(layer.getBounds());
        }}
"""

        # Inicializar gráficos.
        if "charts" in components:
            for i, chart in enumerate(components["charts"][:4]):
                plotly_data = json.dumps(self.chart_generator._convert_to_plotly(chart))
                plotly_layout = json.dumps({
                    "title": chart.get("title", ""),
                    "margin": {"t": 40, "b": 40, "l": 60, "r": 20},
                    "paper_bgcolor": "transparent",
                    "plot_bgcolor": "transparent",
                })
                html += f"""
        Plotly.newPlot('chart{i}', {plotly_data}, {plotly_layout}, {{responsive: true}});
"""

        html += """
    </script>
</body>
</html>
"""
        return html

    def _markdown_to_html(self, markdown: str) -> str:
        """Convertir Markdown básico a HTML."""
        html = markdown
        html = re.sub(r"^### (.+)$", r"<h4>\1</h4>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
        html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
        html = re.sub(r"(<li>.*</li>\n)+", r"<ul>\g<0></ul>", html)
        html = re.sub(r"\n\n", "</p><p>", html)
        html = f"<p>{html}</p>"
        return html
