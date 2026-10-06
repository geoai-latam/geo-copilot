"""F3.3: correctores conscientes de la clasificación del error (analyze_error).

VALIDACIÓN CRÍTICA: un error que NO se arregla reescribiendo (permisos, infra,
memoria) se descarta SIN gastar una llamada al LLM y SIN reintentar a ciegas;
los errores corregibles reciben la clasificación dirigida en el prompt.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.gis_agent.sql_corrector import SQLCorrector
from geo_copilot.agents.python_agent.code_corrector import CodeCorrector


def _llm():
    m = MagicMock()
    m.chat = AsyncMock(return_value=MagicMock(content="SELECT 1 FROM t LIMIT 1000"))
    return m


# ---------------------------------------------------------------------------
# SQL: no-corregibles se cortan sin LLM
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sql_permission_error_skips_llm():
    llm = _llm()
    corr = SQLCorrector(llm_client=llm)
    result = await corr.correct_sql(
        sql="SELECT * FROM secret", error="ERROR: permission denied for table secret",
        schema="t(a)", query="q",
    )
    assert result is None
    llm.chat.assert_not_awaited()  # no se gastó una llamada en algo no corregible


@pytest.mark.asyncio
async def test_sql_infrastructure_error_skips_llm():
    llm = _llm()
    corr = SQLCorrector(llm_client=llm)
    result = await corr.correct_sql(
        sql="SELECT 1", error="could not connect to server: Connection refused",
        query="q",
    )
    assert result is None
    llm.chat.assert_not_awaited()


def test_sql_analyze_classifies_infrastructure():
    corr = SQLCorrector()
    a = corr.analyze_error("server closed the connection unexpectedly")
    assert a["type"] == "infrastructure_error" and a["correctable"] is False


@pytest.mark.asyncio
async def test_sql_correctable_error_injects_classification():
    llm = _llm()
    corr = SQLCorrector(llm_client=llm)
    await corr.correct_sql(
        sql="SELECT nombre FROM t", error='column "nombre" does not exist',
        schema="t(NOMBRE)", query="trae nombres",
    )
    llm.chat.assert_awaited_once()
    prompt = llm.chat.call_args[0][0][0].content
    assert "CLASIFICACIÓN DEL ERROR" in prompt
    assert "column_not_found" in prompt  # la clasificación dirigida llegó al prompt


# ---------------------------------------------------------------------------
# Python: MemoryError no-corregible se corta sin LLM
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_code_memory_error_skips_llm():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content="result = gdf"))
    corr = CodeCorrector(llm_client=llm)
    result = await corr.correct_code(
        code="result = gdf.buffer(1)", error="MemoryError: out of memory",
        columns=["geometry"], feature_count=999999,
    )
    assert result is None
    llm.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_code_correctable_error_injects_classification():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content="result = gdf.copy()"))
    corr = CodeCorrector(llm_client=llm)
    await corr.correct_code(
        code="result = gdf['x']", error="KeyError: 'x'",
        columns=["a", "b"], feature_count=3,
    )
    llm.chat.assert_awaited_once()
    prompt = llm.chat.call_args[0][0][0].content
    assert "CLASIFICACIÓN DEL ERROR" in prompt
    assert "key_error" in prompt


# ---------------------------------------------------------------------------
# LLM-pilar (Fix #1): el residuo AMBIGUO NO se vetea en código — lo decide el LLM
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sql_unknown_error_defers_correctability_to_llm():
    # Un error NO catalogado como infra/recursos → no se aborta por keywords;
    # el LLM corrector es quien decide (aquí responde NO_CORRECTION_POSSIBLE).
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content="NO_CORRECTION_POSSIBLE"))
    corr = SQLCorrector(llm_client=llm)
    result = await corr.correct_sql(
        sql="SELECT 1", error="algo raro no catalogado por keywords",
        schema="t(a)", query="q")
    assert result is None            # el LLM decidió que no hay corrección
    llm.chat.assert_awaited_once()   # NO se vetó en código: el LLM fue consultado


@pytest.mark.asyncio
async def test_code_unknown_error_defers_correctability_to_llm():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content="NO_CORRECTION_POSSIBLE"))
    corr = CodeCorrector(llm_client=llm)
    result = await corr.correct_code(
        code="result = f()", error="RuntimeError: algo inesperado",
        columns=["a"], feature_count=2)
    assert result is None
    llm.chat.assert_awaited_once()
