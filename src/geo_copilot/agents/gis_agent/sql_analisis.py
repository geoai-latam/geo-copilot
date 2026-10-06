"""El ANÁLISIS de una consulta ya validada: sugerencias de optimización (índices espaciales),
complejidad estimada, tablas usadas y explicación. No decide si la consulta es segura.

Salió de `SQLValidator` (F4 del plan de calidad: sql_validator.py tenía 537 líneas), tal cual.
"""

import re
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.gis_agent.sql_validator")


class SQLAnalisisMixin:
    """El ANÁLISIS de una consulta ya validada: sugerencias de optimización (índices espaciales),"""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        max_limit: int

    # Funciones espaciales que usan índices
    INDEXED_FUNCTIONS = [
        "ST_DWithin",
        "ST_Intersects",
        "ST_Contains",
        "ST_Within",
        "ST_Covers",
        "ST_CoveredBy",
        "ST_Overlaps",
        "ST_Touches",
        "ST_Crosses",
    ]

    def optimize(self, sql: str) -> str:
        """
        Aplicar optimizaciones a una consulta SQL.

        Args:
            sql: SQL original

        Returns:
            SQL optimizado
        """
        optimized = sql

        # Agregar LIMIT si no existe
        if not re.search(r"\bLIMIT\b", optimized, re.IGNORECASE):
            # Encontrar el final del SQL (antes del último ;)
            if optimized.rstrip().endswith(";"):
                optimized = optimized.rstrip()[:-1] + f"\nLIMIT {self.max_limit};"
            else:
                optimized = optimized + f"\nLIMIT {self.max_limit}"

        # Reducir LIMIT si excede el máximo
        limit_match = re.search(r"LIMIT\s+(\d+)", optimized, re.IGNORECASE)
        if limit_match:
            current_limit = int(limit_match.group(1))
            if current_limit > self.max_limit:
                optimized = re.sub(
                    r"LIMIT\s+\d+",
                    f"LIMIT {self.max_limit}",
                    optimized,
                    flags=re.IGNORECASE
                )

        # Agregar ST_Simplify a ST_AsGeoJSON si no está
        pattern = r"ST_AsGeoJSON\s*\(\s*([^)]+)\s*\)"
        matches = re.findall(pattern, optimized, re.IGNORECASE)
        for match in matches:
            # DAT (auditoría): el patrón `[^)]+` corta en el PRIMER ')', así que
            # para un argumento con función anidada —p.ej.
            # ST_AsGeoJSON(ST_Transform(geom, 4326))— la captura queda incompleta
            # y la sustitución malformaba el SQL (metía el 0.0001 como 3er arg de
            # ST_Transform y dejaba ST_Simplify con un solo argumento). Solo
            # envolvemos cuando el argumento es SIMPLE (sin '(' anidado ni
            # subquery); con función anidada confiamos en la regla del prompt
            # (ST_Simplify obligatorio).
            if (
                "ST_Simplify" not in match
                and "(" not in match
                and "SELECT" not in match.upper()
            ):
                original = f"ST_AsGeoJSON({match})"
                replacement = f"ST_AsGeoJSON(ST_Simplify({match}, 0.0001))"
                optimized = optimized.replace(original, replacement)

        return optimized

    def estimate_complexity(self, sql: str) -> dict[str, Any]:
        """
        Estimar la complejidad de una consulta.

        Args:
            sql: Consulta SQL

        Returns:
            Estimación de complejidad
        """
        sql_upper = sql.upper()

        complexity: dict[str, Any] = {
            "score": 1,
            "factors": [],
            "estimated_time": "< 1s"
        }

        # Contar JOINs
        join_count = sql_upper.count("JOIN")
        if join_count > 0:
            complexity["score"] += join_count * 2
            complexity["factors"].append(f"{join_count} JOIN(s)")

        # Verificar funciones espaciales costosas
        expensive_functions = [
            "ST_Buffer",
            "ST_Union",
            "ST_Intersection",
            "ST_ConvexHull",
            "ST_Voronoi",
        ]
        for func in expensive_functions:
            if func.upper() in sql_upper:
                complexity["score"] += 3
                complexity["factors"].append(f"Función costosa: {func}")

        # Verificar subqueries
        subquery_count = sql_upper.count("SELECT") - 1
        if subquery_count > 0:
            complexity["score"] += subquery_count * 2
            complexity["factors"].append(f"{subquery_count} subquery(ies)")

        # Verificar agregaciones
        agg_functions = ["COUNT", "SUM", "AVG", "MIN", "MAX", "ST_UNION", "ST_COLLECT"]
        for func in agg_functions:
            if func in sql_upper:
                complexity["score"] += 1
                complexity["factors"].append(f"Agregación: {func}")
                break

        # Estimar tiempo
        if complexity["score"] <= 3:
            complexity["estimated_time"] = "< 1s"
        elif complexity["score"] <= 6:
            complexity["estimated_time"] = "1-5s"
        elif complexity["score"] <= 10:
            complexity["estimated_time"] = "5-15s"
        else:
            complexity["estimated_time"] = "> 15s"

        return complexity

    def get_tables_used(self, sql: str) -> list[str]:
        """
        Extraer las tablas usadas en la consulta.

        Args:
            sql: Consulta SQL

        Returns:
            Lista de tablas
        """
        tables = []

        # Buscar patrones FROM y JOIN
        patterns = [
            r"FROM\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)",
            r"JOIN\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, sql, re.IGNORECASE)
            tables.extend(matches)

        # Eliminar duplicados manteniendo orden
        seen = set()
        unique_tables = []
        for table in tables:
            if table.lower() not in seen:
                seen.add(table.lower())
                unique_tables.append(table)

        return unique_tables

    def explain_query(self, sql: str) -> str:
        """
        Generar versión EXPLAIN de la consulta.

        Args:
            sql: Consulta SQL

        Returns:
            Query con EXPLAIN ANALYZE
        """
        # Remover LIMIT para EXPLAIN
        sql_no_limit = re.sub(r"\s*LIMIT\s+\d+\s*;?\s*$", "", sql, flags=re.IGNORECASE)

        return f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)\n{sql_no_limit}\nLIMIT 100;"
