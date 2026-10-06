"""Las tablas DERIVADAS de los datos: resumen por grupos, comparación y ranking.

Salió de `TableFormatter` (F4 del plan de calidad: table_formatter.py tenía 659 líneas), tal cual.
"""

from typing import TYPE_CHECKING, Any

from geo_copilot.agents.insights_agent.render_tabla import TableFormat
from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.insights_agent.table_formatter")


class TablasDerivadasMixin:
    """Las tablas DERIVADAS de los datos: resumen por grupos, comparación y ranking."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        def format_table(
            self,
            data: list[dict[str, Any]] | dict[str, Any],
            columns: list[str] | None = None,
            column_labels: dict[str, str] | None = None,
            title: str | None = None,
            format_type: TableFormat | None = None,
            show_index: bool = False,
            numeric_columns: list[str] | None = None,
            highlight_column: str | None = None,
            highlight_condition: str | None = None
        ) -> dict[str, Any]: ...
        def _extract_records(self, data: list | dict) -> list[dict]: ...
        def _format_value(self, value: Any) -> str: ...
        def _empty_table_config(self, title: str | None = None) -> dict[str, Any]: ...

    def create_summary_table(  # noqa: C901, PLR0912
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        group_by: str,
        metrics: list[dict[str, str]],
        title: str | None = None,
        format_type: TableFormat | None = None
    ) -> dict[str, Any]:
        """
        Crear tabla resumen con agregaciones.

        Args:
            data: Datos a agregar
            group_by: Campo para agrupar
            metrics: Lista de métricas [{"field": "area", "agg": "sum", "label": "Área Total"}]
            title: Título
            format_type: Formato de salida

        Returns:
            Configuración de tabla resumen
        """
        records = self._extract_records(data)

        if not records:
            return self._empty_table_config(title)

        # Agrupar datos
        groups: dict[Any, list] = {}
        for record in records:
            key = record.get(group_by, "N/A")
            if key not in groups:
                groups[key] = []
            groups[key].append(record)

        # Calcular métricas por grupo
        summary_rows = []
        for group_name, group_records in sorted(groups.items()):
            row = {group_by: group_name, "_count": len(group_records)}

            for metric in metrics:
                field = metric["field"]
                agg = metric.get("agg", "sum")
                values = [
                    r.get(field, 0) for r in group_records
                    if r.get(field) is not None
                ]

                if values:
                    if agg == "sum":
                        row[field] = sum(values)
                    elif agg == "avg":
                        row[field] = sum(values) / len(values)
                    elif agg == "count":
                        row[field] = len(values)
                    elif agg == "min":
                        row[field] = min(values)
                    elif agg == "max":
                        row[field] = max(values)
                else:
                    row[field] = None

            summary_rows.append(row)

        # Preparar columnas
        columns = [group_by] + [m["field"] for m in metrics]
        column_labels = {group_by: group_by.replace("_", " ").title()}
        for m in metrics:
            column_labels[m["field"]] = m.get("label", m["field"])

        return self.format_table(
            summary_rows,
            columns=columns,
            column_labels=column_labels,
            title=title or f"Resumen por {group_by}",
            format_type=format_type,
            numeric_columns=[m["field"] for m in metrics]
        )

    def create_comparison_table(
        self,
        datasets: list[dict[str, Any]],
        compare_fields: list[str],
        dataset_labels: list[str] | None = None,
        title: str | None = None,
        format_type: TableFormat | None = None
    ) -> dict[str, Any]:
        """
        Crear tabla comparativa entre datasets.

        Args:
            datasets: Lista de datasets a comparar
            compare_fields: Campos a comparar
            dataset_labels: Etiquetas para cada dataset
            title: Título
            format_type: Formato

        Returns:
            Configuración de tabla comparativa
        """
        if not datasets:
            return self._empty_table_config(title)

        labels = dataset_labels or [f"Dataset {i+1}" for i in range(len(datasets))]

        # Calcular estadísticas para cada dataset
        comparison_rows = []
        for field in compare_fields:
            row: dict[str, Any] = {"metric": field}

            for i, dataset in enumerate(datasets):
                records = self._extract_records(dataset)
                values: list[Any] = [
                    r.get(field) for r in records
                    if r.get(field) is not None
                ]

                if values and all(isinstance(v, (int, float)) for v in values):
                    row[labels[i]] = {
                        "count": len(values),
                        "sum": sum(values),
                        "avg": sum(values) / len(values),
                        "min": min(values),
                        "max": max(values),
                    }
                else:
                    row[labels[i]] = {"count": len(values)}

            comparison_rows.append(row)

        # Formatear para tabla
        table_rows = []
        for row in comparison_rows:
            table_row = {"Métrica": row["metric"]}
            for label in labels:
                stats: dict[str, Any] = row.get(label, {})
                if "avg" in stats:
                    table_row[f"{label} (Promedio)"] = self._format_value(stats["avg"])
                    table_row[f"{label} (Total)"] = self._format_value(stats["sum"])
                else:
                    table_row[f"{label} (Conteo)"] = stats.get("count", 0)
            table_rows.append(table_row)

        columns = ["Métrica"]
        for label in labels:
            if comparison_rows and "avg" in comparison_rows[0].get(label, {}):
                columns.extend([f"{label} (Promedio)", f"{label} (Total)"])
            else:
                columns.append(f"{label} (Conteo)")

        return self.format_table(
            table_rows,
            columns=columns,
            title=title or "Comparación de Datasets",
            format_type=format_type
        )

    def create_ranking_table(
        self,
        data: list[dict[str, Any]] | dict[str, Any],
        rank_field: str,
        display_fields: list[str],
        title: str | None = None,
        ascending: bool = False,
        top_n: int = 10,
        format_type: TableFormat | None = None
    ) -> dict[str, Any]:
        """
        Crear tabla de ranking.

        Args:
            data: Datos
            rank_field: Campo para ordenar
            display_fields: Campos a mostrar
            title: Título
            ascending: Orden ascendente
            top_n: Top N elementos
            format_type: Formato

        Returns:
            Configuración de tabla ranking
        """
        records = self._extract_records(data)

        if not records:
            return self._empty_table_config(title)

        # Ordenar
        sorted_records = sorted(
            [r for r in records if r.get(rank_field) is not None],
            key=lambda x: x.get(rank_field, 0),
            reverse=not ascending
        )[:top_n]

        # Agregar posición
        for i, record in enumerate(sorted_records):
            record["_rank"] = i + 1

        columns = ["_rank"] + display_fields + [rank_field]
        column_labels = {
            "_rank": "#",
            rank_field: rank_field.replace("_", " ").title()
        }

        return self.format_table(
            sorted_records,
            columns=columns,
            column_labels=column_labels,
            title=title or f"Top {top_n} por {rank_field}",
            format_type=format_type,
            numeric_columns=[rank_field],
            highlight_column="_rank",
            highlight_condition="<=3"
        )
