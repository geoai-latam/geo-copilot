"""El JUEZ de la salida: ¿el resultado responde lo que se pidió? Si no, un reintento con su motivo.

Salió de `PythonAgent` (F4 del plan de calidad: agent.py tenía 1.181 líneas), tal cual.
"""

import json
from typing import TYPE_CHECKING

from geo_copilot.agents.base import AgentResponse
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.agents.base import AgentResponse
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.python_agent.agent")


def _resumen_salida(result_geojson: dict | None, table: list | None, stats: dict | None, chart: dict | None,
                    feature_count: int) -> list[str]:
    """Resumen fáctico de lo que produjo el código (lo que el juez compara con la solicitud)."""
    resumen: list[str] = []
    if result_geojson is not None:
        resumen.append(f"- GeoDataFrame `result`: {feature_count} features")
    if table is not None:
        head = json.dumps(table[:2], ensure_ascii=False, default=str)[:300]
        resumen.append(f"- table: {len(table)} filas; primeras: {head}")
    if stats is not None:
        resumen.append(
            f"- stats: {json.dumps(stats, ensure_ascii=False, default=str)[:300]}"
        )
    if chart is not None:
        n_pts = len(chart.get("data") or []) if isinstance(chart, dict) else 0
        ct = chart.get("chart_type") if isinstance(chart, dict) else "?"
        resumen.append(f"- chart: tipo {ct}, {n_pts} puntos")
    if not resumen:
        resumen.append("- (sin salidas)")
    return resumen


class JuezMixin:
    """El juez de la salida: ¿responde lo que se pidió? Si no, un reintento con su motivo."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient

        async def process(self, query: str, context: dict | None = None) -> AgentResponse: ...

    async def _judge_output_responds(
        self,
        *,
        query: str,
        result_geojson: dict | None,
        table: list | None,
        stats: dict | None,
        chart: dict | None,
        feature_count: int,
    ) -> dict | None:
        """A4(b): juicio LIGERO — ¿la salida responde la solicitud?

        Devuelve ``{"responds": bool, "reason": str}`` o ``None`` si el juez
        no pudo correr (sin LLM / error) — en ese caso el resultado exitoso
        se entrega tal cual (el juez es red de seguridad, no gate).
        """
        if self.llm_client is None:
            return None

        resumen = _resumen_salida(result_geojson, table, stats, chart, feature_count)

        prompt = (
            f"SOLICITUD DEL USUARIO: {query!r}\n\n"
            f"SALIDA PRODUCIDA por el código (resumen fáctico):\n"
            + "\n".join(resumen)
            + "\n\n¿Esta salida RESPONDE lo que el usuario pidió? Juzga el TIPO y "
            "CONTENIDO de la salida contra la solicitud (p.ej. pidió correlación "
            "y la salida es solo un mapa → no responde; pidió conteos por grupo "
            "y la tabla los tiene → responde). Sé conservador: ante la duda, "
            "responde que SÍ (no castigues salidas razonables)."
        )
        try:
            from geo_copilot.core.structured_output import structured_call
            verdict = await structured_call(
                self.llm_client,
                [LLMMessage(role="user", content=prompt)],
                name="judge_output",
                description="Registra si la salida responde la solicitud.",
                parameters={
                    "type": "object",
                    "properties": {
                        "responds": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["responds", "reason"],
                    "additionalProperties": False,
                },
                max_tokens=300,
            )
            return verdict
        except Exception as exc:  # noqa: BLE001 — el juez nunca tumba un éxito
            logger.debug(f"[PythonAgent] A4 judge falló (se entrega el resultado): {exc}")
            return None

    async def _retry_with_judge_reason(
        self,
        *,
        query: str,
        context: dict,
        code: str,
        reason: str,
        geojson_info: dict,
    ) -> AgentResponse | None:
        """A4(b): UNA corrección dirigida por la razón del juez y re-proceso.

        Reusa la maquinaria de corrección (CodeCorrector) con el "error"
        semántico como input. ``_a4_verified_retry`` evita recursión: el
        reintento no se vuelve a juzgar-reintentar. ``None`` = no se pudo
        corregir (el caller entrega el resultado original).
        """
        try:
            from geo_copilot.agents.python_agent.code_corrector import CodeCorrector
            corrector = CodeCorrector(self.llm_client)
            corrected = await corrector.correct_code(
                code=code,
                error=(
                    "El código ejecutó SIN errores pero el RESULTADO no responde "
                    f"la solicitud del usuario: {reason}. Corrige el código para "
                    "que la salida responda exactamente lo pedido."
                ),
                columns=geojson_info.get("columns", []),
                feature_count=geojson_info.get("count", 0),
                query=query,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[PythonAgent] A4 corrección falló: {exc}")
            return None
        if not corrected or corrected == code:
            return None
        retry_context = {
            **context,
            "_use_corrected_code": corrected,
            "_a4_verified_retry": True,
        }
        return await self.process(query, retry_context)
