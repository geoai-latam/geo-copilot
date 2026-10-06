"""F2.3: juez semántico de resultados vacíos (0-por-bug vs 0-por-realidad).

VALIDACIÓN CRÍTICA: el sesgo es conservador — un 0 sin evidencia de defecto se
reporta como REAL (no se reintenta), porque relajar una consulta correcta
fabricaría datos que no responden la pregunta. Solo un defecto NOMBRADO y con
confianza dispara corrección.
"""

import json

import pytest

from geo_copilot.orchestrator.result_judge import (
    DEFAULT_MIN_CONFIDENCE,
    EmptyResultVerdict,
    judge_empty_result,
)


class _Resp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """LLM falso que devuelve un JSON fijo (o lanza si se le pide)."""

    def __init__(self, payload=None, raise_exc=False):
        self._payload = payload
        self._raise = raise_exc
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if self._raise:
            raise RuntimeError("LLM caído")
        return _Resp(json.dumps(self._payload))


# ---------------------------------------------------------------------------
# is_bug: gating por confianza
# ---------------------------------------------------------------------------
def test_is_bug_requires_verdict_and_confidence():
    assert EmptyResultVerdict("likely_bug", 0.9, "x").is_bug is True
    assert EmptyResultVerdict("likely_bug", DEFAULT_MIN_CONFIDENCE - 0.01, "x").is_bug is False
    assert EmptyResultVerdict("plausibly_real", 1.0, "x").is_bug is False


# ---------------------------------------------------------------------------
# Degradación honesta: sin LLM o LLM caído → real (NO inventa bug)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_llm_defaults_to_real():
    v = await judge_empty_result(query="q", sql="SELECT 1", schema_info="", llm_client=None)
    assert v.verdict == "plausibly_real" and v.is_bug is False


@pytest.mark.asyncio
async def test_llm_failure_defaults_to_real():
    llm = _FakeLLM(raise_exc=True)
    v = await judge_empty_result(query="q", sql="SELECT 1", schema_info="s", llm_client=llm)
    assert v.verdict == "plausibly_real" and v.is_bug is False


@pytest.mark.asyncio
async def test_garbage_json_defaults_to_real():
    llm = _FakeLLM(payload=None)  # json.dumps(None) = "null" → parse vacío
    v = await judge_empty_result(query="q", sql="SELECT 1", schema_info="s", llm_client=llm)
    assert v.verdict == "plausibly_real"


@pytest.mark.asyncio
async def test_invalid_verdict_value_defaults_to_real():
    llm = _FakeLLM(payload={"verdict": "maybe", "confidence": 0.9})
    v = await judge_empty_result(query="q", sql="x", schema_info="s", llm_client=llm)
    assert v.verdict == "plausibly_real"


# ---------------------------------------------------------------------------
# Camino feliz: el juez detecta un bug concreto
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_detects_case_mismatch_bug():
    llm = _FakeLLM(payload={
        "verdict": "likely_bug",
        "confidence": 0.85,
        "reason": "Igualdad sobre texto que puede diferir en mayúsculas",
        "suggested_fix": "Usar ILIKE en vez de =",
    })
    v = await judge_empty_result(
        query="cuántas escuelas hay",
        sql="SELECT * FROM poi WHERE tipo = 'ESCUELA'",
        schema_info="poi(tipo text, geom)",
        llm_client=llm,
    )
    assert v.is_bug is True
    assert v.suggested_fix and "ILIKE" in v.suggested_fix


@pytest.mark.asyncio
async def test_real_zero_not_retried():
    llm = _FakeLLM(payload={
        "verdict": "plausibly_real",
        "confidence": 0.9,
        "reason": "El SQL es fiel a la pregunta; 0 es posible",
        "suggested_fix": None,
    })
    v = await judge_empty_result(
        query="hospitales dentro de 10m de este punto exacto",
        sql="SELECT * FROM hospitales WHERE ST_DWithin(geom::geography, p, 10)",
        schema_info="hospitales(geom)",
        llm_client=llm,
    )
    assert v.is_bug is False


@pytest.mark.asyncio
async def test_low_confidence_bug_treated_as_real():
    # El LLM dice bug pero con baja confianza → NO accionable (sesgo conservador).
    llm = _FakeLLM(payload={
        "verdict": "likely_bug",
        "confidence": 0.3,
        "reason": "Quizá el filtro es estrecho",
        "suggested_fix": "Ampliar rango",
    })
    v = await judge_empty_result(query="q", sql="x", schema_info="s", llm_client=llm)
    assert v.verdict == "likely_bug" and v.is_bug is False


@pytest.mark.asyncio
async def test_suggested_fix_null_string_normalized():
    llm = _FakeLLM(payload={
        "verdict": "plausibly_real", "confidence": 0.8,
        "reason": "ok", "suggested_fix": "null",
    })
    v = await judge_empty_result(query="q", sql="x", schema_info="s", llm_client=llm)
    assert v.suggested_fix is None


# ===========================================================================
# Integración con el nodo gis_agent: el veredicto del juez decide retry vs
# reporte honesto (wiring real, no solo la función del juez).
# ===========================================================================
from unittest.mock import AsyncMock, MagicMock, patch

from geo_copilot.orchestrator.nodes import gis_agent as gis_node

_EMPTY_GJ = {"type": "FeatureCollection", "features": []}


def _mock_graph(execute_side_effect):
    graph = MagicMock()
    graph.db_pool = object()  # truthy → no corta por "sin BD"
    graph.hitl_manager = None  # desactiva HITL (skip aprobación)
    graph.llm = MagicMock()
    graph.gis_agent.get_db_schema = AsyncMock(return_value="poi(tipo text, geom)")
    graph.gis_agent.generate_sql_from_query = AsyncMock(
        return_value="SELECT * FROM poi WHERE tipo = 'ESCUELA'"
    )
    graph.gis_agent._execute_sql = AsyncMock(side_effect=execute_side_effect)
    return graph


def _state():
    return {
        "query": "cuántas escuelas hay",
        "session_id": "",
        "autonomous_mode": True,
        "max_retries": 1,  # → max_attempts = 2
    }


@pytest.mark.asyncio
async def test_node_real_zero_reports_honestly_without_retry():
    graph = _mock_graph(execute_side_effect=[([], _EMPTY_GJ)])
    real = EmptyResultVerdict("plausibly_real", 0.9, "0 es posible aquí")

    with patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")), \
         patch(
             "geo_copilot.orchestrator.result_judge.judge_empty_result",
             AsyncMock(return_value=real),
         ):
        result = await gis_node.run(graph, _state())

    # Éxito sin reintento; SQL ejecutado una sola vez.
    assert graph.gis_agent._execute_sql.await_count == 1
    assert result.get("raw_data") == []
    assert result["empty_result_verdict"]["verdict"] == "plausibly_real"
    assert "real" in result["messages"][0]["content"].lower()


@pytest.mark.asyncio
async def test_node_bug_zero_triggers_correction_and_retry():
    # attempt 0 → 0 filas (bug) ; attempt 1 (corregido) → 1 fila.
    graph = _mock_graph(execute_side_effect=[
        ([], _EMPTY_GJ),
        ([{"id": 1}], {"type": "FeatureCollection", "features": [{"id": 1}]}),
    ])
    bug = EmptyResultVerdict("likely_bug", 0.9, "Igualdad sensible a mayúsculas", "Usar ILIKE")

    corrector = MagicMock()
    corrector.correct_sql = AsyncMock(
        return_value="SELECT * FROM poi WHERE tipo ILIKE 'escuela'"
    )

    judge_mock = AsyncMock(return_value=bug)
    with patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")), \
         patch.object(gis_node, "SQLCorrector", return_value=corrector), \
         patch(
             "geo_copilot.orchestrator.result_judge.judge_empty_result",
             judge_mock,
         ):
        result = await gis_node.run(graph, _state())

    # El juez disparó corrección: 2 ejecuciones, corrector invocado, resultado final no vacío.
    assert graph.gis_agent._execute_sql.await_count == 2
    corrector.correct_sql.assert_awaited()
    assert result.get("raw_data") == [{"id": 1}]
    assert result.get("retry_count") == 1


@pytest.mark.asyncio
async def test_node_repeated_correction_stops_loop():
    # El corrector regenera el MISMO SQL ya intentado → el guard corta el loop
    # (no se reintenta indefinidamente con mutaciones inútiles).
    same_sql = "SELECT * FROM poi WHERE tipo = 'ESCUELA'"
    graph = _mock_graph(execute_side_effect=[([], _EMPTY_GJ), ([], _EMPTY_GJ)])
    graph.gis_agent.generate_sql_from_query = AsyncMock(return_value=same_sql)
    bug = EmptyResultVerdict("likely_bug", 0.9, "case", "ILIKE")

    corrector = MagicMock()
    corrector.correct_sql = AsyncMock(return_value=same_sql)  # idéntico → ya visto

    with patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")), \
         patch.object(gis_node, "SQLCorrector", return_value=corrector), \
         patch(
             "geo_copilot.orchestrator.result_judge.judge_empty_result",
             AsyncMock(return_value=bug),
         ):
        result = await gis_node.run(graph, {**_state(), "max_retries": 3})

    # Una sola ejecución: el guard impidió reintentar con SQL ya probado.
    assert graph.gis_agent._execute_sql.await_count == 1
    assert "error" in result and "posible defecto" in result["error"]


# ===========================================================================
# Propagación del veredicto: GraphState channel + consumo en responder/insights
# (regresión: sin el canal declarado, LangGraph descarta el veredicto).
# ===========================================================================
def test_graphstate_declares_empty_result_verdict_channel():
    from geo_copilot.orchestrator.graph import GraphState

    assert "empty_result_verdict" in GraphState.__annotations__


def test_judge_prompt_has_false_positive_carveouts():
    from geo_copilot.orchestrator.result_judge import _JUDGE_PROMPT

    # Las salvaguardas contra falsos positivos deben permanecer en el prompt.
    assert "AGREGADOS" in _JUDGE_PROMPT
    assert "ANTI-JOINS" in _JUDGE_PROMPT or "NOT EXISTS" in _JUDGE_PROMPT
    assert "::geography" in _JUDGE_PROMPT
    assert "FILTROS EXACTOS" in _JUDGE_PROMPT


def test_default_min_confidence_conservative():
    from geo_copilot.orchestrator.result_judge import DEFAULT_MIN_CONFIDENCE

    assert DEFAULT_MIN_CONFIDENCE >= 0.7


@pytest.mark.asyncio
async def test_responder_propagates_verdict_to_final_data():
    from geo_copilot.orchestrator.nodes import responder

    graph = MagicMock()
    graph.insights_agent.infer_visualization_type = AsyncMock(return_value={"type": "table"})
    verdict = {"verdict": "plausibly_real", "confidence": 0.9, "reason": "0 real"}
    state = {
        "query": "q", "raw_data": [], "geojson": None, "sql": "SELECT 1",
        "empty_result_verdict": verdict,
    }
    result = await responder.run(graph, state)
    assert result["final_data"]["empty_result_verdict"] == verdict


@pytest.mark.asyncio
async def test_insights_honest_note_reaches_prompt_for_bug_zero():
    from geo_copilot.orchestrator.nodes import insights

    captured = {}

    class _Resp:
        content = "ok"

    graph = MagicMock()

    async def _chat(messages):
        captured["prompt"] = messages[0].content
        return _Resp()

    graph.llm.chat = _chat
    state = {
        "query": "cuántas escuelas hay", "intent": "query_data",
        "raw_data": [], "geojson": {"type": "FeatureCollection", "features": []},
        "empty_result_verdict": {
            "verdict": "likely_bug", "confidence": 0.9,
            "reason": "igualdad sensible a mayúsculas",
        },
    }
    await insights.run(graph, state)
    # El narrador recibe la advertencia honesta (no afirmar "no existen datos").
    assert "PROBLEMA" in captured["prompt"] or "posible" in captured["prompt"].lower()
    assert "mayúsculas" in captured["prompt"]


@pytest.mark.asyncio
async def test_insights_honest_note_real_zero():
    from geo_copilot.orchestrator.nodes import insights

    captured = {}

    class _Resp:
        content = "ok"

    graph = MagicMock()

    async def _chat(messages):
        captured["prompt"] = messages[0].content
        return _Resp()

    graph.llm.chat = _chat
    state = {
        "query": "hospitales aquí", "intent": "query_data",
        "raw_data": [], "geojson": {"type": "FeatureCollection", "features": []},
        "empty_result_verdict": {
            "verdict": "plausibly_real", "confidence": 0.9,
            "reason": "el SQL es fiel a la pregunta",
        },
    }
    await insights.run(graph, state)
    assert "REAL" in captured["prompt"]
