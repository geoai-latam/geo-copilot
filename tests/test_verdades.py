"""Batería de verdades (F0 del plan de calidad): el YAML es válido y el evaluador de cifras acierta.

Deterministas, sin LLM. La corrida real (grafo de la app + MCP) se hace con
``runner.py --app --tasks verdades.yaml`` dentro del contenedor (ver la cabecera del YAML).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_BENCH = Path(__file__).parent / "agentic_bench"
sys.path.insert(0, str(_BENCH))

from evaluator import cifras, evaluate

from tests.test_agentic_bench import _KNOWN_EXPECTED_KEYS

_VERDADES = yaml.safe_load((_BENCH / "verdades.yaml").read_text(encoding="utf-8"))


def test_ids_unicos_y_unas_30_preguntas():
    ids = [t["id"] for t in _VERDADES]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 30


@pytest.mark.parametrize("tarea", _VERDADES, ids=lambda t: t["id"])
def test_cada_verdad_dice_como_se_midio_y_usa_checks_conocidos(tarea):
    assert tarea.get("query") and tarea.get("category")
    assert tarea.get("medida"), "una verdad sin cómo se midió no se puede volver a medir"
    assert tarea.get("expected")
    assert not set(tarea["expected"]) - _KNOWN_EXPECTED_KEYS


@pytest.mark.parametrize(("texto", "esperado"), [
    ("Hay 2.584 lotes", 2584.0),          # miles en español
    ("Hay 2,584 lots", 2584.0),           # miles en inglés
    ("media 0,6413", 0.6413),             # decimal en español
    ("promedio de 882,73 m²", 882.73),
    ("total 2.409.199 construcciones", 2409199.0),
    ("1.234,5 m²", 1234.5),
    ("son 30 lotes.", 30.0),              # el punto final no es decimal
])
def test_cifras_lee_formatos_espanol_e_ingles(texto, esperado):
    assert esperado in cifras(texto)


def _tarea(**expected):
    return {"id": "t", "expected": expected}


def test_answer_numbers_acepta_la_verdad_con_tolerancia():
    v = evaluate(_tarea(answer_numbers=[{"valor": 2584, "tol": 0.02}]),
                 {"message": "En Chapinero hay 2.590 lotes de más de 1000 m²."})
    assert v.ok, v.reasons


def test_answer_numbers_rechaza_la_cifra_falsa_de_la_revision():
    """El caso real: «43.249 lotes en Chapinero» eran los de todo Bogotá."""
    tarea = _tarea(answer_numbers=[{"valor": 2584, "tol": 0.02}], answer_numbers_not=[43249])
    v = evaluate(tarea, {"message": "Hay 43.249 lotes de más de 1000 m² en Chapinero."})
    assert not v.ok
    assert any("FALSA" in r for r in v.reasons)


def test_answer_numbers_exige_todas_las_cifras():
    v = evaluate(_tarea(answer_numbers=[1941, 59]), {"message": "Hay 1.941 sedes oficiales."})
    assert not v.ok


def test_rango():
    assert evaluate(_tarea(answer_numbers=[{"rango": [0.60, 0.68]}]),
                    {"message": "NDVI promedio 0,6413"}).ok
    assert not evaluate(_tarea(answer_numbers=[{"rango": [0.60, 0.68]}]),
                        {"message": "NDVI promedio 0,41"}).ok


def test_tool_regex_mira_las_herramientas_del_bucle_incluidas_las_mcp():
    resultado = {"decision_trace": [
        {"step": 0, "kind": "tool_call", "tool": "lugares__lugares_buscar"},
        {"step": 0, "kind": "tool_result", "tool": "lugares__lugares_buscar", "success": True},
    ]}
    assert evaluate(_tarea(tool_regex="lugares"), resultado).ok
    assert not evaluate(_tarea(tool_regex="imagery"), resultado).ok


def test_has_imagery():
    assert evaluate(_tarea(has_imagery=True), {"external_imagery": {"tiles": "x"}}).ok
    assert not evaluate(_tarea(has_imagery=True), {}).ok


def test_alguno_acepta_cualquiera_de_las_lecturas_defendibles():
    tarea = _tarea(answer_numbers=[{"alguno": [9, 10]}])
    assert evaluate(tarea, {"message": "Hay 10 sedes rurales"}).ok
    assert evaluate(tarea, {"message": "Hay 9 sedes rurales"}).ok
    assert not evaluate(tarea, {"message": "Hay 17 sedes rurales"}).ok
