"""Los MAPAS por tipo: puntos, coropletas, calor, clústeres y multicapa.

Salió de `MapGenerator` (F4 del plan de calidad: map_generator.py tenía 638 líneas), tal cual.
"""

import json
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.agents.insights_agent.map_generator import MapStyle

logger = get_logger("geo_copilot.agents.insights_agent.map_generator")


class ColorScheme(str, Enum):
    """Esquemas de color para mapas coropléticos."""
    SEQUENTIAL_BLUE = "Blues"
    SEQUENTIAL_GREEN = "Greens"
    SEQUENTIAL_RED = "Reds"
    SEQUENTIAL_ORANGE = "OrRd"
    DIVERGING = "RdYlGn"
    CATEGORICAL = "Set1"


class MapasTematicosMixin:
    """Los MAPAS por tipo: puntos, coropletas, calor, clústeres y multicapa."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        DEFAULT_CENTER: list[float]
        DEFAULT_ZOOM: int
        CATEGORY_COLORS: list[str]
        default_style: MapStyle
        default_color_scheme: ColorScheme
        def _calculate_center(self, features: list[dict]) -> list[float]: ...
        def _calculate_zoom(self, features: list[dict]) -> int: ...
        def _empty_map_config(self, title: str | None = None) -> dict[str, Any]: ...

    def create_point_map(
        self,
        geojson_data: dict | str,
        popup_fields: list[str] | None = None,
        color_field: str | None = None,
        radius: int = 8,
        center: list[float] | None = None,
        zoom: int | None = None,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Crear mapa de puntos con marcadores.

        Args:
            geojson_data: Datos GeoJSON (dict o string)
            popup_fields: Campos a mostrar en popup
            color_field: Campo para colorear puntos
            radius: Radio de los marcadores
            center: Centro del mapa [lat, lon]
            zoom: Nivel de zoom
            title: Título del mapa

        Returns:
            Configuración del mapa para renderizar
        """
        if isinstance(geojson_data, str):
            geojson_data = cast(dict, json.loads(geojson_data))

        features = geojson_data.get("features", [])
        if not features:
            return self._empty_map_config(title)

        # Calcular centro automático si no se especifica
        if center is None:
            center = self._calculate_center(features)

        if zoom is None:
            zoom = self._calculate_zoom(features)

        # Construir configuración de mapa
        config = {
            "type": "point_map",
            "title": title or "Mapa de Puntos",
            "center": center,
            "zoom": zoom,
            "style": self.default_style.value,
            "data": geojson_data,
            "options": {
                "popup_fields": popup_fields or [],
                "color_field": color_field,
                "radius": radius,
                "colors": self.CATEGORY_COLORS if color_field else ["#3388ff"],
            },
            "stats": {
                "feature_count": len(features),
                "geometry_type": "Point"
            }
        }

        logger.info(f"Created point map with {len(features)} features")
        return config

    def create_choropleth_map(
        self,
        geojson_data: dict | str,
        value_field: str,
        popup_fields: list[str] | None = None,
        color_scheme: ColorScheme | None = None,
        center: list[float] | None = None,
        zoom: int | None = None,
        title: str | None = None,
        legend_title: str | None = None
    ) -> dict[str, Any]:
        """
        Crear mapa coroplético (áreas coloreadas por valor).

        Args:
            geojson_data: Datos GeoJSON
            value_field: Campo numérico para colorear
            popup_fields: Campos a mostrar en popup
            color_scheme: Esquema de colores
            center: Centro del mapa
            zoom: Nivel de zoom
            title: Título del mapa
            legend_title: Título de la leyenda

        Returns:
            Configuración del mapa
        """
        if isinstance(geojson_data, str):
            geojson_data = cast(dict, json.loads(geojson_data))

        features = geojson_data.get("features", [])
        if not features:
            return self._empty_map_config(title)

        # Extraer valores para estadísticas
        values = []
        for f in features:
            val = f.get("properties", {}).get(value_field)
            if val is not None:
                try:
                    values.append(float(val))
                except (TypeError, ValueError):
                    pass

        if center is None:
            center = self._calculate_center(features)

        if zoom is None:
            zoom = self._calculate_zoom(features)

        config = {
            "type": "choropleth_map",
            "title": title or f"Mapa Coroplético: {value_field}",
            "center": center,
            "zoom": zoom,
            "style": self.default_style.value,
            "data": geojson_data,
            "options": {
                "value_field": value_field,
                "popup_fields": popup_fields or [value_field],
                "color_scheme": (color_scheme or self.default_color_scheme).value,
                "legend_title": legend_title or value_field,
            },
            "stats": {
                "feature_count": len(features),
                "value_min": min(values) if values else None,
                "value_max": max(values) if values else None,
                "value_mean": sum(values) / len(values) if values else None,
            }
        }

        logger.info(f"Created choropleth map with {len(features)} features")
        return config

    def create_heatmap(
        self,
        geojson_data: dict | str,
        weight_field: str | None = None,
        radius: int = 25,
        blur: int = 15,
        center: list[float] | None = None,
        zoom: int | None = None,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Crear mapa de calor (heatmap).

        Args:
            geojson_data: Datos GeoJSON (puntos)
            weight_field: Campo para ponderar intensidad
            radius: Radio del heatmap
            blur: Nivel de difuminado
            center: Centro del mapa
            zoom: Nivel de zoom
            title: Título del mapa

        Returns:
            Configuración del mapa
        """
        if isinstance(geojson_data, str):
            geojson_data = cast(dict, json.loads(geojson_data))

        features = geojson_data.get("features", [])
        if not features:
            return self._empty_map_config(title)

        # Extraer puntos para heatmap
        heat_points = []
        for f in features:
            geom = f.get("geometry", {})
            if geom.get("type") == "Point":
                coords = geom.get("coordinates", [])
                if len(coords) >= 2:
                    weight = 1.0
                    if weight_field:
                        val = f.get("properties", {}).get(weight_field)
                        if val is not None:
                            try:
                                weight = float(val)
                            except (TypeError, ValueError):
                                pass
                    heat_points.append([coords[1], coords[0], weight])

        if center is None:
            center = self._calculate_center(features)

        if zoom is None:
            zoom = self._calculate_zoom(features)

        config = {
            "type": "heatmap",
            "title": title or "Mapa de Calor",
            "center": center,
            "zoom": zoom,
            "style": self.default_style.value,
            "data": {
                "points": heat_points,
                "geojson": geojson_data
            },
            "options": {
                "radius": radius,
                "blur": blur,
                "weight_field": weight_field,
            },
            "stats": {
                "point_count": len(heat_points),
            }
        }

        logger.info(f"Created heatmap with {len(heat_points)} points")
        return config

    def create_cluster_map(
        self,
        geojson_data: dict | str,
        popup_fields: list[str] | None = None,
        center: list[float] | None = None,
        zoom: int | None = None,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Crear mapa con clustering de puntos.

        Args:
            geojson_data: Datos GeoJSON (puntos)
            popup_fields: Campos a mostrar en popup
            center: Centro del mapa
            zoom: Nivel de zoom
            title: Título del mapa

        Returns:
            Configuración del mapa
        """
        if isinstance(geojson_data, str):
            geojson_data = cast(dict, json.loads(geojson_data))

        features = geojson_data.get("features", [])
        if not features:
            return self._empty_map_config(title)

        if center is None:
            center = self._calculate_center(features)

        if zoom is None:
            zoom = self._calculate_zoom(features)

        config = {
            "type": "cluster_map",
            "title": title or "Mapa de Clusters",
            "center": center,
            "zoom": zoom,
            "style": self.default_style.value,
            "data": geojson_data,
            "options": {
                "popup_fields": popup_fields or [],
                "cluster_radius": 50,
                "disable_clustering_at_zoom": 15,
            },
            "stats": {
                "feature_count": len(features),
            }
        }

        logger.info(f"Created cluster map with {len(features)} features")
        return config

    def create_multi_layer_map(
        self,
        layers: list[dict[str, Any]],
        center: list[float] | None = None,
        zoom: int | None = None,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Crear mapa con múltiples capas.

        Args:
            layers: Lista de configuraciones de capa
                    [{"name": "Capa1", "geojson": {...}, "style": {...}}, ...]
            center: Centro del mapa
            zoom: Nivel de zoom
            title: Título del mapa

        Returns:
            Configuración del mapa
        """
        if not layers:
            return self._empty_map_config(title)

        # Calcular centro desde todas las capas
        if center is None:
            all_features = []
            for layer in layers:
                geojson = layer.get("geojson", {})
                if isinstance(geojson, str):
                    geojson = json.loads(geojson)
                all_features.extend(geojson.get("features", []))

            if all_features:
                center = self._calculate_center(all_features)
            else:
                center = self.DEFAULT_CENTER

        if zoom is None:
            zoom = self.DEFAULT_ZOOM

        config = {
            "type": "multi_layer_map",
            "title": title or "Mapa Multicapa",
            "center": center,
            "zoom": zoom,
            "style": self.default_style.value,
            "layers": layers,
            "options": {
                "layer_control": True,
            },
            "stats": {
                "layer_count": len(layers),
            }
        }

        logger.info(f"Created multi-layer map with {len(layers)} layers")
        return config
