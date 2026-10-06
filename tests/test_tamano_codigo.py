"""Trinquete de TAMAÑO (F1 del plan de calidad, docs/PLAN_CALIDAD_ORQUESTADOR_2026-10-04.md).

Clean Code: archivos de ~200 líneas y como máximo 500; funciones que hacen una sola cosa. El repo
tenía 38 archivos de más de 500 líneas y 170 funciones de más de 50 al empezar. No se parten de
golpe (cada refactor va en su PR, con la batería de verdades antes y después); lo que se impide es
que EMPEORE:

- un archivo nuevo de más de ``MAX_LINEAS_ARCHIVO`` o una función nueva de más de
  ``MAX_LINEAS_FUNCION`` falla;
- los que ya los pasaban están en ``tamano_codigo_base.json`` con su tamaño: pueden bajar, no subir;
- cuando uno baja del límite, sale de la base (como RUF100 con un noqa sin uso).

Tras partir un archivo o una función:  ``python tests/test_tamano_codigo.py --actualizar``
(solo BAJA la base: si algo creció o es nuevo, se niega y lo dice).

El límite es una ALARMA, no la meta (Ousterhout): se parte por responsabilidad, no por líneas.
El frontend tiene su trinquete en ESLint (max-lines, complexity) con el mismo mecanismo.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BASE = Path(__file__).with_name("tamano_codigo_base.json")
CARPETAS = ("src", "services", "packages", "scripts")
MAX_LINEAS_ARCHIVO = 500
MAX_LINEAS_FUNCION = 60


def _archivos() -> list[Path]:
    salida = []
    for carpeta in CARPETAS:
        for p in (RAIZ / carpeta).rglob("*.py"):
            partes = set(p.relative_to(RAIZ).parts)
            if partes & {"build", "__pycache__", ".venv", "node_modules"}:
                continue
            salida.append(p)
    return sorted(salida)


def _funciones_largas(nodo: ast.AST, rel: str, prefijo: str, salida: dict[str, int]) -> None:
    for hijo in ast.iter_child_nodes(nodo):
        if not isinstance(hijo, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            _funciones_largas(hijo, rel, prefijo, salida)
            continue
        nombre = f"{prefijo}{hijo.name}"
        if not isinstance(hijo, ast.ClassDef):
            largo = (hijo.end_lineno or hijo.lineno) - hijo.lineno + 1
            if largo > MAX_LINEAS_FUNCION:
                clave, n = f"{rel}::{nombre}", 2
                while clave in salida:  # mismo nombre dos veces (p. ej. en las ramas de un if)
                    clave, n = f"{rel}::{nombre}#{n}", n + 1
                salida[clave] = largo
        _funciones_largas(hijo, rel, f"{nombre}.", salida)


def medir() -> dict[str, dict[str, int]]:
    """{"archivos": {ruta: líneas}, "funciones": {ruta::Clase.funcion: líneas}} de lo que pasa el límite."""
    archivos: dict[str, int] = {}
    funciones: dict[str, int] = {}
    for p in _archivos():
        rel = p.relative_to(RAIZ).as_posix()
        texto = p.read_text(encoding="utf-8")
        lineas = len(texto.splitlines())
        if lineas > MAX_LINEAS_ARCHIVO:
            archivos[rel] = lineas
        _funciones_largas(ast.parse(texto), rel, "", funciones)
    return {"archivos": archivos, "funciones": funciones}


def comparar(actual: dict, base: dict) -> list[str]:
    """Problemas del trinquete (vacío = pasa)."""
    problemas = []
    for tipo, limite in (("archivos", MAX_LINEAS_ARCHIVO), ("funciones", MAX_LINEAS_FUNCION)):
        for clave, lineas in sorted(actual[tipo].items()):
            previo = base[tipo].get(clave)
            if previo is None:
                problemas.append(f"NUEVO {'archivo' if tipo == 'archivos' else 'función'} de {lineas} líneas (límite {limite}): {clave}. "
                                 "Pártelo por responsabilidad.")
            elif lineas > previo:
                problemas.append(f"CRECIÓ {clave}: {previo} → {lineas} líneas (ya pasaba el límite de "
                                 f"{limite}; puede bajar, no subir).")
        for clave in sorted(set(base[tipo]) - set(actual[tipo])):
            problemas.append(f"YA NO PASA el límite (o cambió de nombre): {clave}. Sácalo de la base: "
                             "python tests/test_tamano_codigo.py --actualizar")
    return problemas


def test_el_codigo_no_crece_por_encima_de_los_limites():
    base = json.loads(BASE.read_text(encoding="utf-8"))
    problemas = comparar(medir(), base)
    assert not problemas, "\n".join(problemas)


def test_comparar_detecta_nuevo_crecido_y_sobrante():
    base = {"archivos": {"a.py": 600, "b.py": 700}, "funciones": {}}
    actual = {"archivos": {"a.py": 650, "c.py": 520}, "funciones": {}}
    problemas = comparar(actual, base)
    assert any(p.startswith("CRECIÓ a.py") for p in problemas)
    assert any(p.startswith("NUEVO archivo") and "c.py" in p for p in problemas)
    assert any(p.startswith("YA NO PASA") and "b.py" in p for p in problemas)
    assert not comparar({"archivos": {"a.py": 580}, "funciones": {}},
                        {"archivos": {"a.py": 600}, "funciones": {}})  # bajar está permitido


def _actualizar() -> int:
    actual = medir()
    base = json.loads(BASE.read_text(encoding="utf-8")) if BASE.exists() else None
    if base is not None:
        malos = [p for p in comparar(actual, base) if not p.startswith("YA NO PASA")]
        if malos:
            print("No se actualiza: la base solo BAJA.\n" + "\n".join(malos))
            return 1
    BASE.write_text(json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"base: {len(actual['archivos'])} archivos > {MAX_LINEAS_ARCHIVO} líneas, "
          f"{len(actual['funciones'])} funciones > {MAX_LINEAS_FUNCION}")
    return 0


if __name__ == "__main__":
    if "--actualizar" in sys.argv:
        sys.exit(_actualizar())
    print(__doc__)
