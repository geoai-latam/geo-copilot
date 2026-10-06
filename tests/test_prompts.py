"""Prompts como archivos (F1 del plan de calidad): cada uso tiene su .md y cada plantilla se rellena.

Un nombre mal escrito en ``cargar_prompt("…")`` o un hueco que el código no rellena fallaría en
ejecución, a mitad de una consulta; aquí falla antes.
"""

from __future__ import annotations

import ast
import string
from pathlib import Path

import pytest

from geo_copilot.prompts import cargar_prompt

SRC = Path(__file__).resolve().parents[1] / "src" / "geo_copilot"
CARPETA = SRC / "prompts"


def _usos() -> list[tuple[str, str, ast.Call]]:
    """(archivo, nombre, nodo de la llamada) de cada cargar_prompt("nombre") en src."""
    usos = []
    for p in SRC.rglob("*.py"):
        arbol = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(arbol):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "cargar_prompt"
                    and n.args and isinstance(n.args[0], ast.Constant)):
                usos.append((p.relative_to(SRC).as_posix(), n.args[0].value, n))
    return usos


def _formats() -> dict[str, set[str]]:
    """nombre → argumentos de ``cargar_prompt(nombre).format(...)`` en el código."""
    salida: dict[str, set[str]] = {}
    for p in SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "format"
                    and isinstance(n.func.value, ast.Call) and isinstance(n.func.value.func, ast.Name)
                    and n.func.value.func.id == "cargar_prompt"):
                salida[n.func.value.args[0].value] = {k.arg for k in n.keywords}
    return salida


def _huecos(texto: str) -> set[str]:
    return {campo for _, campo, _, _ in string.Formatter().parse(texto) if campo}


def test_cada_uso_tiene_su_archivo_y_cada_archivo_se_usa():
    usados = {nombre for _, nombre, _ in _usos()}
    archivos = {p.stem for p in CARPETA.glob("*.md")}
    assert usados, "no se encontró ningún cargar_prompt en src"
    assert not usados - archivos, f"prompts sin archivo: {usados - archivos}"
    assert not archivos - usados, f"archivos de prompt que nadie usa: {archivos - usados}"


@pytest.mark.parametrize("nombre", sorted(p.stem for p in CARPETA.glob("*.md")))
def test_el_prompt_carga_sin_crlf_ni_salto_final(nombre):
    texto = cargar_prompt(nombre)
    assert texto.strip()
    assert "\r" not in texto


@pytest.mark.parametrize(("nombre", "argumentos"), sorted(_formats().items()))
def test_lo_que_el_codigo_rellena_son_exactamente_los_huecos_de_la_plantilla(nombre, argumentos):
    huecos = _huecos(cargar_prompt(nombre))
    assert huecos == argumentos, f"{nombre}: huecos {huecos} ≠ argumentos {argumentos}"
    cargar_prompt(nombre).format(**dict.fromkeys(argumentos, "x"))  # llaves literales bien dobladas
