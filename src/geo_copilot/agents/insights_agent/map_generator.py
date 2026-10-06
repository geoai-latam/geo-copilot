"""
Generador de mapas interactivos.

Crea mapas con Folium y Plotly para visualizar resultados
de análisis espaciales.
"""

import json
from enum import Enum
from html import escape as html_escape
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# F4: los mapas por tipo viven en su módulo (mixin).
from geo_copilot.agents.insights_agent.mapas_tematicos import (
    ColorScheme,
    MapasTematicosMixin,
)


def _safe_json_for_script(obj: Any) -> str:
    """``json.dumps`` seguro para EMBEBER dentro de un ``<script>``.

    C3d-4 / TST-03: el popup se construye en runtime con ``createTextNode``
    (seguro), pero los datos GeoJSON se serializan e incrustan dentro del
    ``<script>`` del HTML. Sin escape, un valor de property con ``</script>``
    cierra el tag ante el parser HTML (que corre ANTES que el de JS) e inyecta
    el markup siguiente (``</script><img src=x onerror=...>`` → DOM vivo → XSS).
    Escapamos ``< > &`` y los separadores de línea JS a ``\\uXXXX``: sigue siendo
    JSON válido (JS los decodifica en el string) pero el parser HTML nunca ve
    ``</script>``.
    """
    return (
        json.dumps(obj)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


class MapStyle(str, Enum):
    """Estilos de mapa base disponibles."""
    OPENSTREETMAP = "OpenStreetMap"
    CARTODB_POSITRON = "CartoDB positron"
    CARTODB_DARK = "CartoDB dark_matter"
    STAMEN_TERRAIN = "Stamen Terrain"
    STAMEN_TONER = "Stamen Toner"


class MapGenerator(MapasTematicosMixin):
    """
    Generador de mapas interactivos con Folium y Plotly.

    Soporta:
    - Mapas de puntos con marcadores
    - Mapas coropléticos (áreas coloreadas por valor)
    - Mapas de calor (heatmaps)
    - Mapas de clusters
    - Capas múltiples
    """

    # Configuración por defecto para Colombia
    DEFAULT_CENTER = [4.6097, -74.0817]  # Bogotá
    DEFAULT_ZOOM = 6

    # Colores por defecto para categorías
    CATEGORY_COLORS = [
        "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
        "#ff7f00", "#ffff33", "#a65628", "#f781bf"
    ]

    def __init__(
        self,
        default_style: MapStyle = MapStyle.CARTODB_POSITRON,
        default_color_scheme: ColorScheme = ColorScheme.SEQUENTIAL_BLUE
    ):
        """
        Inicializar generador de mapas.

        Args:
            default_style: Estilo de mapa base por defecto
            default_color_scheme: Esquema de colores por defecto
        """
        self.default_style = default_style
        self.default_color_scheme = default_color_scheme


    def render_to_html(self, map_config: dict[str, Any]) -> str:
        """
        Renderizar configuración de mapa a HTML.

        Args:
            map_config: Configuración del mapa

        Returns:
            HTML del mapa interactivo
        """
        map_type = map_config.get("type", "point_map")
        # C3d-4: escapar el título — puede venir del query/LLM.
        title = html_escape(str(map_config.get("title", "Mapa")))
        center = map_config.get("center", self.DEFAULT_CENTER)
        zoom = map_config.get("zoom", self.DEFAULT_ZOOM)
        style = map_config.get("style", self.default_style.value)

        # Template HTML básico con Leaflet
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
        .map-title {{
            position: absolute;
            top: 10px;
            left: 50px;
            z-index: 1000;
            background: white;
            padding: 10px 20px;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            font-family: Arial, sans-serif;
            font-size: 16px;
            font-weight: bold;
        }}
        .stats-panel {{
            position: absolute;
            bottom: 20px;
            left: 10px;
            z-index: 1000;
            background: white;
            padding: 10px;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            font-family: Arial, sans-serif;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="map-title">{title}</div>
    <div id="map"></div>
    <div class="stats-panel">
        <strong>Estadísticas:</strong><br>
        Elementos: {map_config.get('stats', {}).get('feature_count', 'N/A')}<br>
        Tipo: {map_type}
    </div>
    <script>
        var map = L.map('map').setView([{center[0]}, {center[1]}], {zoom});

        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
            subdomains: 'abcd',
            maxZoom: 20
        }}).addTo(map);

        var geojsonData = {_safe_json_for_script(map_config.get('data', {}))};

        if (geojsonData.features) {{
            var geojsonLayer = L.geoJSON(geojsonData, {{
                style: function(feature) {{
                    return {{
                        color: '#3388ff',
                        weight: 2,
                        fillOpacity: 0.5
                    }};
                }},
                pointToLayer: function(feature, latlng) {{
                    return L.circleMarker(latlng, {{
                        radius: 8,
                        fillColor: '#3388ff',
                        color: '#fff',
                        weight: 2,
                        fillOpacity: 0.8
                    }});
                }},
                onEachFeature: function(feature, layer) {{
                    if (feature.properties) {{
                        // C3d-4: construir el popup con DOM/textContent en vez
                        // de concatenar HTML — neutraliza XSS si una key o un
                        // valor de properties contiene markup (<script>, etc.).
                        var container = document.createElement('div');
                        for (var key in feature.properties) {{
                            var row = document.createElement('div');
                            var label = document.createElement('strong');
                            label.textContent = key + ': ';
                            row.appendChild(label);
                            row.appendChild(
                                document.createTextNode(String(feature.properties[key]))
                            );
                            container.appendChild(row);
                        }}
                        layer.bindPopup(container);
                    }}
                }}
            }}).addTo(map);

            map.fitBounds(geojsonLayer.getBounds());
        }}
    </script>
</body>
</html>
"""
        return html

    def _calculate_center(self, features: list[dict]) -> list[float]:
        """Calcular centro geográfico de las features."""
        if not features:
            return self.DEFAULT_CENTER

        lats = []
        lons = []

        for f in features:
            geom = f.get("geometry", {})
            coords = self._extract_coords(geom)
            for lon, lat in coords:
                lats.append(lat)
                lons.append(lon)

        if not lats or not lons:
            return self.DEFAULT_CENTER

        return [sum(lats) / len(lats), sum(lons) / len(lons)]

    def _extract_coords(self, geom: dict) -> list[tuple[float, float]]:
        """Extraer coordenadas de una geometría."""
        geom_type = geom.get("type", "")
        coords = geom.get("coordinates", [])

        if geom_type == "Point":
            return [(coords[0], coords[1])] if len(coords) >= 2 else []
        elif geom_type == "LineString":
            return [(c[0], c[1]) for c in coords if len(c) >= 2]
        elif geom_type == "Polygon":
            return [(c[0], c[1]) for ring in coords for c in ring if len(c) >= 2]
        elif geom_type == "MultiPoint":
            return [(c[0], c[1]) for c in coords if len(c) >= 2]
        elif geom_type == "MultiLineString":
            return [(c[0], c[1]) for line in coords for c in line if len(c) >= 2]
        elif geom_type == "MultiPolygon":
            return [(c[0], c[1]) for poly in coords for ring in poly for c in ring if len(c) >= 2]

        return []

    def _calculate_zoom(self, features: list[dict]) -> int:
        """Calcular nivel de zoom apropiado."""
        if not features:
            return self.DEFAULT_ZOOM

        lats = []
        lons = []

        for f in features:
            geom = f.get("geometry", {})
            coords = self._extract_coords(geom)
            for lon, lat in coords:
                lats.append(lat)
                lons.append(lon)

        if not lats or not lons:
            return self.DEFAULT_ZOOM

        lat_range = max(lats) - min(lats)
        lon_range = max(lons) - min(lons)
        max_range = max(lat_range, lon_range)

        # Calcular zoom basado en el rango
        if max_range > 10:
            return 5
        elif max_range > 5:
            return 6
        elif max_range > 2:
            return 8
        elif max_range > 1:
            return 9
        elif max_range > 0.5:
            return 10
        elif max_range > 0.1:
            return 12
        elif max_range > 0.01:
            return 14
        else:
            return 16

    def _empty_map_config(self, title: str | None = None) -> dict[str, Any]:
        """Configuración para mapa vacío."""
        return {
            "type": "empty",
            "title": title or "Mapa vacío",
            "center": self.DEFAULT_CENTER,
            "zoom": self.DEFAULT_ZOOM,
            "style": self.default_style.value,
            "data": {"type": "FeatureCollection", "features": []},
            "stats": {"feature_count": 0},
            "message": "No hay datos para mostrar"
        }
