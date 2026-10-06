"""El RENDER de una tabla en cada formato: HTML, Markdown, texto y CSV.

Salió de `TableFormatter` (F4 del plan de calidad: table_formatter.py tenía 659 líneas), tal cual.
"""

from enum import Enum
from typing import TYPE_CHECKING

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.insights_agent.table_formatter")


class TableFormat(str, Enum):
    """Formatos de tabla disponibles."""
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"
    CSV = "csv"


class RenderTablaMixin:
    """El RENDER de una tabla en cada formato: HTML, Markdown, texto y CSV."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        DEFAULT_STYLES: str

    def _render_table(
        self,
        headers: list[str],
        rows: list[dict],
        columns: list[str],
        format_type: TableFormat,
        title: str | None,
        show_index: bool,
        numeric_columns: list[str] | None,
        truncated: bool
    ) -> str:
        """Renderizar tabla al formato especificado."""
        if format_type == TableFormat.HTML:
            return self._render_html(
                headers, rows, columns, title, show_index, numeric_columns, truncated
            )
        elif format_type == TableFormat.MARKDOWN:
            return self._render_markdown(headers, rows, title, show_index, truncated)
        elif format_type == TableFormat.TEXT:
            return self._render_text(headers, rows, title, show_index, truncated)
        elif format_type == TableFormat.CSV:
            return self._render_csv(headers, rows, show_index)

        return ""

    def _render_html(
        self,
        headers: list[str],
        rows: list[dict],
        columns: list[str],
        title: str | None,
        show_index: bool,
        numeric_columns: list[str] | None,
        truncated: bool
    ) -> str:
        """Renderizar tabla HTML.

        SEC-6c: todo dato interpolado pasa por ``html.escape`` para evitar
        XSS almacenado. Antes, valores de BD / fuentes externas con
        ``<script>`` se ejecutaban en cualquier navegador que renderizara
        el reporte.
        """
        from html import escape as _e

        numeric_cols = set(numeric_columns or [])

        html = self.DEFAULT_STYLES
        if title:
            html += f'<div class="table-title">{_e(str(title))}</div>\n'

        html += '<table class="data-table">\n<thead>\n<tr>\n'

        if show_index:
            html += '<th>#</th>\n'

        for header in headers:
            html += f'<th>{_e(str(header))}</th>\n'

        html += '</tr>\n</thead>\n<tbody>\n'

        for row in rows:
            highlight_class = ' class="highlight"' if row.get("highlight") else ""
            html += f'<tr{highlight_class}>\n'

            if show_index and row.get("index"):
                html += f'<td>{_e(str(row["index"]))}</td>\n'

            for i, value in enumerate(row["values"]):
                col_name = columns[i] if i < len(columns) else ""
                numeric_class = ' class="numeric"' if col_name in numeric_cols else ""
                html += f'<td{numeric_class}>{_e(str(value))}</td>\n'

            html += '</tr>\n'

        html += '</tbody>\n</table>\n'

        if truncated:
            html += f'<div class="table-summary">Mostrando {len(rows)} de más registros...</div>\n'

        return html

    def _render_markdown(
        self,
        headers: list[str],
        rows: list[dict],
        title: str | None,
        show_index: bool,
        truncated: bool
    ) -> str:
        """Renderizar tabla Markdown."""
        lines = []

        if title:
            lines.append(f"### {title}\n")

        # Header
        header_row = ["#"] if show_index else []
        header_row.extend(headers)
        lines.append("| " + " | ".join(header_row) + " |")

        # Separator
        sep_row = ["---"] * len(header_row)
        lines.append("| " + " | ".join(sep_row) + " |")

        # Rows
        for row in rows:
            values = []
            if show_index and row.get("index"):
                values.append(str(row["index"]))
            values.extend([str(v) for v in row["values"]])
            lines.append("| " + " | ".join(values) + " |")

        if truncated:
            lines.append(f"\n*Mostrando {len(rows)} registros...*")

        return "\n".join(lines)

    def _render_text(
        self,
        headers: list[str],
        rows: list[dict],
        title: str | None,
        show_index: bool,
        truncated: bool
    ) -> str:
        """Renderizar tabla de texto plano."""
        # Calcular anchos de columna
        all_values = [headers]
        for row in rows:
            all_values.append(row["values"])

        col_widths = []
        for i in range(len(headers)):
            max_width = len(str(headers[i]))
            for values in all_values:
                if i < len(values):
                    max_width = max(max_width, len(str(values[i])))
            col_widths.append(min(max_width, 30))  # Cap at 30 chars

        lines = []

        if title:
            lines.append(title)
            lines.append("=" * sum(col_widths))

        # Header
        header_line = ""
        for i, header in enumerate(headers):
            header_line += str(header).ljust(col_widths[i] + 2)
        lines.append(header_line)
        lines.append("-" * len(header_line))

        # Rows
        for row in rows:
            row_line = ""
            for i, value in enumerate(row["values"]):
                if i < len(col_widths):
                    row_line += str(value)[:col_widths[i]].ljust(col_widths[i] + 2)
            lines.append(row_line)

        if truncated:
            lines.append(f"... ({len(rows)} registros mostrados)")

        return "\n".join(lines)

    def _render_csv(
        self,
        headers: list[str],
        rows: list[dict],
        show_index: bool
    ) -> str:
        """Renderizar como CSV."""
        lines = []

        # Header
        header_row = ["index"] if show_index else []
        header_row.extend(headers)
        lines.append(",".join([f'"{h}"' for h in header_row]))

        # Rows
        for row in rows:
            values = []
            if show_index and row.get("index"):
                values.append(str(row["index"]))
            for v in row["values"]:
                # Escapar comillas y comas
                v_str = str(v).replace('"', '""')
                values.append(f'"{v_str}"')
            lines.append(",".join(values))

        return "\n".join(lines)
