"""El PINNING de las herramientas MCP (§3.6.2): la huella de lo que el LLM lee de una tool
(descripción, esquema, anotaciones) y dónde se guarda. Si cambia, la tool se deshabilita hasta
que un administrador la re-apruebe (rug pull).

Salió de `hub.py` (F4 del plan de calidad: 1.097 líneas), tal cual.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


class PinStore(Protocol):
    async def get(self, clave: str) -> str | None: ...
    async def set(self, clave: str, valor: str, *, por: str = "") -> None: ...


class MemoryPinStore:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    async def get(self, clave: str) -> str | None:
        return self._d.get(clave)

    async def set(self, clave: str, valor: str, *, por: str = "") -> None:
        self._d[clave] = valor


class RedisPinStore:
    """Pins persistentes (sobreviven reinicios): un rug pull se detecta aunque la app reinicie."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._r = aioredis.Redis.from_url(url, decode_responses=True)

    async def get(self, clave: str) -> str | None:
        valor = await self._r.get(f"mcp:pin:{clave}")
        # decode_responses=True ya da str; los tipos de redis-py 7 admiten bytes (CI)
        return valor.decode() if isinstance(valor, bytes) else valor

    async def set(self, clave: str, valor: str, *, por: str = "") -> None:
        await self._r.set(f"mcp:pin:{clave}", valor)


def huella_tool(tool: Any) -> str:
    """Hash de lo que el LLM lee de una tool: descripción, esquema y anotaciones."""
    anot = tool.annotations.model_dump(mode="json") if getattr(tool, "annotations", None) else None
    doc = json.dumps({"d": tool.description or "", "s": tool.inputSchema, "a": anot},
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(doc.encode()).hexdigest()
