"""F4.1 + F4.3: catálogo de herramientas (a mano) y circuit-breaker del bucle ReAct.

VALIDACIÓN CRÍTICA: los esquemas tienen forma válida de function-calling y cubren
las capacidades reales (incl. la terminal ``answer``); el breaker corta el bucle
al tope de herramientas o de tokens, evitando bucles infinitos.
"""

import pytest

from geo_copilot.orchestrator.circuit_breaker import CircuitBreaker
from geo_copilot.orchestrator.tool_schemas import (
    ANSWER_TOOL,
    TOOL_NAMES,
    get_tool_schemas,
    is_known_tool,
    tool_schema,
)


# ---------------------------------------------------------------------------
# F4.1 — tool schemas
# ---------------------------------------------------------------------------
def test_schemas_have_valid_function_shape():
    for s in get_tool_schemas():
        assert s["type"] == "function"
        fn = s["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        # Todo campo 'required' existe en properties.
        for req in params.get("required", []):
            assert req in params["properties"]


def test_catalog_covers_core_capabilities_and_answer():
    expected = {
        "query_database", "spatial_operation", "apply_symbology",
        "search_external", "select_service", "load_external", ANSWER_TOOL,
    }
    assert expected <= TOOL_NAMES
    assert ANSWER_TOOL in TOOL_NAMES  # herramienta terminal presente


def test_is_known_tool_and_lookup():
    assert is_known_tool("query_database") is True
    assert is_known_tool("inventada") is False
    assert is_known_tool(None) is False
    assert tool_schema("answer")["function"]["name"] == "answer"
    assert tool_schema("nope") is None


def test_get_tool_schemas_returns_copy():
    a = get_tool_schemas()
    a[0]["function"]["name"] = "MUTADO"
    assert "MUTADO" not in TOOL_NAMES  # no mutó el catálogo global


def test_select_service_number_is_integer_min_1():
    sel = tool_schema("select_service")["function"]["parameters"]["properties"]["number"]
    assert sel["type"] == "integer" and sel.get("minimum") == 1


# ---------------------------------------------------------------------------
# F4.3 — circuit-breaker
# ---------------------------------------------------------------------------
def test_breaker_trips_on_tool_calls():
    cb = CircuitBreaker(max_tool_calls=3)
    for _ in range(2):
        cb.record_tool_call()
    assert cb.tripped is False and cb.remaining_calls() == 1
    cb.record_tool_call()
    assert cb.tripped is True
    assert "herramientas" in cb.reason


def test_breaker_trips_on_token_budget():
    cb = CircuitBreaker(max_tool_calls=100, max_tokens=1000)
    cb.record_tokens(600)
    assert cb.tripped is False
    cb.record_tokens(500)
    assert cb.tripped is True and "tokens" in cb.reason


def test_breaker_no_token_limit_when_zero():
    cb = CircuitBreaker(max_tool_calls=100, max_tokens=0)
    cb.record_tokens(10_000_000)
    assert cb.tripped is False  # 0 = sin límite de tokens
    assert cb.reason is None


def test_breaker_validates_args():
    with pytest.raises(ValueError):
        CircuitBreaker(max_tool_calls=0)
    with pytest.raises(ValueError):
        CircuitBreaker(max_tool_calls=5, max_tokens=-1)


def test_breaker_from_settings():
    class _S:
        react_max_tool_calls = 4
        react_token_budget = 2000

    cb = CircuitBreaker.from_settings(_S())
    assert cb.max_tool_calls == 4 and cb.max_tokens == 2000


def test_breaker_from_settings_defaults_when_missing():
    cb = CircuitBreaker.from_settings(object())
    assert cb.max_tool_calls == 12 and cb.max_tokens == 0
