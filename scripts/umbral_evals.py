"""Umbral de las evaluaciones con LLM real (F7, S7.4): ¿la versión puede salir?

Lee el JUnit XML de `pytest -m llm` y falla (código 1) si la tasa de aprobados queda bajo el umbral.
Los tests de juicio son TASAS (el LLM no es determinista): un fallo suelto es varianza; por
debajo del umbral es una regresión que bloquea la publicación.

Los saltos (skip/xfail) no entran en la tasa: el test decide saltar cuando no aplica (p. ej. el
LLM eligió otro camino legítimo). Pero se listan con su motivo, para que un salto que crece se
vea. Un error de RECOGIDA (un archivo que no importa) bloquea siempre: sus tests no corrieron y
la tasa del resto no dice nada de ellos.

    python scripts/umbral_evals.py resultados.xml --umbral 0.95
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import Counter


def _fraccion(texto: str) -> float:
    """El umbral es una fracción 0-1: `95` (en vez de 0.95) bloquearía toda versión sin decir por qué."""
    valor = float(texto)
    if not 0.0 <= valor <= 1.0:
        raise argparse.ArgumentTypeError(f"el umbral es una fracción entre 0 y 1, no {texto}")
    return valor


def evaluar(raiz: ET.Element, umbral: float) -> tuple[bool, list[str]]:
    """(¿pasa?, líneas del informe) a partir de la raíz del JUnit XML."""
    total, fallos = 0, []
    saltos: Counter[str] = Counter()
    sin_recoger: list[str] = []
    por_archivo: Counter[str] = Counter()
    fallos_por_archivo: Counter[str] = Counter()
    for caso in raiz.iter("testcase"):
        partes = (caso.get("classname") or "?").split(".")
        archivo = next((p for p in partes if p.startswith("test_")), partes[-1])
        error = caso.find("error")
        # F7 (auditoría): pytest marca un módulo que no importa como testcase sin classname y
        # `<error message="collection failure">`; contado como UN fallo, la tasa del resto pasaba.
        if error is not None and not caso.get("classname") and error.get("message") == "collection failure":
            sin_recoger.append(caso.get("name") or "?")
            continue
        # fallo/error antes que salto: un error de teardown tras un skip sigue siendo un error
        if caso.find("failure") is not None or error is not None:
            total += 1
            por_archivo[archivo] += 1
            fallos.append(f"{caso.get('classname')}::{caso.get('name')}")
            fallos_por_archivo[archivo] += 1
            continue
        salto = caso.find("skipped")
        if salto is not None:
            tipo = "xfail" if salto.get("type") == "pytest.xfail" else "skip"
            saltos[f"{tipo}: {(salto.get('message') or 'sin motivo')[:160]}"] += 1
            continue
        total += 1
        por_archivo[archivo] += 1

    lineas: list[str] = []
    if sin_recoger:
        lineas.append(f"::error::{len(sin_recoger)} archivo(s) de evaluación no se pudieron recoger: "
                      f"{', '.join(sin_recoger)}. Sus tests no corrieron; la versión no se publica")
        return False, lineas
    if total == 0:
        lineas.append("::error::no se ejecutó ningún test de juicio (¿faltan las credenciales del LLM?)")
        lineas += [f"  SALTA ×{n} {motivo}" for motivo, n in sorted(saltos.items())]
        return False, lineas
    tasa = (total - len(fallos)) / total
    lineas.append(f"Evaluaciones con LLM real: {total - len(fallos)}/{total} = {tasa:.1%} "
                  f"(umbral {umbral:.0%}); {sum(saltos.values())} saltados")
    for archivo, n in sorted(por_archivo.items()):
        if fallos_por_archivo[archivo]:
            lineas.append(f"  {archivo}: {n - fallos_por_archivo[archivo]}/{n}")
    lineas += [f"  FALLA {f}" for f in fallos]
    lineas += [f"  SALTA ×{n} {motivo}" for motivo, n in sorted(saltos.items())]
    if tasa < umbral:
        lineas.append(f"::error::por debajo del umbral ({tasa:.1%} < {umbral:.0%}): la versión no se publica")
        return False, lineas
    return True, lineas


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("junit")
    p.add_argument("--umbral", type=_fraccion, default=0.95)
    args = p.parse_args(argv)

    # F7 (auditoría): evals.yml corre pytest con `|| true`; si pytest no llegó a escribir el XML,
    # que se diga eso y no una traza de ElementTree.
    try:
        raiz = ET.parse(args.junit).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"::error::no se pudo leer el resultado de pytest ({args.junit}): {exc}")
        return 1
    aprobado, lineas = evaluar(raiz, args.umbral)
    for linea in lineas:
        print(linea)
    return 0 if aprobado else 1


if __name__ == "__main__":
    sys.exit(main())
