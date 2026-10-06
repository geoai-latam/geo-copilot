"""
Formateador de tablas para presentación de datos.

Genera tablas HTML, Markdown y texto plano a partir de
resultados de análisis.
"""

from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# F4: las tablas derivadas y el render por formato viven en sus módulos (mixins).
from geo_copilot.agents.insights_agent.render_tabla import RenderTablaMixin, TableFormat
from geo_copilot.agents.insights_agent.tablas_derivadas import TablasDerivadasMixin


class TableFormatter(TablasDerivadasMixin, RenderTablaMixin):
    """
    Formateador de tablas con múltiples formatos de salida.

    Soporta:
    - Tablas HTML con estilos
    - Tablas Markdown para documentación
    - Tablas de texto plano para consola
    - Exportación a CSV
    - Tablas resumen con estadísticas
    - Tablas comparativas
    """

    # Estilos CSS para tablas HTML
    DEFAULT_STYLES = """
    <style>
        .data-table {
            border-collapse: collapse;
            width: 100%;
            font-family: Arial, sans-serif;
            font-size: 14px;
        }
        .data-table th, .data-table td {
            border: 1px solid #ddd;
            padding: 8px 12px;
            text-align: left;
        }
        .data-table th {
            background-color: #4CAF50;
            color: white;
            font-weight: bold;
        }
        .data-table tr:nth-child(even) {
            background-color: #f9f9f9;
        }
        .data-table tr:hover {
            background-color: #f1f1f1;
        }
        .data-table .numeric {
            text-align: right;
        }
        .data-table .highlight {
            background-color: #fff3cd;
        }
        .table-title {
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
            color: #333;
        }
        .table-summary {
            font-size: 12px;
            color: #666;
            margin-top: 10px;
        }
    </style>
    """

    def __init__(
        self,
        default_format: TableFormat = TableFormat.HTML,
        max_rows: int = 100,
        max_columns: int = 20
    ):
        """
        Inicializar formateador de tablas.

        Args:
            default_format: Formato por defecto
            max_rows: Máximo de filas a mostrar
            max_columns: Máximo de columnas a mostrar
        """
        self.default_format = default_format
        self.max_rows = max_rows
        self.max_columns = max_columns

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
    ) -> dict[str, Any]:
        """
        Formatear datos como tabla.

        Args:
            data: Datos a formatear
            columns: Columnas a incluir (None = todas)
            column_labels: Etiquetas personalizadas para columnas
            title: Título de la tabla
            format_type: Formato de salida
            show_index: Mostrar índice de fila
            numeric_columns: Columnas numéricas (alineación derecha)
            highlight_column: Columna para resaltar
            highlight_condition: Condición para resaltar (ej: ">100")

        Returns:
            Configuración de tabla con contenido formateado
        """
        records = self._extract_records(data)

        if not records:
            return self._empty_table_config(title)

        # Determinar columnas
        if columns is None:
            columns = list(records[0].keys())[:self.max_columns]

        # Limitar filas
        truncated = len(records) > self.max_rows
        records = records[:self.max_rows]

        # Preparar etiquetas
        labels = column_labels or {}
        headers = [labels.get(col, col) for col in columns]

        # Preparar filas
        rows = []
        for i, record in enumerate(records):
            row = []
            for col in columns:
                value = record.get(col, "")
                row.append(self._format_value(value))
            rows.append({
                "index": i + 1 if show_index else None,
                "values": row,
                "highlight": self._should_highlight(
                    record, highlight_column, highlight_condition
                )
            })

        format_type = format_type or self.default_format

        config = {
            "title": title,
            "columns": columns,
            "headers": headers,
            "rows": rows,
            "numeric_columns": numeric_columns or [],
            "show_index": show_index,
            "truncated": truncated,
            "total_rows": len(records),
            "format": format_type.value,
            "rendered": self._render_table(
                headers, rows, columns, format_type, title,
                show_index, numeric_columns, truncated
            )
        }

        logger.info(f"Formatted table with {len(records)} rows")
        return config


    def _extract_records(self, data: list | dict) -> list[dict]:
        """Extraer registros de diferentes formatos."""
        if isinstance(data, list):
            return data

        if "features" in data:
            return [f.get("properties", {}) for f in data.get("features", [])]

        if "data" in data:
            return self._extract_records(data["data"])

        return [data]

    def _format_value(self, value: Any) -> str:
        """Formatear valor para presentación."""
        if value is None:
            return "-"
        elif isinstance(value, float):
            if abs(value) >= 1000000:
                return f"{value/1000000:.2f}M"
            elif abs(value) >= 1000:
                return f"{value/1000:.2f}K"
            else:
                return f"{value:.2f}"
        elif isinstance(value, bool):
            return "Sí" if value else "No"
        elif isinstance(value, (list, dict)):
            return str(value)[:50] + "..." if len(str(value)) > 50 else str(value)

        return str(value)

    def _should_highlight(
        self,
        record: dict,
        column: str | None,
        condition: str | None
    ) -> bool:
        """Determinar si una fila debe resaltarse."""
        if not column or not condition:
            return False

        value = record.get(column)
        if value is None:
            return False

        try:
            if condition.startswith(">="):
                return float(value) >= float(condition[2:])
            elif condition.startswith("<="):
                return float(value) <= float(condition[2:])
            elif condition.startswith(">"):
                return float(value) > float(condition[1:])
            elif condition.startswith("<"):
                return float(value) < float(condition[1:])
            elif condition.startswith("="):
                return str(value) == condition[1:]
        except (TypeError, ValueError):
            pass

        return False

    def _empty_table_config(self, title: str | None = None) -> dict[str, Any]:
        """Configuración para tabla vacía."""
        return {
            "title": title,
            "columns": [],
            "headers": [],
            "rows": [],
            "total_rows": 0,
            "rendered": "<p>No hay datos disponibles</p>",
            "message": "Sin datos"
        }
