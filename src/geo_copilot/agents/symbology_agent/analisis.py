"""El ANÁLISIS de la capa: tipo de geometría y, por campo, su tipo de dato y sus estadísticas
(los hechos que el LLM ve para diseñar).

Salió de `SymbologyAgent` (F4 del plan de calidad: agent.py tenía 1.622 líneas), tal cual.
"""

import statistics
from collections import Counter
from typing import TYPE_CHECKING, Any

from geo_copilot.agents.symbology_agent.styles import (
    DataType,
)
from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.symbology_agent.agent")


def _numerico(result: dict, numeric_values: list) -> dict:
    """Campo numérico homogéneo: estadísticas; discreto si son pocos enteros."""
    result["data_type"] = DataType.NUMERIC_CONTINUOUS.value
    result["statistics"] = {
        "min": min(numeric_values),
        "max": max(numeric_values),
        "mean": statistics.mean(numeric_values),
        "median": statistics.median(numeric_values),
        "std": statistics.stdev(numeric_values) if len(numeric_values) > 1 else 0,
    }
    unique = len(set(numeric_values))
    if unique < 20 and all(v == int(v) for v in numeric_values):
        result["data_type"] = DataType.NUMERIC_DISCRETE.value
        result["unique_values"] = unique
    return result


def _texto(result: dict, string_values: list) -> dict:
    """Campo de texto homogéneo: categorías (las 25 más comunes) o identificador."""
    unique_values = set(string_values)
    result["unique_values"] = len(unique_values)
    # R4.5: 25 categorías (antes 10 — las 11+ se perdían al color base
    # sin leyenda ni aviso). El resto se agrupa como "Otros" en
    # _calculate_categorical_breaks usando `count` total.
    result["value_counts"] = dict(Counter(string_values).most_common(25))
    # Identificador: cada valor único (no se puede agrupar por él).
    if len(unique_values) == len(string_values):
        result["data_type"] = DataType.IDENTIFIER.value
    else:
        result["data_type"] = DataType.CATEGORICAL.value
    return result


class AnalisisMixin:
    """Análisis de la capa: geometría y tipo/estadísticas de cada campo."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        async def _llm_design_symbology(
            self,
            query: str,
            field_analysis: dict,
            primary_geom: str,
            sample_props: list[dict],
            feature_count: int | None = None,
            estilo_actual: dict | None = None,
        ) -> dict[str, Any]: ...

        async def _validate_design_via_a2a(
            self,
            design: dict[str, Any],
            *,
            feature_count: int,
            geometry_type: str,
            field_analysis: dict[str, dict],
            extent_km2: float | None = None,
        ) -> dict[str, Any]: ...

    async def analyze_data(
        self,
        geojson: dict,
        query: str = "",
        estilo_actual: dict | None = None,
    ) -> dict[str, Any]:
        """Analizar GeoJSON + diseñar simbología via LLM.

        `estilo_actual` (FH.6): el estilo que la capa TIENE ahora en el mapa y lo que el
        usuario fijó a mano — un hecho para el diseño (qué conservar lo decide el LLM).

        Args:
            geojson: FeatureCollection con los datos
            query: Query del usuario (opcional) — el LLM la usa para inferir
                intent visual ("color por uso", "mapa de calor", etc.).

        Returns:
            Diccionario con análisis + plan de simbología completo del LLM.
        """
        features = geojson.get("features", [])
        if not features:
            return {"empty": True, "feature_count": 0}

        geometry_types = set()
        for f in features:
            if f.get("geometry"):
                geometry_types.add(f["geometry"].get("type"))
        primary_geom = self._get_primary_geometry_type(geometry_types)

        all_props = [f.get("properties", {}) for f in features if f.get("properties")]
        field_analysis = self._analyze_fields(all_props)

        # El LLM diseña la simbología COMPLETA: tipo (single/unique/graduated_colors/
        # graduated_symbols/heatmap/cluster), método de clasificación (natural_breaks,
        # quantile, equal_interval, std_deviation), paleta de color, num clases,
        # label_field y classification_field. Antes había heurísticas por keywords
        # ("nombre", "name") y umbrales (std>0, unique<=10) que fallaban con schemas
        # no convencionales y NUNCA elegían cluster/heatmap/graduated_symbols.
        design = await self._llm_design_symbology(
            query=query,
            field_analysis=field_analysis,
            primary_geom=primary_geom,
            sample_props=all_props[:3],
            feature_count=len(features),  # R4.9: conteo real, no promedio
            estilo_actual=estilo_actual,
        )
        design = await self._juzgar_encaje(design, features, primary_geom, field_analysis)

        return {
            "empty": False,
            "feature_count": len(features),
            "geometry_type": primary_geom,
            "geometry_types": list(geometry_types),
            "fields": field_analysis,
            "design": design,
            # Compat con código existente que aún usa estos nombres.
            "recommended_label_field": design.get("label_field"),
            "recommended_classification_field": design.get("classification_field"),
        }

    async def _juzgar_encaje(self, design: dict, features: list, primary_geom: str,
                             field_analysis: dict) -> dict:
        """El juez A2A del encaje de la visualización, salvo instrucción literal del usuario."""
        # A2A (2026-05-31): el LLM puede elegir heatmap/cluster en datos
        # que no encajan (heatmap con 5 puntos → puntos sueltos disfrazados;
        # cluster con polígonos → imposible). Consultamos al InsightsAgent
        # para validar el fit antes de aplicar. Si no encaja, degradamos a
        # la alternativa sugerida.
        # EXCEPCIÓN (bug #12 audit 2026-06-15, re-resuelto en A3): si el
        # DISEÑADOR marcó `explicit_user_request` — el usuario NOMBRÓ
        # textualmente el tipo — la instrucción literal es un HECHO y el juez
        # de fit no la degrada (mismo estatus que manual_class_breaks). Quién
        # detecta la instrucción literal (incluidas negaciones) es el LLM del
        # diseño, no un substring — R5.2 sigue intacto.
        if design.get("explicit_user_request"):
            logger.info(
                "[SymbologyAgent] instrucción literal del usuario "
                f"({design.get('symbology_type')}) — el juez A2A no la degrada"
            )
            return design
        from geo_copilot.core.spatial import point_extent_km2
        return await self._validate_design_via_a2a(
            design,
            feature_count=len(features),
            geometry_type=primary_geom,
            field_analysis=field_analysis,
            extent_km2=point_extent_km2(features),
        )

    def _get_primary_geometry_type(self, geom_types: set) -> str:
        """Determinar tipo de geometría principal."""
        priority = ["MultiPolygon", "Polygon", "MultiLineString", "LineString", "MultiPoint", "Point"]
        for geom in priority:
            if geom in geom_types:
                return geom
        return list(geom_types)[0] if geom_types else "Point"

    def _analyze_fields(self, properties: list[dict]) -> dict[str, dict]:
        """Analizar campos de las propiedades."""
        if not properties:
            return {}

        # Obtener todos los campos
        all_fields: set[str] = set()
        for p in properties:
            all_fields.update(p.keys())

        field_analysis = {}
        for field in all_fields:
            values = [p.get(field) for p in properties if p.get(field) is not None]
            if not values:
                continue

            field_info = self._analyze_field_values(field, values)
            field_analysis[field] = field_info

        return field_analysis

    def _analyze_field_values(self, field_name: str, values: list) -> dict:
        """Analizar valores de un campo: stats si numérico, value_counts si
        categórico. SOLO clasifica como NUMERIC/CATEGORICAL si el campo es
        homogéneo (100% del mismo tipo). Antes había `>0.8` threshold mágico
        que clasificaba campos mixed como numéricos engañosamente.
        """
        numeric_values = [
            v for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        string_values = [v for v in values if isinstance(v, str)]
        bool_values = [v for v in values if isinstance(v, bool)]
        total = len(values)

        result: dict[str, Any] = {
            "name": field_name,
            "count": total,
            "null_count": len([v for v in values if v is None]),
        }

        # Booleano puro (cheked first — bool is subclass of int en Python).
        if total > 0 and len(bool_values) == total:
            result["data_type"] = DataType.BOOLEAN.value
            result["true_count"] = sum(bool_values)
            result["false_count"] = total - result["true_count"]
            return result

        # Numérico homogéneo (100% numérico, sin bools).
        if total > 0 and len(numeric_values) == total:
            return _numerico(result, numeric_values)

        # String homogéneo.
        if total > 0 and len(string_values) == total:
            return _texto(result, string_values)

        # Mixto o desconocido → TEXT. El LLM downstream decide si vale usarlo.
        result["data_type"] = DataType.TEXT.value
        result["mixed_types"] = {
            "numeric": len(numeric_values),
            "string": len(string_values),
            "bool": len(bool_values),
        }
        return result
