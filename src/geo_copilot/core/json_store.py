"""Persistencia JSON atómica para estado del agente (durable entre reinicios).

Usado por EntityMemory (F3.2) y AgentMetrics (F6) para sobrevivir a un reinicio
del proceso. Escritura ATÓMICA (temp + os.replace) para que un crash a mitad de
escritura no deje el archivo corrupto. Best-effort: si el disco falla, se loguea
y se sigue en memoria (la persistencia no debe tumbar el agente).

v1 single-process (un servidor). Multi-proceso/concurrencia fuerte requeriría una
BD; queda como follow-up.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


def load_json(path: str | os.PathLike) -> dict[str, Any]:
    """Lee un dict JSON de ``path``. ``{}`` si no existe o está corrupto."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 — archivo corrupto no debe crashear
        logger.warning(f"[json_store] no se pudo leer {p}: {exc}")
        return {}


def save_json(path: str | os.PathLike, data: dict[str, Any]) -> bool:
    """Escribe ``data`` a ``path`` de forma ATÓMICA. True si tuvo éxito."""
    p = Path(path)
    tmp: str | None = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        os.replace(tmp, p)  # atómico en el mismo filesystem
        tmp = None
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, no tumbar el agente
        logger.warning(f"[json_store] no se pudo escribir {p}: {exc}")
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
