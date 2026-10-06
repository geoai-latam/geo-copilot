"""Los PRODUCTOS del análisis: mapa, gráficos, tabla, narrativa, recomendaciones y título, y
los campos y registros que usan.

Salió de `InsightsAgent` (F4 del plan de calidad: agent.py tenía 1.610 líneas), tal cual.
"""

import json
from typing import TYPE_CHECKING, Any

from geo_copilot.agents.insights_agent.table_formatter import TableFormat
from geo_copilot.core.config import settings
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.agents.insights_agent.chart_generator import ChartGenerator
    from geo_copilot.agents.insights_agent.map_generator import MapGenerator
    from geo_copilot.agents.insights_agent.narrative_generator import NarrativeGenerator
    from geo_copilot.agents.insights_agent.table_formatter import TableFormatter
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.insights_agent.agent")


class ProductosMixin:
    """Los PRODUCTOS del análisis: mapa, gráficos, tabla, narrativa, recomendaciones y título, y"""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient | None
        map_generator: MapGenerator
        chart_generator: ChartGenerator
        table_formatter: TableFormatter
        narrative_generator: NarrativeGenerator

    async def _generate_appropriate_map(
        self,
        analysis_type: str,
        geojson_data: dict | str,
        stats: dict[str, Any],
        title: str,
        design: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generar el mapa según el plan del LLM (`design`)."""
        datos: dict = json.loads(geojson_data) if isinstance(geojson_data, str) else geojson_data

        features = datos.get("features", [])
        if not features:
            return self.map_generator._empty_map_config(title)

        design = design or {}
        map_type = design.get("map_type") or "point_map"
        value_field = design.get("map_value_field")
        color_field = design.get("map_color_field")
        popup_fields = design.get("popup_fields") or []
        first_geom_type = features[0].get("geometry", {}).get("type", "Point")

        if map_type == "choropleth" and value_field and first_geom_type in ("Polygon", "MultiPolygon"):
            return self.map_generator.create_choropleth_map(
                datos,
                value_field=value_field,
                popup_fields=popup_fields,
                title=title,
            )

        if map_type == "heatmap":
            return self.map_generator.create_heatmap(
                datos,
                weight_field=value_field,
                title=title,
            )

        if map_type == "cluster":
            return self.map_generator.create_cluster_map(
                datos,
                popup_fields=popup_fields,
                title=title,
            )

        # Default / point_map.
        return self.map_generator.create_point_map(
            datos,
            popup_fields=popup_fields,
            color_field=color_field,
            title=title,
        )

    async def _generate_appropriate_charts(
        self,
        analysis_type: str,
        analysis_result: dict[str, Any],
        stats: dict[str, Any],
        design: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generar gráficos según el plan del LLM (`design.charts`).

        Antes había un bloque if/elif por analysis_type que mapeaba fijo:
        proximity→histogram(distance_m), aggregation→bar+pie, etc. Ignoraba
        la query del usuario y elegía campos por keyword. Ahora el LLM
        decide qué charts crear y con qué columnas.
        """
        charts: list[dict[str, Any]] = []
        data = analysis_result.get("geojson") or analysis_result.get("data") or analysis_result
        design = design or {}
        plan = design.get("charts") or []

        for ch in plan:
            ct = ch.get("chart_type")
            try:
                self._un_grafico(charts, ct, ch, data)
            except Exception as exc:  # un chart roto (campos/datos del LLM) no debe tumbar el resto del informe
                logger.warning(
                    f"[InsightsAgent] error creando chart {ct}: {exc}", exc_info=True
                )

        self._resumen_estadistico(charts, stats)

        return charts

    def _generate_appropriate_table(
        self,
        analysis_type: str,
        analysis_result: dict[str, Any],
        stats: dict[str, Any],
        design: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generar tabla apropiada para el análisis.

        Sprint E: usa el `design` del LLM cuando esté disponible (el primer
        bar chart del plan tiene x_key/y_key adecuados). Si no, cae a
        helpers técnicos `_first_numeric_field` / `_first_string_field`.
        """
        data = analysis_result.get("geojson") or analysis_result.get("data") or analysis_result

        # Tomar pista del primer bar chart del design (si existe).
        design = design or {}
        first_bar = next(
            (c for c in (design.get("charts") or []) if c.get("chart_type") == "bar"),
            None,
        )
        group_field = (first_bar or {}).get("x_key") or self._first_string_field(data)
        value_field = (first_bar or {}).get("y_key") or self._first_numeric_field(data)

        if analysis_type == "aggregation":
            if group_field and value_field:
                return self.table_formatter.create_summary_table(
                    data,
                    group_by=group_field,
                    metrics=[
                        {"field": value_field, "agg": "sum", "label": f"Total {value_field}"},
                        {"field": value_field, "agg": "avg", "label": f"Promedio {value_field}"},
                    ],
                    title="Resumen por Área",
                )

        elif analysis_type in ["proximity", "coverage"]:
            ranking = self._tabla_ranking(analysis_type, design, data, value_field)
            if ranking is not None:
                return ranking

        # Tabla genérica
        return self.table_formatter.format_table(
            data,
            title="Datos del Análisis",
            format_type=TableFormat.HTML
        )

    def _un_grafico(self, charts: list[dict[str, Any]], ct: str, ch: dict[str, Any], data: Any) -> None:
        """Crea el gráfico de una entrada del plan del LLM (y lo añade a `charts`)."""
        if ct == "bar":
            charts.append(self.chart_generator.create_bar_chart(
                data,
                x_field=ch["x_key"],
                y_field=ch["y_key"],
                title=ch.get("title"),
                horizontal=bool(ch.get("horizontal", False)),
                sort_by=ch.get("sort_by"),
                limit=int(ch["limit"]) if ch.get("limit") else None,
            ))
        elif ct == "pie":
            charts.append(self.chart_generator.create_pie_chart(
                data,
                label_field=ch["x_key"],
                value_field=ch["y_key"],
                title=ch.get("title"),
                donut=bool(ch.get("donut", True)),
            ))
        elif ct == "line":
            y_fields = ch.get("y_fields") or [ch["y_key"]]
            charts.append(self.chart_generator.create_line_chart(
                data,
                x_field=ch["x_key"],
                y_fields=y_fields,
                title=ch.get("title"),
                fill=bool(ch.get("fill", False)),
            ))
        elif ct == "histogram":
            charts.append(self.chart_generator.create_histogram(
                data,
                value_field=ch["value_field"],
                title=ch.get("title"),
                bins=int(ch.get("bins", 20)),
            ))
        elif ct == "scatter":
            charts.append(self.chart_generator.create_scatter_chart(
                data,
                x_field=ch["x_key"],
                y_field=ch["y_key"],
                title=ch.get("title"),
                size_field=ch.get("size_field"),
                color_field=ch.get("color_field"),
                label_field=ch.get("label_field"),
            ))
        else:
            logger.info(f"[InsightsAgent] chart_type no soportado: {ct!r}")

    def _resumen_estadistico(self, charts: list[dict[str, Any]], stats: dict[str, Any]) -> None:
        """Añade un gráfico con las estadísticas generales, si las hay."""
        # Agregar estadísticas generales si hay datos
        if stats:
            summary_data = [
                {"metric": k.replace("_", " ").title(), "value": v}
                for k, v in stats.items()
                if isinstance(v, (int, float)) and not k.startswith("_")
            ]
            if summary_data:
                charts.append(
                    self.chart_generator.create_bar_chart(
                        summary_data,
                        x_field="metric",
                        y_field="value",
                        title="Resumen Estadístico",
                        horizontal=True
                    )
                )

    def _tabla_ranking(self, analysis_type: str, design: dict[str, Any], data: Any,
                       value_field: str | None) -> dict[str, Any] | None:
        """Ranking por distancia (proximidad) o cobertura, si los registros tienen ese campo."""
        # rank_field viene del plan visual (histogram para proximity,
        # bar para coverage) o del default convencional.
        rank_field = None
        if analysis_type == "proximity":
            hist = next(
                (c for c in (design.get("charts") or []) if c.get("chart_type") == "histogram"),
                None,
            )
            rank_field = (hist or {}).get("value_field") or "distance_m"
        else:  # coverage
            rank_field = value_field or "coverage_percent"

        display_fields = self._get_display_fields(data)
        records = self._extract_records(data)
        if records and rank_field in (records[0] if records else {}):
            return self.table_formatter.create_ranking_table(
                data,
                rank_field=rank_field,
                display_fields=display_fields[:3],
                title=f"Ranking por {rank_field}",
                ascending=(analysis_type == "proximity"),
                top_n=10,
            )
        return None

    async def _generate_narrative_for_analysis(
        self,
        analysis_type: str,
        analysis_result: dict[str, Any],
        stats: dict[str, Any],
        title: str,
        include_recommendations: bool
    ) -> dict[str, Any]:
        """Generar narrativa para el análisis."""
        # Preparar datos para el generador de narrativas
        narrative_data = {
            "title": title,
            "stats": stats,
            "config": analysis_result.get("config", {}),
            "top_items": self._extract_top_items(analysis_result),
            "gaps": analysis_result.get("gaps", []),
        }

        narrative_result = await self.narrative_generator.generate_narrative(
            analysis_type=analysis_type,
            data=narrative_data,
            context=analysis_result.get("context"),
            use_llm=(self.llm_client is not None)
        )

        if include_recommendations:
            recommendations = await self._generate_recommendations(
                analysis_type, stats
            )
            if recommendations:
                narrative_result["recommendations"] = recommendations
                narrative_result["narrative"] += (
                    f"\n\n### Recomendaciones\n\n{recommendations}"
                )

        return narrative_result

    async def _generate_recommendations(
        self,
        analysis_type: str,
        stats: dict[str, Any]
    ) -> str:
        """Generar recomendaciones contextuales con LLM.

        Antes había una serie de if/elif con umbrales inventados
        (count>1000, coverage<50, avg_distance>1000, max_value>avg*5) que
        producían frases genéricas tipo "considerar filtrar por área". Eran
        plantillas, no recomendaciones reales. Si no hay LLM, devolvemos
        cadena vacía — preferimos no decir nada a decir cosas inventadas.
        """
        if self.llm_client is None:
            return ""
        try:
            prompt = (
                f"Análisis tipo: {analysis_type}\n"
                f"Estadísticas: {stats}\n\n"
                "Genera 2-3 recomendaciones accionables en español basadas "
                "en estos datos. Sé concreto, evita frases genéricas. "
                "Una recomendación por línea con guión inicial."
            )
            from geo_copilot.core.llm_client import LLMMessage
            resp = await self.llm_client.chat(
                [LLMMessage(role="user", content=prompt)],
                temperature=0.3,
                max_tokens=200,
            )
            return (resp.content or "").strip()
        except Exception as exc:  # llamada al LLM opcional; sin recomendaciones el informe sigue
            logger.warning(f"[insights] recomendaciones LLM fallaron: {exc}", exc_info=True)
            return ""

    def _generate_title(self, analysis_type: str, result: dict) -> str:
        """Generar título descriptivo."""
        titles = {
            "proximity": "Análisis de Proximidad",
            "aggregation": "Agregación Territorial",
            "coverage": "Análisis de Cobertura",
            "intersection": "Intersección Espacial",
            "temporal": "Análisis Temporal",
            "hotspot": "Análisis de Concentración",
            "cluster": "Análisis de Clusters",
        }
        return result.get("title") or titles.get(analysis_type, "Análisis Geoespacial")

    def _first_numeric_field(self, data: dict | list) -> str | None:
        """Primer campo numérico (fallback técnico, no semántico)."""
        records: list[dict[str, Any]] = self._extract_records(data)
        if not records:
            return None
        for key, value in records[0].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not key.startswith("_"):
                return key
        return None

    def _first_string_field(self, data: dict | list) -> str | None:
        """Primer campo string (fallback técnico, no semántico)."""
        records: list[dict[str, Any]] = self._extract_records(data)
        if not records:
            return None
        for key, value in records[0].items():
            if isinstance(value, str) and not key.startswith("_") and key not in ("geometry", "geom"):
                return key
        return None

    def _get_popup_fields(self, features: list, max_fields: int = 5) -> list[str]:
        """Obtener campos para popup del mapa."""
        if not features:
            return []

        props = features[0].get("properties", {})
        fields = [k for k in props.keys() if not k.startswith("_") and k not in settings.geometry_field_names]
        return fields[:max_fields]

    def _get_display_fields(self, data: dict | list, max_fields: int = 5) -> list[str]:
        """Obtener campos para mostrar en tabla."""
        records = self._extract_records(data)
        if not records:
            return []

        fields = [k for k in records[0].keys() if not k.startswith("_") and k not in settings.geometry_field_names]
        return fields[:max_fields]

    def _extract_records(self, data: dict | list) -> list[dict]:
        """Extraer registros de diferentes formatos."""
        if isinstance(data, list):
            return data
        if "features" in data:
            return [f.get("properties", {}) for f in data.get("features", [])]
        return [data] if data else []

    def _extract_top_items(self, result: dict, limit: int = 5) -> list[dict]:
        """Extraer top items del resultado para la narrativa.

        Usa el design del LLM si está en `result["design"]`, si no cae a
        helpers técnicos `_first_numeric_field`/`_first_string_field`.
        """
        data = result.get("geojson") or result.get("data")
        if not data:
            return []

        records = self._extract_records(data)
        if not records:
            return []

        # Tomar pista del primer bar chart del design.
        design = result.get("design") or {}
        first_bar = next(
            (c for c in (design.get("charts") or []) if c.get("chart_type") == "bar"),
            None,
        )
        value_field = (first_bar or {}).get("y_key") or self._first_numeric_field(data)
        name_field = (first_bar or {}).get("x_key") or self._first_string_field(data)

        if not value_field:
            return []

        sorted_records = sorted(
            records,
            key=lambda x: x.get(value_field, 0) or 0,
            reverse=True,
        )[:limit]

        return [
            {"name": r.get(name_field, "N/A"), "value": r.get(value_field, 0)}
            for r in sorted_records
        ]
