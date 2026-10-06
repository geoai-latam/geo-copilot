"""geo_sql_guard — lo que decide si un SQL de un LLM es una LECTURA admisible (sqlglot) y si
un CRS sirve para medir (pyproj). Sin dependencias del núcleo: lo usan el núcleo y los
servidores MCP de SQL (T5.1)."""

from geo_sql_guard.ast import ResultadoAST, analizar, fijar_srid_de_columnas
from geo_sql_guard.crs import check_measurable_crs, is_metric_crs

__all__ = ["ResultadoAST", "analizar", "check_measurable_crs", "fijar_srid_de_columnas", "is_metric_crs"]
