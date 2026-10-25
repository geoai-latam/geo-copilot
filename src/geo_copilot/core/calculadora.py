"""Cuentas exactas para el agente (plan de fiabilidad F6).

El modelo sumaba de cabeza los números que devolvían las herramientas y a veces se equivocaba:
los cuatro rangos de precio de la UPRA en Cota suman 3.383,039 ha y la respuesta dijo 3.403,828.
Aquí la cuenta la hace el código, en decimal exacto; el modelo decide qué sumar.

Solo aritmética: números, + - * / // %, potencias pequeñas, paréntesis y `round`, `abs`, `min`,
`max`, `sum`. Nada de nombres, atributos ni llamadas arbitrarias (se evalúa el AST, no `eval`).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Decimal, DivisionByZero, InvalidOperation, localcontext

MAX_EXPRESION = 2000
MAX_EXPONENTE = 12

_BINARIOS: dict[type[ast.operator], Callable[[Decimal, Decimal], Decimal]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}


class ErrorDeCalculo(ValueError):
    """La expresión no es aritmética válida o no se puede calcular."""


def calcular(expresion: str) -> Decimal:
    """El valor exacto de una expresión aritmética («984.143 + 562.444 + 422.521 + 1413.931»)."""
    if not expresion.strip() or len(expresion) > MAX_EXPRESION:
        raise ErrorDeCalculo(f"expresión vacía o de más de {MAX_EXPRESION} caracteres")
    try:
        arbol = ast.parse(expresion.strip(), mode="eval")
    except SyntaxError as exc:
        raise ErrorDeCalculo(
            "no es una expresión aritmética: escribe los números con punto decimal y sin separador "
            "de miles (1413.931, no 1.413,931)"
        ) from exc
    with localcontext() as ctx:
        ctx.prec = 40
        try:
            return _valor(arbol.body)
        except (DivisionByZero, InvalidOperation, ZeroDivisionError, RecursionError) as exc:
            raise ErrorDeCalculo(f"no se puede calcular: {type(exc).__name__}") from exc


def _valor(nodo: ast.AST) -> Decimal:
    if isinstance(nodo, ast.Constant) and type(nodo.value) in (int, float):
        # el texto del número tal como se escribió, no su float (0.1 + 0.2 = 0.3 exacto)
        return Decimal(repr(nodo.value))
    if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, ast.USub | ast.UAdd):
        v = _valor(nodo.operand)
        return -v if isinstance(nodo.op, ast.USub) else v
    if isinstance(nodo, ast.BinOp):
        a, b = _valor(nodo.left), _valor(nodo.right)
        if isinstance(nodo.op, ast.Pow):
            if b != b.to_integral_value() or abs(b) > MAX_EXPONENTE:
                raise ErrorDeCalculo(f"solo potencias enteras de hasta {MAX_EXPONENTE}")
            return a ** int(b)
        if type(nodo.op) in _BINARIOS:
            return _BINARIOS[type(nodo.op)](a, b)
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and not nodo.keywords:
        args = [_valor(x) for x in nodo.args]
        nombre = nodo.func.id
        if nombre == "round" and len(args) in (1, 2):
            digitos = int(args[1]) if len(args) == 2 else 0
            return args[0].quantize(Decimal(1).scaleb(-digitos), rounding=ROUND_HALF_EVEN)
        if nombre in ("abs",) and len(args) == 1:
            return abs(args[0])
        if nombre in ("min", "max", "sum") and args:
            return min(args) if nombre == "min" else max(args) if nombre == "max" else sum(args, Decimal(0))
    if isinstance(nodo, ast.List | ast.Tuple):
        raise ErrorDeCalculo(
            "la coma no es decimal aquí: escribe 1413.931 (punto decimal, sin separador de miles); "
            "para varios números, sum(a, b, c)"
        )
    raise ErrorDeCalculo(f"no permitido en una cuenta: {type(nodo).__name__}")


def en_formato_colombiano(valor: Decimal) -> str:
    """3383.039 → «3.383,039» (punto de miles, coma decimal)."""
    texto = format(valor.normalize(), "f")
    signo, texto = ("-", texto[1:]) if texto.startswith("-") else ("", texto)
    entero, _, decimales = texto.partition(".")
    grupos = f"{int(entero):,}".replace(",", ".")
    return signo + grupos + (f",{decimales}" if decimales else "")
