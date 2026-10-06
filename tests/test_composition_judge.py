"""F5: juez composicional (¿la respuesta cubre la consulta?) + sesgo conservador.

VALIDACIÓN CRÍTICA: solo una brecha CONCRETA y de alta confianza pide más
trabajo; ante fallo/duda/baja confianza se ACEPTA la respuesta (no se mete al
usuario en bucles de re-trabajo innecesario). Una negativa honesta cuenta como
atendida.
"""

import json

import pytest

from geo_copilot.core.composition_judge import (
    AnswerVerdict,
    judge_answer,
)


class _Resp:
    def __init__(self, content):
        self.content = content


class _LLM:
    def __init__(self, payload=None, raise_exc=False):
        self._payload, self._raise = payload, raise_exc

    async def chat(self, messages):
        if self._raise:
            raise RuntimeError("down")
        return _Resp(json.dumps(self._payload))


def test_needs_more_work_requires_gap_and_confidence():
    assert AnswerVerdict(False, 0.9, "x", "falta hospitales").needs_more_work is True
    assert AnswerVerdict(False, 0.5, "x").needs_more_work is False  # baja confianza
    assert AnswerVerdict(True, 1.0, "x").needs_more_work is False   # atendida


@pytest.mark.asyncio
async def test_no_llm_or_empty_answer_accepts():
    assert (await judge_answer(query="q", answer="a", tools_used=[], llm_client=None)).addressed
    llm = _LLM(payload={"addressed": False, "confidence": 0.9})
    assert (await judge_answer(query="q", answer="", tools_used=[], llm_client=llm)).addressed


@pytest.mark.asyncio
async def test_llm_failure_accepts():
    v = await judge_answer(query="q", answer="a", tools_used=["x"], llm_client=_LLM(raise_exc=True))
    assert v.addressed and not v.needs_more_work


@pytest.mark.asyncio
async def test_missing_field_accepts():
    v = await judge_answer(query="q", answer="a", tools_used=[], llm_client=_LLM(payload={"foo": 1}))
    assert v.addressed


@pytest.mark.asyncio
async def test_detects_concrete_gap():
    llm = _LLM(payload={
        "addressed": False, "confidence": 0.85,
        "reason": "solo trajo escuelas", "missing": "los hospitales",
    })
    v = await judge_answer(
        query="escuelas y hospitales", answer="hay 3 escuelas",
        tools_used=["query_database"], llm_client=llm)
    assert v.needs_more_work and v.missing == "los hospitales"


@pytest.mark.asyncio
async def test_honest_negative_is_addressed():
    llm = _LLM(payload={
        "addressed": True, "confidence": 0.9,
        "reason": "negativa honesta: no hay datos de esa capa", "missing": None,
    })
    v = await judge_answer(query="trae X", answer="No tengo datos de X en esta plataforma",
                           tools_used=[], llm_client=llm)
    assert v.addressed and not v.needs_more_work
