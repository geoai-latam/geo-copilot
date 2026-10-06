"""Tests del backend 'docker' del sandbox (SEC-SANDBOX-RCE p2).

No arrancan Docker: mockean ``asyncio.create_subprocess_exec`` para verificar
que ``execute()`` con ``sandbox_backend='docker'`` construye el ``docker exec``
correcto y parsea el resultado por el mismo protocolo JSON-por-stdin, que el
check POSIX no bloquea el modo docker, y que el backend por defecto
('subprocess') sigue lanzando el runner local.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from geo_copilot.agents.gis_agent import sandbox as sandbox_mod
from geo_copilot.agents.gis_agent.sandbox import PythonSandbox


class _FakeProc:
    """Stub de asyncio.subprocess.Process: devuelve un JSON fijo por stdout."""

    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self, payload: bytes):
        self._payload = payload
        return (self._stdout, b"")

    def kill(self) -> None:  # pragma: no cover - sólo en timeout
        pass

    async def wait(self):  # pragma: no cover - sólo en timeout
        return self.returncode


def _fake_exec(result: dict, returncode: int = 0):
    """(coro_func, calls): la coro captura argv/kwargs y devuelve un _FakeProc."""
    calls: dict = {}

    async def fake(*argv, **kwargs):
        calls["argv"] = list(argv)
        calls["kwargs"] = kwargs
        return _FakeProc(json.dumps(result).encode("utf-8"), returncode)

    return fake, calls


@pytest.mark.asyncio
async def test_docker_backend_construye_docker_exec_y_parsea():
    sb = PythonSandbox()
    fake, calls = _fake_exec({"success": True, "output": "", "results": {"x": 1}})
    with patch.object(sandbox_mod.settings, "sandbox_backend", "docker"), patch.object(
        sandbox_mod.settings, "sandbox_docker_container", "geo_copilot_sandbox"
    ), patch.object(
        sandbox_mod.settings, "sandbox_docker_runner_path", "/opt/sandbox/sandbox_runner.py"
    ), patch.object(sandbox_mod.asyncio, "create_subprocess_exec", fake):
        result = await sb.execute("x = 1", require_approval=False)

    assert result["success"] is True
    assert result["results"] == {"x": 1}

    argv = calls["argv"]
    # docker exec -i ... geo_copilot_sandbox python -I /opt/sandbox/sandbox_runner.py
    assert argv[0] == "docker"
    assert argv[1] == "exec"
    assert argv[2] == "-i"
    assert "geo_copilot_sandbox" in argv
    assert argv[-3:] == ["python", "-I", "/opt/sandbox/sandbox_runner.py"]
    # Los thread caps van como flags -e antes del nombre del contenedor.
    assert "-e" in argv
    assert "OPENBLAS_NUM_THREADS=1" in argv
    assert "OMP_NUM_THREADS=1" in argv


@pytest.mark.asyncio
async def test_docker_backend_no_bloquea_en_so_no_posix():
    """En modo docker la contención es el contenedor Linux; el SO del host
    (Windows-dev, SANDBOX_AVAILABLE=False) no debe bloquear la ejecución."""
    sb = PythonSandbox()
    fake, _ = _fake_exec({"success": True, "output": "", "results": {}})
    with patch.object(sandbox_mod, "SANDBOX_AVAILABLE", False), patch.object(
        sandbox_mod.settings, "sandbox_backend", "docker"
    ), patch.object(sandbox_mod.asyncio, "create_subprocess_exec", fake):
        result = await sb.execute("x = 1", require_approval=False)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_subprocess_backend_en_so_no_posix_sigue_rechazando():
    """El backend por defecto SÍ exige POSIX (rlimits); no debe cambiar."""
    sb = PythonSandbox()
    with patch.object(sandbox_mod, "SANDBOX_AVAILABLE", False), patch.object(
        sandbox_mod.settings, "sandbox_backend", "subprocess"
    ):
        result = await sb.execute("x = 1", require_approval=False)
    assert result["success"] is False
    assert "POSIX" in result["error"]


@pytest.mark.asyncio
async def test_subprocess_backend_construye_argv_local():
    sb = PythonSandbox()
    fake, calls = _fake_exec({"success": True, "output": "", "results": {"x": 1}})
    with patch.object(sandbox_mod, "SANDBOX_AVAILABLE", True), patch.object(
        sandbox_mod.settings, "sandbox_backend", "subprocess"
    ), patch.object(sandbox_mod.asyncio, "create_subprocess_exec", fake):
        result = await sb.execute("x = 1", require_approval=False)

    assert result["success"] is True
    argv = calls["argv"]
    assert argv[0] == sys.executable
    assert argv[1] == "-I"
    assert argv[2].endswith("sandbox_runner.py")
    # El env local trae los thread caps (no van por -e como en docker).
    assert calls["kwargs"]["env"]["OPENBLAS_NUM_THREADS"] == "1"


@pytest.mark.asyncio
async def test_docker_backend_returncode_no_cero_marca_fallo():
    sb = PythonSandbox()
    # El runner reportó success pero el proceso salió !=0 → se fuerza fallo.
    fake, _ = _fake_exec({"success": True, "results": {}}, returncode=1)
    with patch.object(sandbox_mod.settings, "sandbox_backend", "docker"), patch.object(
        sandbox_mod.asyncio, "create_subprocess_exec", fake
    ):
        result = await sb.execute("x = 1", require_approval=False)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_docker_backend_sin_json_reporta_no_result():
    """docker exec falla (contenedor caído / socket sin permisos) → stdout no
    es JSON → error de plumbing legible, no un crash."""
    sb = PythonSandbox()

    async def fake(*argv, **kwargs):
        return _FakeProc(b"Cannot connect to the Docker daemon", returncode=1)

    with patch.object(sandbox_mod.settings, "sandbox_backend", "docker"), patch.object(
        sandbox_mod.asyncio, "create_subprocess_exec", fake
    ):
        result = await sb.execute("x = 1", require_approval=False)
    assert result["success"] is False
    assert "no result" in result["error"].lower()
