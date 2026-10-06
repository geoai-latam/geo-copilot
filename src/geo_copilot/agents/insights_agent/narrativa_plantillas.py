"""La narrativa por PLANTILLAS: las plantillas, sus variables y los formatos de cifras,
tendencias, diferencias, brechas, conclusiones e insights adicionales.

Salió de `NarrativeGenerator` (F4 del plan de calidad: narrative_generator.py tenía 614 líneas), tal cual.
"""

from enum import Enum
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.insights_agent.narrative_generator")


class NarrativeStyle(str, Enum):
    """Estilos de narrativa disponibles."""
    TECHNICAL = "technical"      # Detallado, técnico
    EXECUTIVE = "executive"      # Resumen ejecutivo
    CASUAL = "casual"            # Conversacional
    BULLET_POINTS = "bullets"    # Puntos clave


class NarrativaPlantillasMixin:
    """La narrativa por PLANTILLAS: las plantillas, sus variables y los formatos de cifras,"""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        def _apply_style(self, narrative: str, style: NarrativeStyle) -> str: ...

    # Plantillas de narrativa predefinidas
    TEMPLATES = {
        "proximity_analysis": """
## Análisis de Proximidad: {title}

Se identificaron **{total_count:,}** {source_entity} dentro de un radio de **{distance}** metros de {target_entity}.

### Resumen Estadístico
- Total de elementos encontrados: {total_count:,}
- Distancia mínima: {min_distance:.2f} m
- Distancia máxima: {max_distance:.2f} m
- Distancia promedio: {avg_distance:.2f} m

{additional_insights}
""",
        "aggregation_analysis": """
## Agregación Territorial: {title}

Se analizaron **{total_count:,}** {data_entity} agregados por {admin_entity}.

### Distribución
- Total de áreas administrativas: {group_count}
- Promedio por área: {avg_per_group:.2f}
- Máximo: {max_value:,} ({max_name})
- Mínimo: {min_value:,} ({min_name})

### Top 5 {admin_entity}
{top_5_list}

{additional_insights}
""",
        "coverage_analysis": """
## Análisis de Cobertura: {title}

Se analizó la cobertura de **{service_entity}** sobre **{area_entity}**.

### Resultados
- Cobertura total: **{coverage_percent:.1f}%**
- Áreas con cobertura: {covered_count} de {total_areas}
- Área total cubierta: {covered_area:,.2f} km²

### Brechas Identificadas
{gap_analysis}

{additional_insights}
""",
        "comparison_analysis": """
## Análisis Comparativo: {title}

Se compararon {dataset_count} conjuntos de datos.

### Diferencias Clave
{key_differences}

### Métricas Comparativas
{comparison_metrics}

{additional_insights}
""",
        "temporal_analysis": """
## Análisis Temporal: {title}

Se analizó la evolución de {metric} entre {start_period} y {end_period}.

### Cambio General
- Valor inicial: {start_value:,}
- Valor final: {end_value:,}
- Cambio absoluto: {absolute_change:+,}
- Cambio porcentual: {percent_change:+.1f}%

### Tendencia
{trend_description}

{additional_insights}
""",
        "generic_summary": """
## {title}

{description}

### Datos Analizados
- Total de registros: {record_count:,}
{metrics_summary}

### Conclusiones
{conclusions}
"""
    }

    def _generate_from_template(
        self,
        analysis_type: str,
        data: dict[str, Any],
        context: str | None,
        style: NarrativeStyle
    ) -> str:
        """Generar narrativa usando plantillas."""
        template_key = f"{analysis_type}_analysis"
        template = self.TEMPLATES.get(template_key, self.TEMPLATES["generic_summary"])

        # Preparar variables para la plantilla
        template_vars = self._prepare_template_vars(analysis_type, data, context)

        try:
            narrative = template.format(**template_vars)
        except KeyError as e:
            logger.warning(f"Missing template variable: {e}")
            narrative = self._generate_generic_narrative(data, context)

        # Ajustar según estilo
        narrative = self._apply_style(narrative, style)

        return narrative

    def _prepare_template_vars(
        self,
        analysis_type: str,
        data: dict[str, Any],
        context: str | None
    ) -> dict[str, Any]:
        """Preparar variables para plantillas."""
        stats = data.get("stats", {})
        config = data.get("config", {})

        # Variables base
        vars_dict = {
            "title": data.get("title", "Análisis"),
            "description": context or data.get("description", ""),
            "record_count": stats.get("feature_count", stats.get("record_count", 0)),
            "additional_insights": self._generate_additional_insights(data),
            "conclusions": self._generate_conclusions(data),
            "metrics_summary": self._format_metrics_summary(stats),
        }

        # Variables específicas por tipo de análisis
        if analysis_type == "proximity":
            vars_dict.update({
                "source_entity": config.get("source_entity", "elementos"),
                "target_entity": config.get("target_entity", "objetivo"),
                "distance": config.get("distance", 0),
                "total_count": stats.get("feature_count", 0),
                "min_distance": stats.get("min_distance", 0),
                "max_distance": stats.get("max_distance", 0),
                "avg_distance": stats.get("avg_distance", 0),
            })

        elif analysis_type == "aggregation":
            vars_dict.update({
                "data_entity": config.get("data_entity", "datos"),
                "admin_entity": config.get("admin_entity", "áreas"),
                "group_count": stats.get("group_count", 0),
                "avg_per_group": stats.get("avg_per_group", 0),
                "max_value": stats.get("max_value", 0),
                "max_name": stats.get("max_name", "N/A"),
                "min_value": stats.get("min_value", 0),
                "min_name": stats.get("min_name", "N/A"),
                "top_5_list": self._format_top_list(data.get("top_items", [])),
            })

        elif analysis_type == "coverage":
            vars_dict.update({
                "service_entity": config.get("service_entity", "servicios"),
                "area_entity": config.get("area_entity", "áreas"),
                "coverage_percent": stats.get("coverage_percent", 0),
                "covered_count": stats.get("covered_count", 0),
                "total_areas": stats.get("total_areas", 0),
                "covered_area": stats.get("covered_area", 0),
                "gap_analysis": self._generate_gap_analysis(data),
            })

        elif analysis_type == "temporal":
            vars_dict.update({
                "metric": config.get("metric", "valor"),
                "start_period": config.get("start_period", "inicio"),
                "end_period": config.get("end_period", "fin"),
                "start_value": stats.get("start_value", 0),
                "end_value": stats.get("end_value", 0),
                "absolute_change": stats.get("absolute_change", 0),
                "percent_change": stats.get("percent_change", 0),
                "trend_description": self._describe_trend(stats),
            })

        elif analysis_type == "comparison":
            vars_dict.update({
                "dataset_count": len(data.get("datasets", [])),
                "key_differences": self._format_key_differences(data),
                "comparison_metrics": self._format_comparison_metrics(data),
            })

        return vars_dict

    def _generate_generic_narrative(
        self,
        data: dict[str, Any],
        context: str | None
    ) -> str:
        """Generar narrativa genérica cuando no hay plantilla específica."""
        stats = data.get("stats", {})

        narrative = "## Resumen del Análisis\n\n"

        if context:
            narrative += f"{context}\n\n"

        narrative += "### Estadísticas\n\n"

        for key, value in stats.items():
            if not key.startswith("_"):
                label = key.replace("_", " ").title()
                if isinstance(value, float):
                    narrative += f"- **{label}**: {value:,.2f}\n"
                elif isinstance(value, int):
                    narrative += f"- **{label}**: {value:,}\n"
                else:
                    narrative += f"- **{label}**: {value}\n"

        return narrative

    def _format_statistics(self, data: dict[str, Any]) -> str:
        """Formatear estadísticas para prompt de LLM."""
        stats = data.get("stats", {})
        lines = []

        for key, value in stats.items():
            if not key.startswith("_"):
                label = key.replace("_", " ").title()
                lines.append(f"- {label}: {value}")

        return "\n".join(lines) if lines else "No hay estadísticas disponibles"

    def _format_top_list(self, items: list[dict]) -> str:
        """Formatear lista de top items."""
        if not items:
            return "No hay datos disponibles"

        lines = []
        for i, item in enumerate(items[:5], 1):
            name = item.get("name", "N/A")
            value = item.get("value", 0)
            if isinstance(value, float):
                lines.append(f"{i}. {name}: {value:,.2f}")
            else:
                lines.append(f"{i}. {name}: {value:,}")

        return "\n".join(lines)

    def _format_metrics_summary(self, stats: dict[str, Any]) -> str:
        """Formatear resumen de métricas."""
        lines = []
        for key, value in stats.items():
            if key not in ["feature_count", "record_count"] and not key.startswith("_"):
                label = key.replace("_", " ").title()
                if isinstance(value, float):
                    lines.append(f"- {label}: {value:,.2f}")
                elif isinstance(value, int):
                    lines.append(f"- {label}: {value:,}")

        return "\n".join(lines) if lines else "Sin métricas adicionales"

    def _generate_additional_insights(self, data: dict[str, Any]) -> str:
        """Datos adicionales — SOLO HECHOS (R2.4).

        Antes este método FABRICABA juicios por umbral mágico ('Concentración
        detectada' si ratio>3). El template solo reporta números; interpretar
        es del LLM.
        """
        stats = data.get("stats", {})
        insights = []

        if "max_value" in stats and "avg_per_group" in stats:
            ratio = stats["max_value"] / stats["avg_per_group"] if stats["avg_per_group"] > 0 else 0
            if ratio > 0:
                insights.append(
                    f"El valor máximo es {ratio:.1f}x el promedio por grupo."
                )

        if "min_value" in stats and "max_value" in stats:
            range_val = stats["max_value"] - stats["min_value"]
            if range_val > 0:
                insights.append(f"Rango entre mínimo y máximo: {range_val:,} unidades.")

        return "\n\n".join(insights) if insights else ""

    def _generate_conclusions(self, data: dict[str, Any]) -> str:
        """Cierre factual — SOLO HECHOS (R2.4).

        Antes FABRICABA recomendaciones de negocio por umbral mágico
        ('coverage<50 ⇒ se recomienda expandir la infraestructura'). El
        template reporta lo que se procesó; recomendar es del LLM (y con
        evidencia).
        """
        stats = data.get("stats", {})
        parts = []

        feature_count = stats.get("feature_count", stats.get("record_count", 0))
        if feature_count > 0:
            parts.append(f"Se procesaron {feature_count:,} registros.")

        if "coverage_percent" in stats:
            parts.append(f"Cobertura calculada: {stats['coverage_percent']}%.")

        return " ".join(parts) if parts else "Resumen de datos generado."

    def _generate_gap_analysis(self, data: dict[str, Any]) -> str:
        """Generar análisis de brechas para cobertura."""
        gaps = data.get("gaps", [])
        if not gaps:
            return "No se identificaron brechas significativas."

        lines = []
        for gap in gaps[:5]:
            name = gap.get("name", "Área sin nombre")
            population = gap.get("population", 0)
            lines.append(f"- **{name}**: {population:,} habitantes sin cobertura")

        return "\n".join(lines)

    def _describe_trend(self, stats: dict[str, Any]) -> str:
        """Cambio temporal — SOLO EL HECHO (R2.4).

        Antes interpretaba por umbral mágico ±10% ('significativa' / 'requiere
        atención'). El template reporta el número; calificar la tendencia es
        del LLM.
        """
        percent_change = stats.get("percent_change", 0)
        if percent_change == 0:
            return "Sin cambio en el período analizado (0%)."
        return f"Cambio en el período analizado: {percent_change:+.1f}%."

    def _format_key_differences(self, data: dict[str, Any]) -> str:
        """Formatear diferencias clave entre datasets."""
        differences = data.get("differences", [])
        if not differences:
            return "No se identificaron diferencias significativas."

        lines = []
        for diff in differences[:5]:
            lines.append(f"- {diff}")

        return "\n".join(lines)

    def _format_comparison_metrics(self, data: dict[str, Any]) -> str:
        """Formatear métricas comparativas."""
        metrics = data.get("comparison_metrics", {})
        if not metrics:
            return "Sin métricas comparativas disponibles."

        lines = []
        for metric, values in metrics.items():
            if isinstance(values, dict):
                line = f"- **{metric}**: "
                parts = [f"{k}={v}" for k, v in values.items()]
                line += ", ".join(parts)
                lines.append(line)

        return "\n".join(lines) if lines else "Sin métricas"
