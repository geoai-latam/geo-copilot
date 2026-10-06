"""F7 (auditoría): la puerta de versión — umbral de las evals, `--umbral` del bench y el CI que publica.

- scripts/umbral_evals.py: decide si una versión sale a partir del JUnit de `pytest -m llm`.
- tests/agentic_bench/runner.py: `--umbral` de la corrida nocturna.
- .github/workflows/ci.yml: publicar exige todo en verde y solo ese job escribe en GHCR.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_RAIZ = Path(__file__).resolve().parent.parent


def _cargar(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None, ruta
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


umbral_evals = _cargar("umbral_evals", _RAIZ / "scripts" / "umbral_evals.py")


def _junit(tmp_path: Path, casos: str) -> str:
    ruta = tmp_path / "evals.xml"
    ruta.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">{casos}'
                    "</testsuite></testsuites>", encoding="utf-8")
    return str(ruta)


def _ok(n: int, archivo: str = "test_llm_a") -> str:
    return "".join(f'<testcase classname="tests.{archivo}" name="test_{i}"/>' for i in range(n))


_FALLO = '<testcase classname="tests.test_llm_b" name="test_falla"><failure message="assert 0"/></testcase>'
_SALTO = ('<testcase classname="tests.test_llm_c" name="test_salta">'
          '<skipped type="pytest.skip" message="el LLM decidió pasar el turno al bucle"/></testcase>')
_NO_RECOGIDO = '<testcase classname="" name="tests.test_llm_roto"><error message="collection failure"/></testcase>'


# --------------------------------------------------------------------------- umbral_evals


def test_todo_aprobado_pasa_la_puerta(tmp_path, capsys):
    assert umbral_evals.main([_junit(tmp_path, _ok(5)), "--umbral", "0.95"]) == 0
    assert "5/5 = 100.0%" in capsys.readouterr().out


def test_bajo_el_umbral_bloquea_y_nombra_el_fallo(tmp_path, capsys):
    assert umbral_evals.main([_junit(tmp_path, _ok(9) + _FALLO), "--umbral", "0.95"]) == 1
    out = capsys.readouterr().out
    assert "9/10" in out and "::error::por debajo del umbral" in out
    assert "FALLA tests.test_llm_b::test_falla" in out


def test_un_fallo_suelto_sobre_el_umbral_es_varianza(tmp_path):
    assert umbral_evals.main([_junit(tmp_path, _ok(19) + _FALLO), "--umbral", "0.95"]) == 0


def test_el_salto_no_entra_en_la_tasa_pero_se_lista_con_su_motivo(tmp_path, capsys):
    assert umbral_evals.main([_junit(tmp_path, _ok(4) + _SALTO), "--umbral", "1"]) == 0
    out = capsys.readouterr().out
    assert "4/4" in out and "1 saltados" in out
    assert "SALTA ×1 skip: el LLM decidió pasar el turno al bucle" in out


def test_solo_saltos_no_es_un_verde(tmp_path, capsys):
    assert umbral_evals.main([_junit(tmp_path, _SALTO * 3), "--umbral", "0.5"]) == 1
    out = capsys.readouterr().out
    assert "no se ejecutó ningún test de juicio" in out
    assert "SALTA ×3" in out


def test_un_archivo_que_no_se_recoge_bloquea_aunque_el_resto_pase(tmp_path, capsys):
    # antes contaba como UN fallo: 80 aprobados + 1 = 98,8 % y la versión salía sin ese archivo
    assert umbral_evals.main([_junit(tmp_path, _ok(80) + _NO_RECOGIDO), "--umbral", "0.95"]) == 1
    out = capsys.readouterr().out
    assert "no se pudieron recoger" in out and "tests.test_llm_roto" in out


def test_fallo_de_teardown_en_un_test_saltado_cuenta_como_fallo(tmp_path):
    caso = ('<testcase classname="tests.test_llm_d" name="test_x"><skipped message="x"/>'
            '<error message="failed on teardown"/></testcase>')
    assert umbral_evals.main([_junit(tmp_path, _ok(1) + caso), "--umbral", "0.9"]) == 1


def test_sin_xml_lo_dice_sin_traza(tmp_path, capsys):
    assert umbral_evals.main([str(tmp_path / "no_existe.xml")]) == 1
    assert "::error::no se pudo leer el resultado de pytest" in capsys.readouterr().out


def test_xml_truncado_lo_dice_sin_traza(tmp_path, capsys):
    ruta = tmp_path / "evals.xml"
    ruta.write_text("<testsuites><testsuite>", encoding="utf-8")
    assert umbral_evals.main([str(ruta)]) == 1
    assert "::error::no se pudo leer" in capsys.readouterr().out


@pytest.mark.parametrize("umbral", ["95", "-0.1"])
def test_umbral_fuera_de_0_1_se_rechaza(tmp_path, umbral):
    with pytest.raises(SystemExit) as exc:
        umbral_evals.main([_junit(tmp_path, _ok(1)), "--umbral", umbral])
    assert exc.value.code == 2


def test_el_formato_de_junit_de_este_pytest_es_el_que_se_lee(tmp_path, capsys):
    """El hecho del que depende la puerta: cómo escribe pytest un skip, un fallo y un módulo roto."""
    (tmp_path / "test_bien.py").write_text(
        "import pytest\n"
        "def test_ok(): pass\n"
        "def test_salta(): pytest.skip('motivo real')\n"
        "def test_falla(): assert 0\n", encoding="utf-8")
    (tmp_path / "test_roto.py").write_text("import modulo_que_no_existe\n", encoding="utf-8")
    (tmp_path / "vacio.ini").write_text("[pytest]\n", encoding="utf-8")  # aislado del pyproject del repo
    xml = tmp_path / "evals.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                    "--continue-on-collection-errors", f"--rootdir={tmp_path}", "-c", str(tmp_path / "vacio.ini"),
                    f"--junitxml={xml}", str(tmp_path)],
                   cwd=tmp_path, capture_output=True, check=False)
    assert umbral_evals.main([str(xml), "--umbral", "0"]) == 1
    out = capsys.readouterr().out
    assert "test_roto" in out and "no se pudieron recoger" in out

    (tmp_path / "test_roto.py").unlink()
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                    f"--rootdir={tmp_path}", "-c", str(tmp_path / "vacio.ini"), f"--junitxml={xml}",
                    str(tmp_path)], cwd=tmp_path, capture_output=True, check=False)
    assert umbral_evals.main([str(xml), "--umbral", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "1/2 = 50.0%" in out and "SALTA ×1 skip: motivo real" in out
    assert "FALLA test_bien::test_falla" in out


# --------------------------------------------------------------------------- runner --umbral


@pytest.fixture(scope="module")
def runner():
    return _cargar("bench_runner_f7", _RAIZ / "tests" / "agentic_bench" / "runner.py")


def test_runner_sin_umbral_no_juzga(runner):
    assert runner._bajo_umbral({"score": 0.0, "n_tasks": 3}, None) is None


def test_runner_bajo_el_umbral_sale_con_motivo(runner):
    motivo = runner._bajo_umbral({"score": 0.8, "n_tasks": 10}, 0.85)
    assert motivo and "POR DEBAJO DEL UMBRAL" in motivo and "80.00%" in motivo
    assert runner._bajo_umbral({"score": 0.85, "n_tasks": 10}, 0.85) is None


def test_runner_sin_tareas_no_flaky_no_finge_un_score(runner):
    motivo = runner._bajo_umbral({"score": 0.0, "n_tasks": 0}, 0.85)
    assert motivo and "NINGUNA TAREA" in motivo and "UMBRAL" not in motivo


def test_runner_umbral_en_porcentaje_se_rechaza(runner):
    import argparse

    assert runner._fraccion("0.85") == 0.85
    with pytest.raises(argparse.ArgumentTypeError):
        runner._fraccion("85")


# --------------------------------------------------------------------------- ci.yml


@pytest.fixture(scope="module")
def ci():
    return yaml.safe_load((_RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def test_publicar_exige_todos_los_jobs_y_respeta_la_cancelacion(ci):
    publicar = ci["jobs"]["publicar"]
    assert set(publicar["needs"]) == {"backend", "integration", "frontend", "evals", "images"}
    cond = publicar["if"]
    assert "!cancelled()" in cond and "always()" not in cond
    for job in ("backend", "integration", "frontend", "images"):
        assert f"needs.{job}.result == 'success'" in cond
    assert "needs.evals.result == 'skipped'" in cond  # fuera de etiquetas v* evals no corre


def test_solo_publicar_escribe_en_el_registro(ci):
    con_escritura = [n for n, j in ci["jobs"].items() if (j.get("permissions") or {}).get("packages") == "write"]
    assert con_escritura == ["publicar"]
    assert ci["permissions"] == {"contents": "read"}
    # el job con el token no ejecuta acciones de terceros
    usos = [p["uses"] for p in ci["jobs"]["publicar"]["steps"] if "uses" in p]
    assert all(u.startswith("actions/") for u in usos), usos
    # y publica lo escaneado: no vuelve a construir
    assert not any("build-push-action" in u for u in usos)


def test_las_matrices_de_build_y_publicacion_coinciden(ci):
    construidas = [m["name"] for m in ci["jobs"]["images"]["strategy"]["matrix"]["include"]]
    assert construidas == ci["jobs"]["publicar"]["strategy"]["matrix"]["name"]


@pytest.mark.parametrize("flujo", ["ci.yml", "evals.yml"])
def test_acciones_fijadas_por_sha_completo(flujo):
    texto = (_RAIZ / ".github" / "workflows" / flujo).read_text(encoding="utf-8")
    usos = re.findall(r"uses:\s*(\S+)", texto)
    externas = [u for u in usos if not u.startswith("./")]
    assert externas
    for uso in externas:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uso), f"{flujo}: {uso} no está fijada por SHA"


def test_ruff_cubre_scripts(ci):
    pasos = ci["jobs"]["backend"]["steps"]
    ruff = next(p["run"] for p in pasos if p.get("name") == "Ruff lint")
    assert "scripts/" in ruff


def test_el_backend_instala_promtool_y_exige_que_las_reglas_se_prueben(ci):
    pasos = ci["jobs"]["backend"]["steps"]
    nombres = [p.get("name") for p in pasos]
    promtool = next(p for p in pasos if p.get("name") == "Promtool (reglas de alertas)")
    assert "PROMTOOL=" in promtool["run"]
    pytest_ = next(p for p in pasos if p.get("name") == "Pytest")
    assert nombres.index("Promtool (reglas de alertas)") < nombres.index("Pytest")
    assert pytest_["env"]["REQUIRE_PROMTOOL"] == "1"


def test_una_sola_linea_de_node_en_engines_ci_dockerfile_y_readme(ci):
    """Una dependencia pide Node >= 22: si `engines`, el CI, la imagen o el README se quedan en otra
    línea, alguien instala con una versión que el proyecto declara no soportada (npm solo avisa)."""
    import json

    engines = json.loads((_RAIZ / "frontend" / "package.json").read_text(encoding="utf-8"))["engines"]
    linea = re.fullmatch(r">=(\d+)", engines["node"]).group(1)
    pasos = ci["jobs"]["frontend"]["steps"]
    assert next(p for p in pasos if "setup-node" in p.get("uses", ""))["with"]["node-version"] == linea
    dockerfile = (_RAIZ / "docker" / "Dockerfile.frontend").read_text(encoding="utf-8")
    assert re.findall(r"(?m)^FROM node:(\d+)", dockerfile) == [linea]
    readme = (_RAIZ / "README.md").read_text(encoding="utf-8")
    assert set(re.findall(r"Node\.js (\d+)\+", readme)) == {linea}
