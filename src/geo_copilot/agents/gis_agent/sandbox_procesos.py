"""Los PROCESOS que ejecutan el código: el payload, la comunicación con el runner y los
backends subprocess (con límites del SO) y docker.

Salió de `PythonSandbox` (F4 del plan de calidad: sandbox.py tenía 593 líneas), tal cual.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from geo_copilot.core.config import settings
from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.gis_agent.sandbox")


_RUNNER_PATH = Path(__file__).with_name("sandbox_runner.py")


# Thread caps críticos para numpy/scipy/geopandas DENTRO del sandbox: sin
# ellos el import reserva memoria virtual proporcional al nº de CPUs y
# RLIMIT_AS mata al child antes de correr código de usuario. Compartidos por
# ambos backends (env del subprocess local / flags -e del `docker exec`).
_THREAD_CAPS = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    # esda/pointpats importan matplotlib al cargar; su config dir por defecto
    # (~/.config) no es escribible en el sandbox → apuntarlo a /tmp (escribible).
    "MPLCONFIGDIR": "/tmp",
    # numba (usado por esda/libpysal) single-thread + cache en /tmp: evita que
    # el JIT spawnee muchos threads (choca con RLIMIT_NPROC) y que intente
    # escribir su cache en ~/.cache (no escribible).
    "NUMBA_NUM_THREADS": "1",
    "NUMBA_CACHE_DIR": "/tmp",
}


class SandboxProcesosMixin:
    """Los PROCESOS que ejecutan el código: el payload, la comunicación con el runner y los"""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        timeout: int
        max_output_size: int
        memory_mb: int

    def _build_payload(
        self, code: str, input_data: dict[str, Any], datasets: dict[str, str] | None = None,
    ) -> bytes:
        """Serializa el payload JSON que consume ``sandbox_runner.py`` por stdin.
        Idéntico para ambos backends: el runner es el mismo, cambia sólo cómo
        se lanza (child local vs ``docker exec``)."""
        payload = {
            "code": code,
            "input_data": input_data,
            "cpu_seconds": max(1, int(self.timeout)),
            "memory_mb": self.memory_mb,
            "output_bytes": self.max_output_size,
        }
        if datasets:
            # La raíz la ve el runner: en docker es el volumen montado read-only
            # en el sandbox; en subprocess, el directorio donde app exporta.
            payload["datasets"] = datasets
            payload["datasets_root"] = (
                settings.sandbox_docker_datasets_root
                if settings.sandbox_backend == "docker"
                else settings.workspace_export_dir
            )
        return json.dumps(payload, default=str).encode("utf-8")

    async def _communicate(
        self,
        argv: list[str],
        payload_json: bytes,
        *,
        env: dict[str, str] | None,
        on_timeout: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Lanza ``argv``, envía el payload por stdin, parsea el JSON de stdout.

        Frontera común de ambos backends: aplica el wall-clock duro
        (``asyncio.wait_for``) y mata el proceso al vencer. El ``argv`` es o
        bien ``[python, -I, runner]`` (subprocess) o ``[docker, exec, -i, ...,
        runner]`` (docker); en ambos casos el protocolo stdin/stdout es igual.

        ``on_timeout``: SBX-03. En el backend docker, ``process`` es el CLIENTE
        ``docker exec``; matarlo NO termina el intérprete DENTRO del contenedor.
        Este callback (best-effort) hace el kill in-container (``pkill``) para
        que un loop/CPU-spin no sobreviva al timeout consumiendo la cuota del
        contenedor sandbox.
        """
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=tempfile.gettempdir(),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(payload_json),
                timeout=self.timeout + 5,  # rlimit kicks in first; this is the hard wall.
            )
        except TimeoutError:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                pass
            if on_timeout is not None:
                try:
                    await on_timeout()
                except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                    logger.warning("Sandbox timeout cleanup falló: %s", exc)
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        try:
            result: dict[str, Any] = json.loads(stdout)
        except json.JSONDecodeError:
            # El proceso murió antes de escribir JSON (OOM/CPU kill, contenedor
            # caído, socket sin permisos...). Exponemos la cola de stderr para
            # diagnóstico; se saneará antes de llegar al cliente.
            return {
                "success": False,
                "error": "Sandbox process produced no result",
                "stderr": stderr[-400:],
                "exit_code": process.returncode,
            }

        if process.returncode != 0 and result.get("success"):
            # Defensive: never report success if the child exited non-zero.
            result["success"] = False
            result.setdefault("error", f"non-zero exit: {process.returncode}")

        return result

    async def _run_in_subprocess(
        self, code: str, input_data: dict[str, Any], datasets: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Backend por defecto: child Python local dentro del contenedor app."""
        payload_json = self._build_payload(code, input_data, datasets)

        # Minimal env: keep PATH (so the interpreter can resolve its own
        # binary) and any PYTHONPATH that the venv depends on, drop the rest.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            **_THREAD_CAPS,
        }
        # ``PYTHONPATH`` is needed when the project runs under an editable
        # install or a virtualenv that the child needs to mirror.
        if "PYTHONPATH" in os.environ:
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]

        # -I: modo aislado (sin site.py de usuario ni PYTHON* salvo PYTHONPATH).
        argv = [sys.executable, "-I", str(_RUNNER_PATH)]
        return await self._communicate(argv, payload_json, env=env)

    async def _run_in_docker(
        self, code: str, input_data: dict[str, Any], datasets: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Backend endurecido: ``docker exec`` contra el contenedor `sandbox`.

        El runner corre DENTRO del contenedor con network:none, read_only,
        cap_drop:ALL y non-root (ver docker/docker-compose.yml). Es la
        contención real contra RCE/egreso: aunque el código de LLM cargue una
        librería nativa, no tiene red ni capacidades ni FS de escritura.
        """
        payload_json = self._build_payload(code, input_data, datasets)

        # `docker exec -i`: stdin abierto para el payload, sin -t (sin TTY, así
        # stdout queda limpio). Los thread caps se inyectan con -e dentro del
        # contenedor. `python -I` ignora los PYTHON* del contenedor.
        argv = [settings.sandbox_docker_bin, "exec", "-i"]
        for key, value in _THREAD_CAPS.items():
            argv += ["-e", f"{key}={value}"]
        argv += [
            settings.sandbox_docker_container,
            "python",
            "-I",
            settings.sandbox_docker_runner_path,
        ]

        async def _kill_in_container() -> None:
            """SBX-03: matar el runner DENTRO del contenedor al vencer el
            timeout (matar el cliente ``docker exec`` no lo propaga)."""
            kill_argv = [
                settings.sandbox_docker_bin, "exec",
                settings.sandbox_docker_container,
                "pkill", "-f", "sandbox_runner.py",
            ]
            proc = await asyncio.create_subprocess_exec(
                *kill_argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)

        # env=None → el docker CLI hereda el entorno del app (encuentra el
        # daemon vía DOCKER_HOST, apuntado al docker-socket-proxy en el compose).
        return await self._communicate(
            argv, payload_json, env=None, on_timeout=_kill_in_container,
        )
