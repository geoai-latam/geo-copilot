"""Los gráficos de CATEGORÍAS: barras, torta, líneas y treemap.

Salió de `ChartGenerator` (F4 del plan de calidad: chart_generator.py tenía 720 líneas), tal cual.
"""

from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.insights_agent.chart_generator")


class GraficosCategoricosMixin:
    """Los gráficos de CATEGORÍAS: barras, torta, líneas y treemap."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        def _extract_records(self, data: list | dict) -> list[dict]: ...

    def create_bar_chart(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        x_field: str,
        y_field: str,
        title: str | None = None,
        x_label: str | None = None,
        y_label: str | None = None,
        horizontal: bool = False,
        color_field: str | None = None,
        sort_by: str | None = None,
        limit: int | None = None
    ) -> dict[str, Any]:
        """
        Crear gráfico de barras.

        Args:
            data: Datos (lista de dicts o GeoJSON)
            x_field: Campo para eje X
            y_field: Campo para eje Y
            title: Título del gráfico
            x_label: Etiqueta eje X
            y_label: Etiqueta eje Y
            horizontal: Si es horizontal
            color_field: Campo para colorear barras
            sort_by: Campo para ordenar
            limit: Limitar número de barras

        Returns:
            Configuración del gráfico
        """
        records = self._extract_records(data)

        if sort_by:
            reverse = sort_by.startswith("-")
            field = sort_by.lstrip("-")
            records = sorted(
                records,
                key=lambda x: x.get(field, 0) or 0,
                reverse=reverse
            )

        if limit:
            records = records[:limit]

        # Construir records normalizados con solo los campos relevantes
        # (x_field, y_field, color_field opcional). Esto es lo que consume
        # el frontend Chart.tsx — `data: [{x_key: ..., y_key: ...}, ...]`.
        clean_records: list[dict[str, Any]] = []
        for r in records:
            entry: dict[str, Any] = {
                x_field: r.get(x_field, ""),
                y_field: r.get(y_field, 0),
            }
            if color_field:
                entry[color_field] = r.get(color_field)
            clean_records.append(entry)

        y_values = [r.get(y_field, 0) for r in records]
        numeric_y = all(isinstance(v, (int, float)) for v in y_values)

        config = {
            "chart_type": "bar",  # horizontal se indica con flag aparte
            "title": title or f"{y_field} por {x_field}",
            "x_key": x_field,
            "y_key": y_field,
            "data": clean_records,
            "horizontal": horizontal,
            "color_field": color_field,
            "x_label": x_label or x_field,
            "y_label": y_label or y_field,
            "stats": {
                "record_count": len(records),
                "total": sum(y_values) if numeric_y else None,
                "max": max(y_values) if y_values and numeric_y else None,
                "min": min(y_values) if y_values and numeric_y else None,
            },
        }

        logger.info(f"Created bar chart: {len(records)} records, x_key={x_field}, y_key={y_field}")
        return config

    def create_pie_chart(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        label_field: str,
        value_field: str,
        title: str | None = None,
        donut: bool = False,
        limit: int = 10
    ) -> dict[str, Any]:
        """
        Crear gráfico circular (pie/donut).

        Args:
            data: Datos
            label_field: Campo para etiquetas
            value_field: Campo para valores
            title: Título
            donut: Si es donut (con hueco)
            limit: Máximo de segmentos

        Returns:
            Configuración del gráfico
        """
        records = self._extract_records(data)

        # Ordenar por valor y limitar
        records = sorted(
            records,
            key=lambda x: x.get(value_field, 0) or 0,
            reverse=True
        )

        # Agrupar resto si hay más de limit
        if len(records) > limit:
            top_records = records[:limit - 1]
            others_value = sum(r.get(value_field, 0) or 0 for r in records[limit - 1:])
            top_records.append({label_field: "Otros", value_field: others_value})
            records = top_records

        # Records normalizados — el frontend Chart.tsx usa nameKey/dataKey
        # apuntando a x_key/y_key sobre cada record.
        clean_records: list[dict[str, Any]] = [
            {label_field: r.get(label_field, "N/A"), value_field: r.get(value_field, 0)}
            for r in records
        ]
        values = [r[value_field] for r in clean_records]
        labels = [r[label_field] for r in clean_records]
        total = sum(values)

        config = {
            "chart_type": "pie",
            "title": title or f"Distribución de {value_field}",
            "x_key": label_field,  # nameKey en Recharts
            "y_key": value_field,  # dataKey en Recharts
            "data": clean_records,
            "donut": donut,
            "stats": {
                "segment_count": len(records),
                "total": total,
                "percentages": [
                    {"label": l, "value": v, "percent": round(v / total * 100, 1) if total > 0 else 0}
                    for l, v in zip(labels, values, strict=False)
                ],
            },
        }

        logger.info(f"Created pie chart: {len(records)} segments, x_key={label_field}, y_key={value_field}")
        return config

    def create_line_chart(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        x_field: str,
        y_fields: list[str],
        title: str | None = None,
        x_label: str | None = None,
        y_label: str | None = None,
        fill: bool = False
    ) -> dict[str, Any]:
        """
        Crear gráfico de líneas.

        Args:
            data: Datos
            x_field: Campo para eje X
            y_fields: Campos para líneas
            title: Título
            x_label: Etiqueta eje X
            y_label: Etiqueta eje Y
            fill: Si rellenar área bajo línea

        Returns:
            Configuración del gráfico
        """
        records = self._extract_records(data)

        # Ordenar por campo X y construir records con todos los y_fields.
        records = sorted(records, key=lambda x: x.get(x_field, 0))
        clean_records: list[dict[str, Any]] = []
        for r in records:
            entry: dict[str, Any] = {x_field: r.get(x_field)}
            for y_field in y_fields:
                entry[y_field] = r.get(y_field, 0)
            clean_records.append(entry)

        # x_key/y_key apuntan al primer y_field (single series).
        # Para multi-series el frontend itera y_fields como series adicionales.
        primary_y = y_fields[0] if y_fields else None

        config = {
            "chart_type": "line",  # area es solo flag visual
            "title": title or f"Evolución de {', '.join(y_fields)}",
            "x_key": x_field,
            "y_key": primary_y,
            "y_fields": y_fields,  # para multi-series
            "data": clean_records,
            "fill": fill,
            "x_label": x_label or x_field,
            "y_label": y_label or ", ".join(y_fields),
            "stats": {
                "point_count": len(records),
                "series_count": len(y_fields),
            },
        }

        logger.info(f"Created line chart: {len(records)} points, x_key={x_field}, y_key={primary_y}")
        return config

    def create_treemap(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        label_field: str,
        value_field: str,
        parent_field: str | None = None,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Crear treemap.

        Args:
            data: Datos
            label_field: Campo para etiquetas
            value_field: Campo para valores (tamaño)
            parent_field: Campo para jerarquía
            title: Título

        Returns:
            Configuración del gráfico
        """
        records = self._extract_records(data)

        clean_records: list[dict[str, Any]] = []
        for r in records:
            entry: dict[str, Any] = {
                label_field: r.get(label_field, ""),
                value_field: r.get(value_field, 0),
            }
            if parent_field:
                entry[parent_field] = r.get(parent_field, "")
            clean_records.append(entry)

        values = [r[value_field] for r in clean_records]
        config = {
            "chart_type": "treemap",
            "title": title or f"Treemap de {value_field}",
            "x_key": label_field,
            "y_key": value_field,
            "parent_field": parent_field,
            "data": clean_records,
            "stats": {
                "node_count": len(records),
                "total_value": sum(values),
            },
        }

        logger.info(f"Created treemap with {len(records)} nodes")
        return config
