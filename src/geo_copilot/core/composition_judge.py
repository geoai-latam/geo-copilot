"""Juez composicional (Fase 5 / F5) — ¿la respuesta CUBRE la consulta?

Versatilidad abierta + validación composicional. El bucle ReAct (F4.4) compone
herramientas dinámicamente para resolver peticiones abiertas. F5 añade una
auto-validación del RESULTADO de esa composición: cuando el agente va a
responder (`answer`), un juez evalúa si la respuesta realmente atiende la
consulta ORIGINAL dadas las herramientas usadas. Si no — y queda presupuesto —
el bucle no acepta la respuesta todavía: empuja al agente a seguir (o a explicar
honestamente por qué no se puede), en vez de cerrar con una respuesta evasiva.

**Sesgo conservador** (igual que el juez de resultados F2.3): el costo de molestar
al usuario con un bucle de re-trabajo innecesario supera al de aceptar una
respuesta razonable. Por eso: ante fallo de LLM, parseo dudoso, o baja confianza,
se ACEPTA la respuesta (``addressed=True``). Solo una brecha CONCRETA y de alta
confianza dispara más trabajo. Acotado por ``react_max_reflections`` (el bucle).
"""

from __future__ import annotations

from dataclasses import dataclass

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm
from geo_copilot.prompts import cargar_prompt

logger = get_logger(__name__)

_MIN_CONFIDENCE = 0.7


@dataclass
class AnswerVerdict:
    """¿La respuesta atiende la consulta?"""

    addressed: bool
    confidence: float
    reason: str
    missing: str | None = None

    @property
    def needs_more_work(self) -> bool:
        """¿Vale la pena seguir? Solo si NO atiende y con alta confianza."""
        return (not self.addressed) and self.confidence >= _MIN_CONFIDENCE


_ACCEPT = AnswerVerdict(addressed=True, confidence=1.0, reason="aceptada (sin evidencia de brecha)")


_PROMPT = cargar_prompt("juez_composicion")


async def judge_answer(
    *, query: str, answer: str, tools_used: list[str], llm_client,
    observaciones: list[str] | None = None,
) -> AnswerVerdict:
    """Juzga si ``answer`` atiende ``query``. Degrada a ATENDIDA ante cualquier
    fallo (honesto: no genera re-trabajo desde una incertidumbre de infra)."""
    if llm_client is None or not (answer or "").strip():
        return _ACCEPT

    prompt = _PROMPT.format(
        query=query,
        tools=", ".join(tools_used) if tools_used else "(ninguna)",
        answer=answer[:1500],
        # V5 F4: con solo los NOMBRES de las herramientas, «coloreé por área en 5
        # clases» pasaba aunque la simbología hubiera reportado UN SOLO COLOR.
        hechos="\n".join(f"- {o}" for o in (observaciones or [])[-10:]) or "(sin datos)",
    )
    try:
        response = await llm_client.chat([LLMMessage(role="user", content=prompt)])
    except Exception as exc:  # noqa: BLE001 — fallo de LLM no genera re-trabajo
        logger.warning(f"[CompositionJudge] LLM falló ({exc}) — respuesta aceptada")
        return _ACCEPT

    data = parse_json_from_llm(getattr(response, "content", "") or "", default={})
    if "addressed" not in data:
        # Visible: con un modelo que no sigue el formato, el juez se apagaba sin que nadie lo supiera.
        logger.warning("[CompositionJudge] respuesta del juez sin veredicto legible — aceptada SIN juzgar")
        return _ACCEPT
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    addressed = bool(data.get("addressed"))
    missing = data.get("missing")
    missing = str(missing).strip() if missing not in (None, "", "null") else None
    return AnswerVerdict(
        addressed=addressed,
        confidence=confidence,
        reason=str(data.get("reason", "")).strip() or "(sin razón)",
        missing=missing,
    )
