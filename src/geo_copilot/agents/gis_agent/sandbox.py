"""
Python sandbox — subprocess isolation for LLM-generated analysis code.

This is the security boundary for the PythonAgent. User-generated /
LLM-generated code never executes in the API server's address space.
Each ``execute()`` call spawns ``sandbox_runner.py`` as a child Python
process and communicates with it via JSON over stdin/stdout.

Defence in depth:

1. **AST allowlist** (``analyze_security``) — runs in the parent before
   the code is dispatched. Blocks known-bad imports/names. This is a
   first filter, **NOT** the isolation layer: un denylist AST no contiene
   RCE de un intérprete de propósito general (ver SBX-01). Se endurece de
   todos modos para cerrar los vectores conocidos.
2. **Contención real = contenedor endurecido** (backend ``docker``). En el
   despliegue (docker-compose) el default es ``SANDBOX_BACKEND=docker``: el
   código corre vía ``docker exec`` dentro del contenedor ``sandbox``
   (network:none, read_only, cap_drop:ALL, no-new-privileges, non-root), al
   que ``app`` llega por un **docker-socket-proxy** (allowlist solo EXEC) —
   ``app`` nunca toca el socket crudo del host. El backend ``subprocess``
   (default de código, solo dev POSIX) aísla únicamente la memoria del
   proceso padre, no la red/FS/socket.
3. **POSIX rlimits** en el child (CPU, memoria, file size, nproc) — ver
   ``sandbox_runner.py``. Backstop contra loops que ignoran el kill.
4. **Wall-clock timeout** en el padre — ``asyncio.wait_for``; mata el
   proceso al vencer (y, en backend docker, ``pkill`` dentro del contenedor,
   porque matar el cliente ``docker exec`` no termina el proceso in-container).
5. **HITL** approval still gates execution when enabled.
"""

from __future__ import annotations

import asyncio  # noqa: F401 — las pruebas sustituyen asyncio.create_subprocess_exec vía sandbox.asyncio
import os
from typing import Any, cast

from geo_copilot.core.config import settings
from geo_copilot.core.logging import get_logger
from geo_copilot.security.hitl import HITLActionType, HITLManager, HITLStatus

logger = get_logger(__name__)

# F4: la seguridad (listas y análisis) y los procesos (payload, runner, backends) viven en sus
# módulos (mixins); las excepciones y las constantes se reexportan porque se importan de aquí.
from geo_copilot.agents.gis_agent.sandbox_procesos import (  # noqa: F401
    _RUNNER_PATH,
    _THREAD_CAPS,
    SandboxProcesosMixin,
)
from geo_copilot.agents.gis_agent.sandbox_seguridad import (  # noqa: F401
    ResourceLimitExceeded,
    SandboxError,
    SandboxSeguridadMixin,
    SecurityViolation,
)

# B7: la contención real del sandbox son los rlimits POSIX que aplica
# ``sandbox_runner.py`` (RLIMIT_AS/CPU/NPROC/...). En SO no-POSIX (Windows)
# esos límites no existen, así que ejecutar código LLM ahí correría con
# contención degradada. En vez de fallar de forma opaca (como antes: el
# runner reventaba con ``ImportError: resource`` o ``cwd=/tmp`` inexistente,
# tragado por un ``except`` genérico), rechazamos explícitamente.
# Producción corre en el contenedor Linux (docker/Dockerfile.sandbox);
# en Windows-dev usar WSL2 o Docker Desktop.
SANDBOX_AVAILABLE = os.name == "posix"











class PythonSandbox(SandboxSeguridadMixin, SandboxProcesosMixin):
    """Ejecutor aislado para código Python generado por el LLM."""





    def __init__(
        self,
        timeout: int | None = None,
        max_output_size: int = 1_000_000,
        memory_mb: int | None = None,
        hitl_manager: HITLManager | None = None,
    ):
        # ``timeout`` is honored as the wall-clock budget for one execution.
        self.timeout = timeout if timeout is not None else settings.sandbox_timeout
        self.max_output_size = max_output_size
        # ``memory_mb`` caps RLIMIT_AS (virtual address space) in the child.
        # OpenBLAS / numpy / geopandas reserve ~1-2 GB of virtual memory at
        # import even for tiny workloads (thread-local buffers proportional
        # to CPU count). 512 MB is not enough on multi-core hosts → import
        # geopandas dies with "OpenBLAS Memory allocation failed". The
        # threads are also capped to 1 in ``_run_in_subprocess`` to keep
        # the actual RSS small; that lets us raise the virtual cap safely.
        self.memory_mb = memory_mb if memory_mb is not None else settings.sandbox_memory_mb
        self.hitl_manager = hitl_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        code: str,
        input_data: dict[str, Any] | None = None,
        require_approval: bool = True,
        datasets: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Ejecutar código en el sandbox aislado.

        `datasets` (S2.4): {nombre: ruta relativa del GeoParquet exportado}. El
        runner los carga y el código los ve como `datasets["nombre"]`.
        """
        security_check = self.analyze_security(code)
        if not security_check["is_safe"]:
            return {
                "success": False,
                "error": "Security violation",
                "details": security_check["violations"],
            }

        # B7: rechazo explícito en SO sin rlimits POSIX (no ejecutar código
        # LLM con contención degradada, ni tragar el fallo de plataforma).
        # Solo aplica al backend 'subprocess' (child local): con backend
        # 'docker' la ejecución ocurre en el contenedor Linux endurecido, así
        # que el SO del host (Windows-dev) es irrelevante.
        if settings.sandbox_backend != "docker" and not SANDBOX_AVAILABLE:
            logger.error(
                "Sandbox no disponible: requiere POSIX (rlimits). "
                "SO actual=%s. Usar Linux/Docker/WSL2.", os.name,
            )
            return {
                "success": False,
                "error": (
                    "El sandbox de ejecución requiere POSIX (Linux/Docker/WSL2); "
                    "no disponible en este sistema operativo."
                ),
            }

        if require_approval and settings.hitl_enabled and self.hitl_manager:
            hitl_response = await self.hitl_manager.request_approval(
                action_type=HITLActionType.CODE_EXECUTION,
                title="Python Code Execution",
                description="Execute Python analysis code in sandbox",
                details={
                    "code": code,
                    "modules_used": security_check.get("modules_used", []),
                    "input_variables": list(input_data.keys()) if input_data else [],
                },
                risks=security_check.get("warnings", []),
                preview=code[:500],
            )
            # Allowlist, no lista de rechazos: `require_approval` es True por
            # defecto, así que el próximo llamador que omita el flag entra aquí.
            # Con la forma anterior (REJECTED + MODIFIED) un EXPIRED seguía de
            # largo y ejecutaba código sin aprobación. Hoy los dos llamadores
            # vivos pasan require_approval=False, o sea que esto es la trampa
            # esperando al tercero.
            if hitl_response.status == HITLStatus.MODIFIED:
                code = cast(str, hitl_response.modified_content)
            elif hitl_response.status != HITLStatus.APPROVED:
                logger.info(
                    "Sandbox execution not approved: %s", hitl_response.status.value
                )
                return {
                    "success": False,
                    "error": (
                        f"Execution not approved ({hitl_response.status.value}): "
                        f"{hitl_response.feedback or 'sin razón especificada'}"
                    ),
                }

        try:
            if settings.sandbox_backend == "docker":
                return await self._run_in_docker(code, input_data or {}, datasets)
            return await self._run_in_subprocess(code, input_data or {}, datasets)
        except TimeoutError:
            return {
                "success": False,
                "error": f"Sandbox timed out after {self.timeout}s",
            }
        except Exception as exc:  # noqa: BLE001 — subprocess plumbing
            logger.error(f"Sandbox plumbing error: {exc}")
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Static analysis (defence in depth)
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # Subprocess plumbing
    # ------------------------------------------------------------------
