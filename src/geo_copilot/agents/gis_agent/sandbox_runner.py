"""
Sandbox runner — executed as a subprocess by ``PythonSandbox``.

Reads a JSON payload from stdin describing the code to run, the input
variables to expose, and the resource limits. Applies POSIX rlimits
*before* loading the user code, then executes the code in a fresh
namespace and reports the result as JSON on stdout.

This file is the isolation boundary. The parent process never executes
user-supplied code in its own address space — that was the RCE risk in
the previous in-process ``exec()`` design.

Hardening applied here:

* ``resource.RLIMIT_CPU``    — CPU-seconds wall (raised SIGXCPU on overrun).
* ``resource.RLIMIT_AS``     — total virtual memory.
* ``resource.RLIMIT_FSIZE``  — max bytes written to any single file.
* ``resource.RLIMIT_NPROC``  — no fork/exec.
* ``resource.RLIMIT_NOFILE`` — small fd cap.
* working directory is a fresh tempdir; CWD is changed into it.
* a minimal ``__builtins__`` is exposed (no ``open``, ``eval`` etc.).

Wall-clock enforcement is the parent's job (``asyncio.wait_for``); the
RLIMIT_CPU here is a second line of defence against a tight CPU loop
that ignores the parent's kill signal.
"""

from __future__ import annotations

import builtins
import json
import os
import re
import sys
import tempfile
import traceback
from io import StringIO
from typing import Any

# ``resource`` es POSIX-only. En producción el runner corre dentro del
# contenedor Linux (docker/Dockerfile.sandbox), pero importarlo de forma
# condicional mantiene el módulo importable/testeable en cualquier SO
# (B7). El padre (``sandbox.py``) ya rechaza la ejecución fuera de POSIX.
resource: Any
try:
    import resource
except ImportError:  # pragma: no cover - sólo en SO no-POSIX (Windows)
    resource = None

# Builtins that are safe to expose. Anything not in this list is removed.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bin", "bool", "bytes", "bytearray", "callable",
    "chr", "complex", "dict", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "hex", "id", "int", "isinstance", "issubclass",
    "iter", "len", "list", "map", "max", "min", "next", "object", "oct",
    "ord", "pow", "print", "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "staticmethod", "classmethod", "str", "sum", "tuple",
    "type", "zip",
    # Exceptions the user may raise/catch
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "StopIteration", "ArithmeticError", "ZeroDivisionError",
    "OverflowError", "RuntimeError", "NotImplementedError",
)


def _safe_builtins() -> dict:
    safe: dict = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES if hasattr(builtins, name)}
    # __import__ is needed so ``import geopandas`` etc. work. We do NOT
    # restrict it here — the AST allowlist in the parent already gated the
    # imports before this subprocess was spawned, and the subprocess is the
    # isolation boundary. If a module-level side effect leaks out it cannot
    # reach the parent.
    safe["__import__"] = builtins.__import__
    return safe


def _json_safe(value: object) -> bool:
    try:
        json.dumps(value, default=str)
        return True
    except (TypeError, ValueError):
        return False


def _sanitize_nonfinite(value):
    """Reemplaza floats no-finitos (NaN/Inf) por ``None`` recursivamente.

    JSON no admite NaN/Inf y el serializador de la API (Starlette,
    ``allow_nan=False``) reventaría con 500 al reenviarlos al cliente. Caso
    real del canal analítico: ``scipy.stats.pearsonr`` / ``DataFrame.corr()``
    sobre una columna constante producen ``nan``. Saneamos en el borde de
    serialización del runner como red de seguridad global (el scaffolding del
    agente ya sanea su ``analysis_output``, esto cubre cualquier otra fuente).
    """
    if isinstance(value, bool):
        return value  # bool es subclase de int; no es un float a sanear
    if isinstance(value, float):
        return value if (value == value and value not in (float("inf"), float("-inf"))) else None
    if isinstance(value, dict):
        return {k: _sanitize_nonfinite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_nonfinite(v) for v in value]
    return value


def _apply_limits(cpu_seconds: int, memory_mb: int, output_bytes: int) -> None:
    """Apply rlimits to the current process. Failures are non-fatal: on
    platforms where a limit is not supported we still want the user code to
    run (the parent's wall-clock timeout remains the primary control)."""
    if resource is None:  # pragma: no cover - SO no-POSIX
        return

    def _try(rlimit: int, soft: int, hard: int | None = None) -> None:
        try:
            resource.setrlimit(rlimit, (soft, hard if hard is not None else soft))
        except (ValueError, OSError):
            pass

    _try(resource.RLIMIT_CPU, cpu_seconds)
    _try(resource.RLIMIT_AS, memory_mb * 1024 * 1024)
    _try(resource.RLIMIT_FSIZE, output_bytes)
    # NPROC cuenta TODOS los procesos/threads del usuario. 64 era demasiado bajo
    # para el Python científico con threading (numba/joblib de esda/libpysal
    # spawean threads para JIT → clone() fallaba con EAGAIN/BlockingIOError y el
    # análisis espacial reventaba). 512 sigue frenando fork-bombs pero permite el
    # threading legítimo de las librerías (que además se limita con NUMBA/OMP=1).
    _try(resource.RLIMIT_NPROC, 512)
    # NOFILE también un poco más alto: las libs abren varios fds (caches, mmaps).
    _try(resource.RLIMIT_NOFILE, 256)
    # Disable core dumps so a crash cannot leak in-memory secrets to disk.
    _try(resource.RLIMIT_CORE, 0)


# S2.4: rutas que emite DatasetStore.export_geoparquet (`ws_<hex>/ds_<hex>.parquet`).
# Nada más se lee: ni rutas absolutas ni `..`.
_DATASET_PATH_RE = re.compile(r"^ws_[0-9a-f]{16}/ds_[0-9a-f]{16}\.parquet$")


def _cargar_datasets(root: str, spec: dict) -> dict:
    """Carga los datasets pedidos como GeoDataFrames (código de confianza).

    El código del LLM recibe solo el dict ya cargado: ni rutas ni un cargador
    que pudiera apuntar a los archivos de otras sesiones del mismo volumen.
    """
    import geopandas as gpd

    cargados: dict = {}
    for nombre, relativa in spec.items():
        if not isinstance(relativa, str) or not _DATASET_PATH_RE.match(relativa):
            raise ValueError(f"ruta de dataset no válida para {nombre!r}")
        cargados[str(nombre)] = gpd.read_parquet(os.path.join(root, relativa))
    return cargados


def _run(payload: dict) -> dict:
    code: str = payload["code"]
    input_data: dict = payload.get("input_data") or {}
    cpu_seconds: int = int(payload.get("cpu_seconds", 30))
    memory_mb: int = int(payload.get("memory_mb", 512))
    output_bytes: int = int(payload.get("output_bytes", 1_000_000))

    # Move into a tempdir and apply rlimits before user code runs.
    tmp = tempfile.mkdtemp(prefix="geo_copilot_sandbox_")
    os.chdir(tmp)
    _apply_limits(cpu_seconds, memory_mb, output_bytes)

    namespace: dict = {"__builtins__": _safe_builtins(), **input_data}
    reservados = set(input_data)
    if payload.get("datasets"):
        try:
            namespace["datasets"] = _cargar_datasets(payload.get("datasets_root") or "", payload["datasets"])
        except Exception as exc:  # noqa: BLE001 — se reporta como fallo del turno, no del runner
            return {"success": False, "error": f"no se pudieron cargar los datasets: {type(exc).__name__}: {exc}"}
        reservados.add("datasets")

    captured = StringIO()
    real_stdout, sys.stdout = sys.stdout, captured
    try:
        exec(compile(code, "<sandbox>", "exec"), namespace)
        # Extract JSON-serializable user-defined names. Input keys are kept
        # out so the parent can tell what the code actually produced.
        results: dict = {}
        for name, value in namespace.items():
            if name.startswith("_") or name in reservados or name == "__builtins__":
                continue
            if _json_safe(value):
                results[name] = value
        out = captured.getvalue()
        if len(out) > output_bytes:
            out = out[:output_bytes] + "\n... (output truncated)"
        return {"success": True, "output": out, "results": _sanitize_nonfinite(results)}
    except SystemExit as exc:
        return {"success": False, "error": f"SystemExit({exc.code})", "output": captured.getvalue()}
    except BaseException as exc:  # noqa: BLE001 — sandboxed code may raise anything
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "output": captured.getvalue(),
        }
    finally:
        sys.stdout = real_stdout


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps({"success": False, "error": f"bad payload: {exc}"}))
        return 2
    result = _run(payload)
    sys.stdout.write(json.dumps(result, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
