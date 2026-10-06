"""Los hubs MCP de las organizaciones (F6, S6.2).

Cada organización con conexiones propias tiene su `McpHub` (sus servidores, sus pins en la
BD, sus herramientas registradas solo para ella). Se carga la primera vez que alguien de la
organización hace una petición, se reconstruye al dar de alta o de baja una conexión y se
revalida junto con el de la plataforma.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from geo_copilot.platform.conexiones.store import ConexionesStore, PinesEnPostgres
from geo_copilot.platform.mcp.config import McpConfig
from geo_copilot.platform.mcp.hub import McpHub, MemoryPinStore, hubs_org, instalar_hub_org

logger = logging.getLogger(__name__)


class HubsDeOrganizaciones:
    def __init__(self, store: ConexionesStore, pool: Any = None) -> None:
        self.store = store
        self._pool = pool
        self._cargadas: set[str] = set()
        self._candados: dict[str, asyncio.Lock] = {}

    def _pins(self, org_id: str) -> Any:
        return PinesEnPostgres(self._pool, org_id) if self._pool is not None else MemoryPinStore()

    async def asegurar(self, org_id: str) -> None:
        """Carga (una vez) el hub de la organización, si tiene conexiones."""
        if org_id in self._cargadas:
            return
        async with self._candados.setdefault(org_id, asyncio.Lock()):
            if org_id not in self._cargadas:
                await self.recargar(org_id)

    async def recargar(self, org_id: str) -> None:
        try:
            configs = await self.store.configs(org_id)
        except Exception:
            logger.error("[conexiones] no se pudieron leer las conexiones de %s", org_id, exc_info=True)
            instalar_hub_org(org_id, None)
            self._cargadas.discard(org_id)
            return
        self._cargadas.add(org_id)
        if not configs:
            instalar_hub_org(org_id, None)
            return
        hub = McpHub(McpConfig(servers=configs), pins=self._pins(org_id), org_id=org_id)
        instalar_hub_org(org_id, hub)
        await hub.refrescar()
        logger.info("[conexiones] %s: %d servidor(es) propios", org_id, len(configs))

    async def revalidar(self) -> None:
        for org_id, hub in hubs_org().items():
            try:
                await hub.refrescar()
            except Exception:  # tarea de fondo: una organización no tumba a las demás
                logger.warning("[conexiones] revalidación de %s falló", org_id, exc_info=True)


_instalados: HubsDeOrganizaciones | None = None


def instalar(h: HubsDeOrganizaciones | None) -> None:
    global _instalados
    _instalados = h


def hubs_actuales() -> HubsDeOrganizaciones | None:
    return _instalados
