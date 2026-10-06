"""Tests del benchmark agéntico (A1).

Dos capas:
1. Unitarios DETERMINISTAS (suite por defecto): el evaluador puntúa bien, las
   capas sintéticas son estables, y tasks.yaml es válido (ids únicos,
   categorías/checks conocidos) — un typo en el YAML se detecta aquí, no a
   mitad de una corrida de 20 minutos con LLM real.
2. Wrapper end-to-end (markers llm + agentic_bench): corre el runner real por
   subprocess sobre un subset host-safe. La corrida completa se hace dentro
   del contenedor app (ver tests/agentic_bench/README.md).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_BENCH = Path(__file__).parent / "agentic_bench"
sys.path.insert(0, str(_BENCH))

from evaluator import aggregate, evaluate
from synthetic import build_layer, synthetic_points, synthetic_polygons

# Vocabulario COMPLETO de checks que entiende evaluator.evaluate(). Si se añade
# un check nuevo, se registra aquí (y viceversa) — sincronía YAML ↔ evaluador.
_KNOWN_EXPECTED_KEYS = {
    "success", "partial_ok", "intent_any", "intent_optional_react",
    "trajectory_contains", "trajectory_absent", "sql_regex", "no_sql",
    "min_results", "max_results", "result_kind", "visualization_type",
    "has_symbology", "symbology_type_not", "min_services",
    "answer_regex", "answer_not_regex",
    "answer_numbers", "answer_numbers_not", "tool_regex", "has_imagery",
}
_KNOWN_CATEGORIES = {
    "simple", "aggregation", "spatial", "analytic", "multistep",
    "followup", "symbology", "external", "ambiguous", "unresolvable",
    "imagery",
}


def _load_tasks() -> list[dict]:
    return yaml.safe_load((_BENCH / "tasks.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# tasks.yaml es válido
# ---------------------------------------------------------------------------
class TestTasksYaml:
    def test_ids_unicos_y_query_presente(self):
        tasks = _load_tasks()
        ids = [t["id"] for t in tasks]
        assert len(ids) == len(set(ids)), "ids duplicados en tasks.yaml"
        assert all(t.get("query") for t in tasks)
        assert len(tasks) >= 30, "el benchmark debe tener 30+ tareas (spec A1)"

    def test_categorias_conocidas(self):
        for t in _load_tasks():
            assert t.get("category") in _KNOWN_CATEGORIES, t["id"]

    def test_checks_conocidos(self):
        for t in _load_tasks():
            unknown = set(t.get("expected") or {}) - _KNOWN_EXPECTED_KEYS
            assert not unknown, f"{t['id']}: checks desconocidos {unknown}"

    def test_setup_solo_capas_sinteticas_existentes(self):
        for t in _load_tasks():
            layer = (t.get("setup") or {}).get("previous_geojson")
            if layer:
                assert build_layer(layer)["features"], t["id"]

    def test_toda_tarea_tiene_al_menos_un_check(self):
        for t in _load_tasks():
            assert t.get("expected"), f"{t['id']} sin expected"


# ---------------------------------------------------------------------------
# capas sintéticas deterministas
# ---------------------------------------------------------------------------
class TestSynthetic:
    def test_puntos_deterministas_con_outliers(self):
        a, b = synthetic_points(), synthetic_points()
        assert a == b  # sin aleatoriedad
        assert len(a["features"]) == 60
        valores = [f["properties"]["valor"] for f in a["features"]]
        outliers = [v for v in valores if v > 3000]
        assert len(outliers) == 3  # idx 10/30/50

    def test_poligonos_deterministas(self):
        a = synthetic_polygons()
        assert len(a["features"]) == 24
        assert a == synthetic_polygons()


# ---------------------------------------------------------------------------
# evaluador
# ---------------------------------------------------------------------------
def _ok_result(**over) -> dict:
    base = {
        "success": True, "intent": "query_data",
        "message": "Se encontraron 25 lotes.",
        "sql": "SELECT * FROM catastro.lotes LIMIT 25",
        "data": {"results": [{"a": 1}] * 25},
        "geojson": {"type": "FeatureCollection", "features": [{"f": i} for i in range(25)]},
        "agent_messages": [{"agent": "router"}, {"agent": "gis_agent"}],
        "reasoning_trace": [],
        "visualization": None, "symbology": None, "found_services": None,
    }
    base.update(over)
    return base


class TestEvaluator:
    def test_tarea_simple_pasa(self):
        task = {"id": "x", "expected": {
            "success": True, "intent_any": ["query_data"], "sql_regex": "limit",
            "trajectory_contains": ["gis_agent"], "min_results": 1, "max_results": 25,
            "result_kind": "geojson",
        }}
        v = evaluate(task, _ok_result())
        assert v.ok, v.reasons

    def test_success_falso_falla_con_razon(self):
        v = evaluate({"id": "x", "expected": {"success": True}},
                     _ok_result(success=False))
        assert not v.ok and "success" in v.reasons[0]

    def test_trayectoria_react_mapea_tools_a_agentes(self):
        # Motor ReAct: sin agent_messages, la trayectoria sale del trace.
        result = _ok_result(agent_messages=[], reasoning_trace=[
            {"kind": "tool_call", "tool": "query_database"},
            {"kind": "tool_result", "tool": "query_database"},
            {"kind": "tool_call", "tool": "analyze_layer"},
        ])
        v = evaluate({"id": "x", "expected": {
            "trajectory_contains": ["gis_agent", "python_agent"]}}, result)
        assert v.ok, v.reasons

    def test_intent_optional_react(self):
        # ReAct no emite intent — con el flag y trace presente, no penaliza.
        result = _ok_result(intent=None, reasoning_trace=[{"kind": "final"}])
        v = evaluate({"id": "x", "expected": {
            "intent_any": ["query_data"], "intent_optional_react": True}}, result)
        assert v.ok, v.reasons

    def test_result_kind_table_or_chart(self):
        result = _ok_result(
            geojson=None, visualization={"type": "table"},
            data={"results": [{"cluster": 0, "n": 30}]},
        )
        v = evaluate({"id": "x", "expected": {"result_kind": "table_or_chart"}}, result)
        assert v.ok, v.reasons
        v2 = evaluate({"id": "x", "expected": {"result_kind": "table_or_chart"}},
                      _ok_result(visualization=None, data={"results": []}, geojson=None))
        assert not v2.ok

    def test_no_sql_y_symbology_not(self):
        result = _ok_result(sql=None, symbology={"symbology_type": "cluster"})
        v = evaluate({"id": "x", "expected": {
            "no_sql": True, "symbology_type_not": "cluster"}}, result)
        assert v.checks["no_sql"] is True
        assert v.checks["symbology_type_not"] is False

    def test_partial_ok_relaja_success(self):
        result = _ok_result(success=False, partial=True)
        v = evaluate({"id": "x", "expected": {"success": True, "partial_ok": True}}, result)
        assert v.ok, v.reasons

    def test_answer_not_regex_detecta_fabricacion(self):
        v = evaluate(
            {"id": "x", "expected": {"answer_not_regex": r"hay \d+ hospitales"}},
            _ok_result(message="Hay 42 hospitales en la base de datos."),
        )
        assert not v.ok

    def test_tarea_sin_checks_falla(self):
        assert not evaluate({"id": "x", "expected": {}}, _ok_result()).ok

    def test_aggregate(self):
        from evaluator import TaskVerdict
        vs = [TaskVerdict("a", True), TaskVerdict("b", False), TaskVerdict("c", True)]
        agg = aggregate(vs)
        assert agg == {"n_tasks": 3, "n_passed": 2, "score": round(2 / 3, 4)}


# ---------------------------------------------------------------------------
# wrapper end-to-end (LLM real; subset host-safe). La corrida completa va
# dentro del contenedor (README).
# ---------------------------------------------------------------------------
@pytest.mark.llm
@pytest.mark.agentic_bench
def test_bench_subset_host(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_BENCH / "runner.py"),
         "--category", "followup", "--out", str(tmp_path), "--timeout", "180"],
        capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, proc.stderr[-2000:] or proc.stdout[-2000:]
    outputs = list(tmp_path.glob("*.json"))
    assert outputs, "el runner no escribió resultados"
    summary = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert summary["n_tasks"] >= 2
    # Sanity, no exigencia: el subset conversacional debe estar mayormente sano.
    assert summary["score"] >= 0.5, summary
