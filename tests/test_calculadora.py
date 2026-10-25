"""Plan de fiabilidad F6: las cuentas las hace el código, exactas; el modelo decide qué sumar."""

import json
from decimal import Decimal

import pytest

from geo_copilot.core.calculadora import ErrorDeCalculo, calcular, en_formato_colombiano


@pytest.mark.parametrize(("expresion", "valor"), [
    ("984.143 + 562.444 + 422.521 + 1413.931", "3383.039"),  # los rangos de la UPRA en Cota
    ("0.1 + 0.2", "0.3"),  # decimal exacto, no float
    ("round(31 / 74 * 100, 1)", "41.9"),
    ("sum(1, 2, 3) - max(1, 2) + min(4, 5) + abs(-1)", "9"),
    ("-(2 + 3) * 2 ** 3 // 3 % 7", "-6"),
])
def test_cuentas_exactas(expresion, valor):
    assert calcular(expresion) == Decimal(valor)


@pytest.mark.parametrize("expresion", [
    "", "__import__('os')", "(1).real", "x + 1", "open('a')", "2 ** 100", "1 / 0",
    "1.413,931 + 2", "[1, 2]", "lambda: 1", "1" * 3000, "True + 1",
])
def test_lo_que_no_es_una_cuenta_se_rechaza(expresion):
    with pytest.raises(ErrorDeCalculo):
        calcular(expresion)


def test_formato_colombiano():
    assert en_formato_colombiano(Decimal("3383.039")) == "3.383,039"
    assert en_formato_colombiano(Decimal("-1234567")) == "-1.234.567"
    assert en_formato_colombiano(Decimal("0.5")) == "0,5"


@pytest.mark.asyncio
async def test_la_herramienta_devuelve_el_resultado_y_su_forma_colombiana():
    from geo_copilot.orchestrator.capabilities_core import ensure_core
    from geo_copilot.platform.capabilities import registry

    ensure_core()
    cap = registry().get("calcular")
    assert cap is not None and "expresion" in cap.parameters["required"]
    out = await cap.executor(None, {}, {"expresion": "984.143 + 562.444"})
    assert out.success and json.loads(out.observation)["resultado_en_formato_colombiano"] == "1.546,587"
    mala = await cap.executor(None, {}, {"expresion": "import os"})
    assert not mala.success and "No se pudo calcular" in mala.observation
