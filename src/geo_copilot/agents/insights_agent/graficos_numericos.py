"""Los gráficos NUMÉRICOS: dispersión (con su correlación), histograma y caja.

Salió de `ChartGenerator` (F4 del plan de calidad: chart_generator.py tenía 720 líneas), tal cual.
"""

from typing import TYPE_CHECKING, Any, cast

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.insights_agent.chart_generator")


class GraficosNumericosMixin:
    """Los gráficos NUMÉRICOS: dispersión (con su correlación), histograma y caja."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        def _extract_records(self, data: list | dict) -> list[dict]: ...
        def _empty_chart_config(self, title: str | None = None) -> dict[str, Any]: ...

    def create_scatter_chart(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        x_field: str,
        y_field: str,
        title: str | None = None,
        x_label: str | None = None,
        y_label: str | None = None,
        size_field: str | None = None,
        color_field: str | None = None,
        label_field: str | None = None
    ) -> dict[str, Any]:
        """
        Crear gráfico de dispersión.

        Args:
            data: Datos
            x_field: Campo para eje X
            y_field: Campo para eje Y
            title: Título
            x_label: Etiqueta eje X
            y_label: Etiqueta eje Y
            size_field: Campo para tamaño de puntos
            color_field: Campo para color de puntos
            label_field: Campo para etiquetas

        Returns:
            Configuración del gráfico
        """
        records = self._extract_records(data)

        clean_records: list[dict[str, Any]] = []
        for r in records:
            entry: dict[str, Any] = {
                x_field: r.get(x_field, 0),
                y_field: r.get(y_field, 0),
            }
            if size_field:
                entry[size_field] = r.get(size_field, 10)
            if color_field:
                entry[color_field] = r.get(color_field)
            if label_field:
                entry[label_field] = r.get(label_field, "")
            clean_records.append(entry)

        x_values = [r[x_field] for r in clean_records]
        y_values = [r[y_field] for r in clean_records]
        correlation = self._calculate_correlation(x_values, y_values)

        config = {
            "chart_type": "scatter",
            "title": title or f"{y_field} vs {x_field}",
            "x_key": x_field,
            "y_key": y_field,
            "size_field": size_field,
            "color_field": color_field,
            "label_field": label_field,
            "data": clean_records,
            "x_label": x_label or x_field,
            "y_label": y_label or y_field,
            "stats": {
                "point_count": len(records),
                "correlation": correlation,
                "x_range": [min(x_values), max(x_values)] if x_values else None,
                "y_range": [min(y_values), max(y_values)] if y_values else None,
            },
        }

        logger.info(f"Created scatter: {len(records)} points, x_key={x_field}, y_key={y_field}")
        return config

    def create_histogram(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        value_field: str,
        title: str | None = None,
        x_label: str | None = None,
        bins: int = 20
    ) -> dict[str, Any]:
        """
        Crear histograma.

        Args:
            data: Datos
            value_field: Campo para valores
            title: Título
            x_label: Etiqueta eje X
            bins: Número de bins

        Returns:
            Configuración del gráfico
        """
        records = self._extract_records(data)

        values = [r.get(value_field, 0) for r in records if r.get(value_field) is not None]

        if not values:
            return self._empty_chart_config(title)

        mean_val = sum(values) / len(values)
        sorted_vals = sorted(values)
        median_val = sorted_vals[len(sorted_vals) // 2]

        # Pre-binning para que el frontend (Recharts) lo dibuje como bar
        # chart. histogram nativo no está en Chart.tsx; el binning aquí lo
        # convierte en data: [{bin_label, count}].
        lo, hi = min(values), max(values)
        if hi == lo:
            bins = 1
            edges = [lo, hi + 1]
        else:
            step = (hi - lo) / bins
            edges = [lo + i * step for i in range(bins + 1)]
        counts = [0] * bins
        for v in values:
            idx = min(int((v - lo) / (hi - lo) * bins), bins - 1) if hi > lo else 0
            counts[idx] += 1
        bin_key = value_field
        count_key = "frecuencia"
        clean_records = [
            {
                bin_key: f"{edges[i]:.1f}–{edges[i + 1]:.1f}",
                count_key: counts[i],
            }
            for i in range(bins)
        ]

        config = {
            "chart_type": "histogram",
            "title": title or f"Distribución de {value_field}",
            "x_key": bin_key,
            "y_key": count_key,
            "data": clean_records,
            "value_field": value_field,
            "raw_values": values,  # disponible para downstream que use Plotly real
            "x_label": x_label or value_field,
            "y_label": "Frecuencia",
            "stats": {
                "count": len(values),
                "mean": round(mean_val, 2),
                "median": median_val,
                "min": min(values),
                "max": max(values),
                "std": self._calculate_std(values, mean_val),
            },
        }

        logger.info(f"Created histogram: {len(values)} values, {bins} bins")
        return config

    def create_box_plot(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        value_field: str,
        group_field: str | None = None,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Crear box plot.

        Args:
            data: Datos
            value_field: Campo para valores
            group_field: Campo para agrupar
            title: Título

        Returns:
            Configuración del gráfico
        """
        records = self._extract_records(data)

        if group_field:
            groups: dict[Any, list] = {}
            for r in records:
                group = r.get(group_field, "N/A")
                if group not in groups:
                    groups[group] = []
                val = r.get(value_field)
                if val is not None:
                    groups[group].append(val)

            box_data = [
                {"name": name, "values": vals}
                for name, vals in groups.items()
            ]
        else:
            values = [r.get(value_field) for r in records if r.get(value_field) is not None]
            box_data = [{"name": value_field, "values": values}]

        config = {
            "chart_type": "box",
            "title": title or f"Distribución de {value_field}",
            "x_key": "name",
            "y_key": "values",
            "data": box_data,  # ya en forma [{name, values: []}]
            "value_field": value_field,
            "group_field": group_field,
            "y_label": value_field,
            "stats": {"group_count": len(box_data)},
        }

        logger.info(f"Created box plot with {len(box_data)} groups")
        return config

    def _calculate_correlation(self, x: list, y: list) -> float | None:
        """Calcular correlación de Pearson simple."""
        if len(x) != len(y) or len(x) < 2:
            return None

        try:
            n = len(x)
            mean_x = sum(x) / n
            mean_y = sum(y) / n

            numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
            denominator_x = sum((x[i] - mean_x) ** 2 for i in range(n)) ** 0.5
            denominator_y = sum((y[i] - mean_y) ** 2 for i in range(n)) ** 0.5

            if denominator_x == 0 or denominator_y == 0:
                return None

            return cast(float, round(numerator / (denominator_x * denominator_y), 3))
        except (TypeError, ValueError):
            return None

    def _calculate_std(self, values: list, mean: float) -> float:
        """Calcular desviación estándar."""
        if len(values) < 2:
            return 0.0

        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return cast(float, round(variance ** 0.5, 2))
