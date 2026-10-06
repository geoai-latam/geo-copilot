"""
Dependencias para inyección en FastAPI.

Sistema simplificado usando GeoAgentGraph (LangGraph A2A).
"""

import asyncio
from typing import Any, cast

import asyncpg

from geo_copilot.core.config import get_settings
from geo_copilot.core.llm_client import LLMClient
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.conversation import ConversationManager
from geo_copilot.orchestrator.graph import GeoAgentGraph
from geo_copilot.security.hitl import HITLManager
from geo_copilot.semantic.layer import SemanticLayer

logger = get_logger(__name__)

# F4: el ciclo de vida de los servicios de fondo vive en su módulo (mixin).
from geo_copilot.api.ciclo_vida import CicloVidaMixin


async def hitl_websocket_notification(session_id: str, approval_data: dict) -> None:
    """
    Callback para enviar notificaciones HITL via WebSocket.

    Esta función se pasa al HITLManager para que pueda notificar
    al frontend cuando hay una nueva aprobación pendiente.
    """
    # Import here to avoid circular imports
    from geo_copilot.api.websocket import send_approval_request
    await send_approval_request(session_id, approval_data)


class AppState(CicloVidaMixin):
    """Estado global de la aplicación."""

    def __init__(self) -> None:
        self._config: Any = None  # Settings, tras initialize()
        self._llm_client: LLMClient | None = None
        self._db_pool: asyncpg.Pool | None = None
        self._semantic_layer: SemanticLayer | None = None
        self._agent_graph: GeoAgentGraph | None = None  # LangGraph orchestrator
        self._conversation_manager: ConversationManager | None = None
        self._hitl_manager: HITLManager | None = None
        # S2.1: workspace espacial (None sin BD) y la purga periódica.
        self._dataset_store: Any = None
        self._purga_task: asyncio.Task | None = None
        # F3: hub de servidores MCP y su revalidación periódica.
        self._mcp_hub: Any = None
        self._mcp_task: asyncio.Task | None = None
        self._initialized = False
        # API-2: lock que serializa la inicialización. Sin él, las primeras
        # peticiones concurrentes corrían una carrera que podía crear pools
        # asyncpg y clientes LLM duplicados antes de que ``_initialized``
        # se pusiera en True.
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Inicializar componentes del sistema."""
        if self._initialized:
            return

        async with self._init_lock:
            # Double-check: otro waiter pudo haber inicializado mientras
            # esperábamos el lock.
            if self._initialized:
                return
            await self._do_initialize()

    async def _do_initialize(self) -> None:  # noqa: C901, PLR0912, PLR0915
        logger.info("Initializing GEO_COPILOT components...")

        # Configuración
        self._config = get_settings()

        # Database Pool (requerido para schema discovery). Pool sizes y
        # timeout vienen del config, no hardcodeados (API-14).
        try:
            db_url = self._config.database_url.get_secret_value()
            self._db_pool = await asyncpg.create_pool(
                db_url,
                min_size=self._config.db_pool_min_size,
                max_size=self._config.db_pool_max_size,
                command_timeout=self._config.db_query_timeout,
            )
            # Log only the host suffix — never the full URL (would leak the
            # credentials). If the URL has no ``@`` we say "configured" and
            # nothing more.
            db_info = db_url.split('@')[-1] if '@' in db_url else 'configured'
            logger.info(f"Database pool created: {db_info}")
        except Exception as e:
            # Captura amplia a propósito: arranque: DSN/red/asyncpg fallan de muchas formas; la
            # criticidad se decide abajo (S9).
            logger.error(f"Database pool FAILED: {e}", exc_info=True)
            self._db_pool = None

        # LLM Client (requerido para agentes)
        try:
            self._llm_client = LLMClient.from_settings(self._config)
            logger.info(f"LLM client: {self._config.llm_provider}/{self._config.llm_model}")
        except Exception as e:
            # Captura amplia a propósito: SDK del proveedor puede lanzar cualquier cosa; S9 aborta
            # abajo si queda None.
            logger.error(f"LLM client FAILED: {e}", exc_info=True)
            self._llm_client = None

        # Semantic Layer — el YAML actúa como OVERRIDES, no como fuente
        # primaria. Después de cargarlo intentamos hidratar desde la BD
        # real (introspector) para que el sistema conozca las tablas
        # que el usuario realmente tiene.
        try:
            self._semantic_layer = SemanticLayer(self._config.semantic_layer_path)
            yaml_count = len(self._semantic_layer.list_entities())
            logger.info(f"Semantic layer YAML loaded: {yaml_count} entities (overrides)")

            # Hidratar con tablas REALES de la BD si hay conexión.
            if self._db_pool is not None:
                try:
                    hydrated = await self._semantic_layer.hydrate_from_database(
                        self._db_pool,
                    )
                    if hydrated > 0:
                        logger.info(
                            f"Semantic layer hydrated from DB: {hydrated} entities "
                            f"(replaced {yaml_count} YAML defaults)"
                        )
                    else:
                        logger.warning(
                            "Semantic layer hydration found 0 geo tables — "
                            "verify the connected DB has PostGIS tables with geometry columns"
                        )
                except Exception as exc:
                    # Captura amplia a propósito: introspección de una BD arbitraria; degradar a los
                    # overrides YAML.
                    logger.error(
                        f"Semantic layer hydration FAILED: {exc} — "
                        f"falling back to YAML defaults",
                        exc_info=True,
                    )
        except Exception as e:  # capa semántica opcional (YAML/validación); sin ella el arranque sigue
            logger.warning(f"Semantic layer not loaded: {e}", exc_info=True)
            self._semantic_layer = None

        # F7 (S7.1): estado compartido. En producción Redis es OBLIGATORIO (aborta si falta);
        # en desarrollo, si no está, todo queda en la memoria del proceso (un solo worker).
        from geo_copilot.api import estado as estado_compartido

        self._redis = await estado_compartido.iniciar(self._config)

        # Gestores. Fase 6 #11: el backend de sesiones se elige según
        # ``settings.session_backend`` ('memory' | 'redis'). F7: en producción, sin fallback
        # silencioso a memoria (lo decide `_build_session_store`).
        self._conversation_manager = ConversationManager(
            max_sessions=self._config.session_max_count,
            session_timeout_minutes=self._config.session_timeout_minutes,
            store=_build_session_store(self._config),
        )
        self._hitl_manager = HITLManager(
            notification_callback=hitl_websocket_notification
        )
        # F7: respuestas que entran por otro worker, y reanudación tras un reinicio
        self._hitl_manager.conectar_bus()
        self._hitl_manager.set_reanudador(estado_compartido.reanudar_turno)
        from geo_copilot.api.websocket import connection_manager

        connection_manager.conectar_bus(self._redis)

        # GeoAgentGraph (LangGraph A2A orchestrator)
        if self._llm_client:
            try:
                self._agent_graph = GeoAgentGraph(
                    llm_client=self._llm_client,
                    hitl_manager=self._hitl_manager,
                    db_pool=self._db_pool,
                    semantic_layer=self._semantic_layer,
                )
                logger.info("GeoAgentGraph initialized (LangGraph A2A)")
            except Exception as e:
                # Captura amplia a propósito: compilar el grafo toca LangGraph y todos los agentes;
                # se registra y sigue.
                logger.error(f"GeoAgentGraph FAILED: {e}", exc_info=True)
                self._agent_graph = None
        else:
            logger.warning("GeoAgentGraph not available (requires LLM client)")
            self._agent_graph = None

        # Antes inicializábamos un catálogo ArcGIS local con 412 servicios
        # pre-indexados. Lo eliminamos: ahora todo el discovery usa
        # ArcGIS Hub Open Data global (opendata.arcgis.com/api/v3/search)
        # vía DiscoveryAgent, que es tiempo-real y mucho más amplio.

        # S9: no marcar inicializado si un componente CRÍTICO falló. El
        # cliente LLM es la pieza central (sin él no hay agentes ni grafo);
        # antes se tragaba el fallo, se dejaba en None y se marcaba
        # ``_initialized=True``, de modo que la app "booteaba sana" sirviendo
        # tráfico roto y el lifespan nunca moría. Al lanzar aquí, el lifespan
        # propaga y el proceso no arranca; además ``_initialized`` queda False
        # para permitir un reintento posterior (no se cachea un estado roto).
        if self._llm_client is None:
            raise RuntimeError(
                "Inicialización abortada: el cliente LLM no se pudo construir. "
                "Verifica LLM_PROVIDER y las credenciales."
            )

        # Igual que con el LLM (S9), en PRODUCCIÓN la BD es crítica: sin pool no
        # hay schema discovery, ni SQL, ni hidratación de la capa semántica.
        # Preferimos NO arrancar a botear "sano" con db_pool=None y fallar pieza
        # por pieza en runtime. En desarrollo/test (debug=True) se permite
        # arrancar sin BD real (la suite no levanta PostGIS).
        if self._db_pool is None and not self._config.debug:
            raise RuntimeError(
                "Inicialización abortada: no se pudo crear el pool de base de "
                "datos. Verifica DATABASE_URL y la conectividad a PostGIS."
            )

        # F6: identidad (validador OIDC, tickets del WS y dueño de las sesiones en PostGIS).
        from geo_copilot.platform.identidad import servicio as identidad

        ident = identidad.construir(self._config, self._db_pool)
        if self._db_pool is not None and not await identidad.esquema_listo(self._db_pool):
            from geo_copilot.api.auth import is_production_environment

            if is_production_environment(self._config):
                raise RuntimeError(
                    "Inicialización abortada: falta el esquema `plataforma` (dueños de sesión, "
                    "conexiones, auditoría). Aplica sh docker/migrations/2026-09-28_plataforma.sh")
            logger.warning("[identidad] la BD no tiene el esquema `plataforma`: dueños de sesión EN MEMORIA "
                           "(se pierden al reiniciar). Aplica docker/migrations/2026-09-28_plataforma.sh")
            ident.propiedad = identidad.PropiedadEnMemoria()
        identidad.instalar(ident)
        # F6 (S6.4): auditoría en el mismo esquema (en memoria si falta, con el mismo aviso)
        from geo_copilot.platform import auditoria

        esquema = self._db_pool is not None and isinstance(ident.propiedad, identidad.PropiedadEnPostgres)
        auditoria.instalar(auditoria.AuditoriaEnPostgres(self._db_pool) if esquema else auditoria.AuditoriaEnMemoria())

        # S2.1: workspace de la sesión. Sin BD no hay workspace (la app sigue
        # funcionando con GeoJSON en memoria, como antes de F2).
        if self._db_pool is not None:
            from geo_copilot.platform.workspace import DatasetStore
            from geo_copilot.platform.workspace.context import instalar_store

            self._dataset_store = DatasetStore(self._db_pool)
            instalar_store(self._dataset_store)
            self._purga_task = asyncio.create_task(self._purgar_periodicamente())

        await self._iniciar_mcp()
        self._iniciar_conexiones_org(ident)

        self._initialized = True

        logger.info("GEO_COPILOT initialized successfully")




    @property
    def mcp_hub(self):  # -> McpHub | None
        return self._mcp_hub


    @property
    def dataset_store(self):  # -> DatasetStore | None
        return self._dataset_store


    @property
    def config(self):
        return self._config

    @property
    def llm_client(self) -> LLMClient | None:
        return self._llm_client

    @property
    def semantic_layer(self) -> SemanticLayer | None:
        return self._semantic_layer

    @property
    def agent_graph(self) -> GeoAgentGraph | None:
        return self._agent_graph

    @property
    def conversation_manager(self) -> ConversationManager:
        return cast(ConversationManager, self._conversation_manager)

    @property
    def hitl_manager(self) -> HITLManager:
        return cast(HITLManager, self._hitl_manager)

    @property
    def is_initialized(self) -> bool:
        return bool(self._initialized)

    @property
    def db_pool(self):
        return self._db_pool

    @property
    def orchestrator(self) -> GeoAgentGraph | None:
        """Alias de agent_graph para compatibilidad."""
        return self._agent_graph


# Singleton
_app_state: AppState | None = None


def get_app_state() -> AppState:
    """Obtener estado de la aplicación."""
    global _app_state
    if _app_state is None:
        _app_state = AppState()
    return _app_state


def reset_app_state() -> None:
    """Resetear estado (para testing)."""
    global _app_state
    _app_state = None


# Dependencias FastAPI
async def get_agent_graph() -> GeoAgentGraph | None:
    """Obtener el grafo de agentes (LangGraph)."""
    state = get_app_state()
    if not state.is_initialized:
        await state.initialize()
    return state.agent_graph


async def get_conversation_manager() -> ConversationManager:
    """Obtener gestor de conversaciones."""
    state = get_app_state()
    if not state.is_initialized:
        await state.initialize()
    return state.conversation_manager


async def get_hitl_manager() -> HITLManager:
    """Obtener gestor HITL."""
    state = get_app_state()
    if not state.is_initialized:
        await state.initialize()
    return state.hitl_manager


async def get_db_pool():
    """Obtener pool de base de datos."""
    state = get_app_state()
    if not state.is_initialized:
        await state.initialize()
    return state.db_pool


# NOTE: el sistema usa ``AppState`` (más arriba) como contenedor de
# dependencias. La capa ``ServiceContainer`` se eliminó en Fase 6 (#1)
# porque sus servicios se registraban pero nunca se resolvían: DI
# ceremonial sin consumidores reales.


def _build_session_store(config):
    """Construir el ``SessionStore`` según ``config.session_backend``.

    Fase 6 #11. Encapsula la decisión memory/redis y el fallback seguro
    a in-memory si Redis no está disponible o la dependencia ``redis``
    no está instalada.
    """
    from geo_copilot.orchestrator.sessions import InMemorySessionStore

    backend = (config.session_backend or "memory").lower()
    from geo_copilot.api.estado import EstadoCompartidoNoDisponible, redis_requerido

    if backend != "redis" and redis_requerido(config):
        raise EstadoCompartidoNoDisponible(
            f"Inicialización abortada: producción exige SESSION_BACKEND=redis (hay {backend!r}): en memoria "
            "las sesiones se pierden al reiniciar y cada worker ve solo las suyas")
    if backend == "memory":
        return InMemorySessionStore(
            session_timeout_minutes=config.session_timeout_minutes,
        )

    if backend == "redis":
        try:
            from geo_copilot.orchestrator.sessions import RedisSessionStore
            store = RedisSessionStore(
                redis_url=config.redis_url,
                session_timeout_minutes=config.session_timeout_minutes,
            )
            # Smoke test: si Redis está caído, queremos descubrirlo aquí
            # y caer a in-memory en vez de fallar en cada request.
            store._client.ping()
            logger.info(f"Session store: Redis ({_sin_credenciales(str(config.redis_url))})")
            return store
        except Exception as exc:
            if redis_requerido(config):
                # F7 (S7.1): en producción, sesiones en memoria = se pierden al reiniciar y cada
                # worker ve las suyas. Mejor no arrancar.
                raise EstadoCompartidoNoDisponible(
                    f"Inicialización abortada: producción exige sesiones en Redis ({exc})") from exc
            logger.warning(
                f"Redis session backend requested but unavailable ({exc}); "
                "falling back to in-memory.",
            )
            return InMemorySessionStore(
                session_timeout_minutes=config.session_timeout_minutes,
            )

    logger.warning(
        f"Unknown session_backend={backend!r}; using in-memory."
    )
    return InMemorySessionStore(
        session_timeout_minutes=config.session_timeout_minutes,
    )


async def get_llm_client() -> LLMClient | None:
    """Get LLM client from container or AppState."""
    state = get_app_state()
    if not state.is_initialized:
        await state.initialize()
    return state.llm_client


async def get_semantic_layer() -> SemanticLayer | None:
    """Get semantic layer from container or AppState."""
    state = get_app_state()
    if not state.is_initialized:
        await state.initialize()
    return state.semantic_layer


def _sin_credenciales(url: str) -> str:
    """La URL sin usuario ni contraseña, para logs (la de Redis la llevaba en claro)."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        p = urlsplit(url)
    except ValueError:
        return "<url inválida>"
    if not (p.username or p.password):
        return url
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    return urlunsplit((p.scheme, f"***@{host}", p.path, p.query, p.fragment))
