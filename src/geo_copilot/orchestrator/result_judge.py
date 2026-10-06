"""ResultJudge (Fase 2 / F2.3) — juez semántico de resultados vacíos.

El gap que cierra: cuando una consulta SQL ejecuta sin error pero devuelve 0
filas, el sistema lo trataba SIEMPRE como éxito y reportaba "0 resultados". Pero
0 puede significar dos cosas MUY distintas:

  - 0-por-realidad: la respuesta honesta es "no hay" (no hay escuelas a 500 m de
    ese río). Reintentar aquí es dañino: relajar la consulta fabricaría datos que
    NO responden la pregunta y los presentaría como si la respondieran.
  - 0-por-bug: el SQL tiene un defecto que produce 0 espurio (igualdad sobre un
    string con mayúsculas/acentos distintos, un predicado espacial que mezcla
    grados y metros, una columna equivocada, un rango de fecha demasiado
    estrecho). Aquí SÍ vale corregir y reintentar.

El juez decide cuál de las dos es. **Sesgo conservador**: solo declara
``likely_bug`` cuando puede NOMBRAR un defecto concreto y con confianza; ante la
duda, ``plausibly_real`` (preferimos un 0 honesto a un resultado inventado).
Esto es coherente con el principio del proyecto: si no hay evidencia de bug, no
adivinamos uno.
"""

from __future__ import annotations

from dataclasses import dataclass

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm
from geo_copilot.prompts import cargar_prompt

logger = get_logger(__name__)

# Umbral de confianza para tratar un veredicto como bug accionable. Por debajo
# de esto, aunque el LLM diga "bug", lo tratamos como real (no reintentamos): el
# costo de un falso positivo (fabricar datos) supera al de un 0 honesto. 0.7 deja
# margen ante el ruido de calibración del LLM en la zona 0.5-0.7 (revisión
# adversarial F2.3): solo un bug de alta confianza dispara un reintento que
# relaja la consulta.
DEFAULT_MIN_CONFIDENCE = 0.7


@dataclass
class EmptyResultVerdict:
    """Veredicto del juez sobre un resultado vacío (0 filas)."""

    verdict: str  # "likely_bug" | "plausibly_real"
    confidence: float
    reason: str
    suggested_fix: str | None = None

    @property
    def is_bug(self) -> bool:
        """¿Debe tratarse como bug accionable (corregir + reintentar)?"""
        return (
            self.verdict == "likely_bug"
            and self.confidence >= DEFAULT_MIN_CONFIDENCE
        )

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "reason": self.reason,
            "suggested_fix": self.suggested_fix,
        }


_REAL = EmptyResultVerdict(
    verdict="plausibly_real",
    confidence=1.0,
    reason="Sin evidencia de defecto: se reporta el 0 como respuesta honesta.",
    suggested_fix=None,
)


_JUDGE_PROMPT = cargar_prompt("juez_resultado")


async def judge_empty_result(
    *,
    query: str,
    sql: str,
    schema_info: str,
    llm_client,
) -> EmptyResultVerdict:
    """Juzga un resultado vacío: ``likely_bug`` vs ``plausibly_real``.

    Devuelve un ``EmptyResultVerdict``. Degrada a ``plausibly_real`` (honesto, sin
    reintento) si no hay LLM o el parseo falla — NUNCA inventa un bug ante un
    fallo de infraestructura, porque un falso positivo fabricaría datos.
    """
    if llm_client is None:
        logger.info("[ResultJudge] sin LLM — 0 filas tratado como real (honesto)")
        return _REAL

    prompt = _JUDGE_PROMPT.format(
        query=query,
        sql=sql,
        schema=(schema_info or "(esquema no disponible)")[:4000],
    )
    try:
        response = await llm_client.chat([LLMMessage(role="user", content=prompt)])
    except Exception as exc:  # noqa: BLE001 — fallo de LLM no debe inventar bug
        logger.warning(f"[ResultJudge] LLM falló ({exc}) — 0 tratado como real")
        return _REAL

    data = parse_json_from_llm(getattr(response, "content", "") or "", default={})
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in ("likely_bug", "plausibly_real"):
        logger.warning(f"[ResultJudge] veredicto inválido '{verdict}' — real")
        return _REAL

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(data.get("reason", "")).strip() or "(sin razón)"
    fix = data.get("suggested_fix")
    suggested_fix = str(fix).strip() if fix not in (None, "", "null") else None

    return EmptyResultVerdict(
        verdict=verdict,
        confidence=confidence,
        reason=reason,
        suggested_fix=suggested_fix,
    )
