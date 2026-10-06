"""El CICLO DE VIDA de los servicios de fondo: el hub MCP y su revalidación, las conexiones
de la organización, la purga periódica del workspace y el cierre ordenado.

Salió de `AppState` (F4 del plan de calidad: dependencies.py tenía 584 líneas), tal cual.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    import asyncpg

    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.api.dependencies")


class CicloVidaMixin:
    """El CICLO DE VIDA de los servicios de fondo: el hub MCP y su revalidación, las conexiones"""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        _config: Any
        _llm_client: LLMClient | None
        _db_pool: asyncpg.Pool | None
        _dataset_store: Any
        _purga_task: asyncio.Task[Any] | None
        _mcp_hub: Any
        _mcp_task: asyncio.Task[Any] | None

    async def _iniciar_mcp(self) -> None:
        """F3: conecta los servidores MCP del YAML y registra sus tools como capacidades.

        Un servidor caído no impide arrancar: queda "no disponible" y se reintenta
        en cada revalidación. Un YAML inválido sí se reporta alto (config rota).
        """
        from geo_copilot.platform.mcp import config as mcp_config
        from geo_copilot.platform.mcp.hub import McpHub, MemoryPinStore, RedisPinStore, instalar_hub

        cfg = mcp_config.cargar(self._config.mcp_servers_path)
        if not cfg.servers:
            return
        pins = MemoryPinStore()
        try:
            if str(getattr(self._config, "session_backend", "")).lower() == "redis":
                pins = RedisPinStore(str(self._config.redis_url))  # type: ignore[assignment]
        except Exception:  # sin Redis, los pins viven en memoria (se re-fijan al reiniciar)
            logger.warning("[mcp] pins en memoria: Redis no disponible", exc_info=True)
        self._mcp_hub = McpHub(cfg, pins=pins)
        instalar_hub(self._mcp_hub)
        await self._mcp_hub.refrescar()
        self._mcp_task = asyncio.create_task(self._revalidar_mcp())
        logger.info(f"[mcp] {len(cfg.servers)} servidor(es) registrados")

    async def _revalidar_mcp(self) -> None:
        from geo_copilot.platform.conexiones.hubs import hubs_actuales

        while True:
            await asyncio.sleep(self._config.mcp_refresh_s)
            try:
                if self._mcp_hub is not None:
                    await self._mcp_hub.refrescar()
                hubs = hubs_actuales()
                if hubs is not None:
                    await hubs.revalidar()
            except Exception:  # tarea de fondo: un fallo no mata el bucle
                logger.warning("[mcp] revalidación falló", exc_info=True)

    def _iniciar_conexiones_org(self, ident: Any) -> None:
        """F6 (S6.2): conexiones MCP de cada organización, con sus secretos cifrados.

        Sin `SECRETS_KEK` se pueden dar de alta conexiones SIN credencial; con credencial, no
        (se dice al intentarlo). En el mismo esquema que la identidad; sin él, en memoria.
        """
        from geo_copilot.platform.conexiones import hubs as hubs_org
        from geo_copilot.platform.conexiones.cifrado import CifradoNoConfigurado, Cifrador
        from geo_copilot.platform.conexiones.store import ConexionesEnMemoria, ConexionesEnPostgres
        from geo_copilot.platform.identidad import servicio as identidad

        try:
            cifrador: Cifrador | None = Cifrador.desde_entorno()
        except CifradoNoConfigurado as exc:
            cifrador = None
            logger.warning("[conexiones] %s: las conexiones de las organizaciones no podrán llevar credencial", exc)
        en_bd = self._db_pool is not None and isinstance(ident.propiedad, identidad.PropiedadEnPostgres)
        store = ConexionesEnPostgres(self._db_pool, cifrador) if en_bd else ConexionesEnMemoria(cifrador)
        hubs_org.instalar(hubs_org.HubsDeOrganizaciones(store, self._db_pool if en_bd else None))
        if self._mcp_task is None:  # sin servidores de plataforma: la revalidación es solo de las orgs
            self._mcp_task = asyncio.create_task(self._revalidar_mcp())

    async def _purgar_periodicamente(self, cada_s: float = 3600.0) -> None:
        """Borra datasets vencidos del workspace cada hora (TTL, S2.1)."""
        while True:
            await asyncio.sleep(cada_s)
            try:
                if self._dataset_store is not None:
                    await self._dataset_store.purge_expired(get_settings().workspace_export_dir)
            except Exception:  # tarea de fondo: un fallo de purga no puede matar el bucle; se reintenta en la próxima vuelta
                logger.warning("[workspace] purga de vencidos falló", exc_info=True)

    async def shutdown(self) -> None:  # noqa: C901, PLR0912
        """Limpiar recursos en orden inverso al de inicialización.

        API-6 / API-11: cierra explícitamente el cliente LLM (sesiones
        httpx subyacentes) y drena las conexiones WebSocket activas
        antes del pool de BD. Sin esto la app dejaba file descriptors
        abiertos al apagarse.
        """
        logger.info("Shutting down GEO_COPILOT...")

        if self._purga_task is not None:
            self._purga_task.cancel()
        if self._mcp_task is not None:
            self._mcp_task.cancel()
        try:
            from geo_copilot.api import estado as estado_compartido

            await estado_compartido.cerrar()
        except Exception:  # shutdown best-effort
            logger.warning("[estado] no se pudo cerrar el bus", exc_info=True)

        # 1. Cerrar WebSockets activos para que no queden a medias.
        try:
            from geo_copilot.api.websocket import connection_manager
            session_ids = list(getattr(connection_manager, "active_connections", {}).keys())
            for sid in session_ids:
                try:
                    await connection_manager.disconnect(sid)
                except Exception as exc:  # shutdown best-effort: un WS roto no debe impedir cerrar el resto  # pragma: no cover
                    logger.warning(f"Error disconnecting WS {sid}: {exc}", exc_info=True)
            if session_ids:
                logger.info(f"Closed {len(session_ids)} active WebSocket(s)")
        except Exception as exc:  # shutdown best-effort: seguir cerrando LLM y BD  # pragma: no cover
            logger.warning(f"WebSocket drain failed: {exc}", exc_info=True)

        # 2. Cerrar SDKs LLM (httpx async clients no se cierran solos).
        try:
            if self._llm_client and hasattr(self._llm_client, "_client") and self._llm_client._client:
                inner = self._llm_client._client
                if hasattr(inner, "_client") and inner._client is not None:
                    close = getattr(inner._client, "close", None)
                    if close:
                        result = close()
                        if hasattr(result, "__await__"):
                            await result
                logger.info("LLM SDK client closed")
        except Exception as exc:  # shutdown best-effort sobre SDKs de terceros  # pragma: no cover
            logger.warning(f"LLM client close failed: {exc}", exc_info=True)

        # 3. Cerrar el pool de BD.
        if self._db_pool:
            try:
                await self._db_pool.close()
                logger.info("Database pool closed")
            except Exception as exc:  # shutdown best-effort: el proceso termina igual  # pragma: no cover
                logger.warning(f"DB pool close failed: {exc}", exc_info=True)

        # F6: la identidad instalada apuntaba a este pool
        from geo_copilot.platform.identidad import servicio as identidad

        identidad.instalar(None)
        from geo_copilot.platform import auditoria
        from geo_copilot.platform.conexiones import hubs as hubs_org
        from geo_copilot.platform.mcp.hub import hubs_org as cargados
        from geo_copilot.platform.mcp.hub import instalar_hub_org

        auditoria.instalar(None)
        hubs_org.instalar(None)
        for org in cargados():
            instalar_hub_org(org, None)
        self._initialized = False
