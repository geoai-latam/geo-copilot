"""La INFERENCIA de la visualización de un resultado (mapa, gráfico o tabla): la decide el LLM
viendo la pregunta, el SQL y la forma de los datos.

Salió de `InsightsAgent` (F4 del plan de calidad: agent.py tenía 1.610 líneas), tal cual.
"""

import json
from typing import TYPE_CHECKING

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.insights_agent.agent")


def _columnas(data: list[dict]) -> tuple[list, list, list]:
    """Las columnas del resultado (sin la geometría): todas, numéricas y categóricas."""
    # Preparar información sobre los datos para el LLM
    sample = data[0] if data else {}
    columns = [col for col in sample.keys() if col not in ('geom', 'geometry', 'geom_geojson')]

    # Detectar tipos de columnas
    numeric_cols = []
    categorical_cols = []
    for col in columns:
        val = sample.get(col)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            numeric_cols.append(col)
        else:
            categorical_cols.append(col)
    return columns, numeric_cols, categorical_cols


def _prompt_viz(query: str | None, sql: str | None, data: list[dict], columns: list, numeric_cols: list,
                categorical_cols: list, has_geometry: bool, feature_count: int) -> str:
    """Lo que el LLM ve para decidir la visualización: la pregunta, el SQL y la forma de los datos."""
    # Prompt para que el LLM decida
    return f"""Analiza la consulta del usuario y los datos obtenidos para determinar la mejor forma de visualización.

CONSULTA DEL USUARIO: "{query or 'Sin consulta'}"

SQL EJECUTADO: {sql or 'N/A'}

DATOS OBTENIDOS:
- Filas: {len(data)}
- Columnas: {columns}
- Columnas numéricas: {numeric_cols}
- Columnas categóricas: {categorical_cols}
- Tiene geometría para mapa: {has_geometry}
- Features geográficos: {feature_count}
- Muestra de datos: {json.dumps(data[:2], default=str)[:500] if data else 'N/A'}

Basándote en la INTENCIÓN del usuario y la ESTRUCTURA de los datos, decide qué tipo de visualización es más apropiada:

1. "map" - Si el usuario quiere ver ubicaciones, distribución espacial, o los datos tienen geometría Y el usuario quiere verlos en el mapa
2. "chart" - Si el usuario quiere ver estadísticas, distribuciones, comparaciones, tendencias, o pide explícitamente una gráfica
3. "table" - Si el usuario quiere ver datos tabulares, listados, o detalles específicos

Reporta tu decisión llamando a la función `design_visualization`."""


#: la forma de la decisión (structured output de `design_visualization`)
_PARAMETROS_VIZ = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["map", "chart", "table"]},
        "chart_type": {
            "type": ["string", "null"],
            "enum": ["bar", "pie", "line", None],
            "description": "Solo si type=chart.",
        },
        "x_axis": {"type": ["string", "null"], "description": "Solo si type=chart."},
        "y_axis": {"type": ["string", "null"], "description": "Solo si type=chart."},
        "reasoning": {"type": "string"},
    },
    "required": ["type", "reasoning"],
    "additionalProperties": False,
}


def _visualizacion(viz_type: str, result: dict, reasoning: str, has_geometry: bool, feature_count: int,
                  columns: list, numeric_cols: list, categorical_cols: list, n_filas: int) -> dict:
    """La decisión del LLM como la visualización que consume el frontend."""
    if viz_type == "map" and has_geometry:
        return {
            "type": "map",
            "feature_count": feature_count,
            "reasoning": reasoning
        }
    elif viz_type == "chart":
        return {
            "type": "chart",
            "chart_type": result.get("chart_type", "bar"),
            "x_axis": result.get("x_axis", categorical_cols[0] if categorical_cols else columns[0] if columns else None),
            "y_axis": result.get("y_axis", numeric_cols[0] if numeric_cols else columns[1] if len(columns) > 1 else None),
            "columns": columns,
            "reasoning": reasoning
        }
    else:
        return {
            "type": "table",
            "columns": columns,
            "row_count": n_filas,
            "reasoning": reasoning
        }


class InferenciaMixin:
    """La visualización apropiada para un resultado, decidida por el LLM."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient | None

    async def infer_visualization_type(
        self,
        query: str | None,
        data: list[dict],
        geojson: dict | None,
        sql: str | None
    ) -> dict:
        """
        Usar LLM para inferir el tipo de visualización apropiado basado en la intención del usuario.

        Devuelve la visualización inferida por el LLM; ``ValueError`` si no hay LLM configurado.
        """
        if not self.llm_client:
            raise ValueError("LLM client is required for visualization inference")

        if not data:
            return {"type": "empty"}

        columns, numeric_cols, categorical_cols = _columnas(data)

        has_geometry = bool(geojson and geojson.get("features"))
        feature_count = len(geojson.get("features", [])) if geojson else 0

        prompt = _prompt_viz(query, sql, data, columns, numeric_cols, categorical_cols, has_geometry,
                             feature_count)

        try:
            # A3 (structured outputs): la decisión llega como llamada a función
            # con schema — sin brace-slicing ni default "table" silencioso. Un
            # fallo de forma lanza (el responder ya trata este método como
            # best-effort, R4.6).
            from geo_copilot.core.structured_output import structured_call
            result = await structured_call(
                self.llm_client,
                [LLMMessage(role="user", content=prompt)],
                name="design_visualization",
                description="Registra el tipo de visualización apropiado para el resultado.",
                parameters=_PARAMETROS_VIZ,
            )

            viz_type = result.get("type", "table")
            reasoning = result.get("reasoning", "")

            logger.info(f"[InsightsAgent] LLM inferred visualization: {viz_type} - {reasoning}")

            return _visualizacion(viz_type, result, reasoning, has_geometry, feature_count, columns,
                                  numeric_cols, categorical_cols, len(data))

        except Exception as e:
            logger.error(f"Error inferring visualization type: {e}")
            raise
