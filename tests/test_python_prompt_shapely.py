"""Regresión C3d-3: el prompt no debe anunciar símbolos no importados.

El prompt listaba shapely.ops (unary_union, cascaded_union) pero el
sandbox solo importa unary_union, y cascaded_union fue removido en
Shapely 2.x → NameError si el LLM lo usa.
"""

from geo_copilot.agents.python_agent.prompts import SYSTEM_PROMPT


def test_prompt_does_not_advertise_cascaded_union():
    assert "cascaded_union" not in SYSTEM_PROMPT


def test_prompt_still_lists_unary_union():
    assert "unary_union" in SYSTEM_PROMPT


def test_sandbox_imports_match_prompt():
    """El símbolo que el prompt anuncia debe estar importado en el sandbox."""
    # F4: el andamiaje del sandbox vive en `python_agent.ejecucion` (antes, en el fuente de agent.py).
    from geo_copilot.agents.python_agent.ejecucion import _ANDAMIO_FIN, _ANDAMIO_INICIO

    andamio = _ANDAMIO_INICIO + _ANDAMIO_FIN
    # unary_union se importa; cascaded_union NO debe importarse ni anunciarse.
    assert "from shapely.ops import unary_union" in andamio
    assert "cascaded_union" not in andamio
