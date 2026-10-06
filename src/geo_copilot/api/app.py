"""
Aplicación FastAPI principal.

Configura la aplicación, rutas, middleware y eventos de ciclo de vida.
"""

import re
import uuid
from collections.abc import MutableMapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from geo_copilot.api.dependencies import get_app_state, reset_app_state
from geo_copilot.api.limiter import limiter
from geo_copilot.api.models import ErrorResponse, HealthResponse
from geo_copilot.api.routes import (
    approval_router,
    discovery_router,
    metadata_router,
    proxy_router,
    query_router,
    session_router,
    tiles_router,
)
from geo_copilot.api.websocket import websocket_endpoint
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger, setup_logging

logger = get_logger(__name__)

# Versión de la API
API_VERSION = "0.1.0"

# Rate limiter importado de limiter.py (singleton centralizado)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación."""
    # API-3: configurar logging respetando settings.log_level/log_format.
    # Antes esta llamada no existía → los settings se ignoraban y el
    # JSONFormatter nunca se usaba.
    cfg = get_settings()
    setup_logging(level=cfg.log_level, format_type=cfg.log_format)

    # S1 (D2): no arrancar en producción sin API key — de lo contrario
    # toda la superficie protegida queda abierta sin credenciales.
    # SEC-ERROR-LEAK: ni en producción con debug=True (filtraría trazas/BD).
    # R0.5 (AUD-03): ni con el sandbox en-proceso, cuyo filtro AST es evadible.
    from geo_copilot.api.auth import (
        enforce_production_auth,
        enforce_production_debug,
        enforce_sandbox_backend,
    )
    enforce_production_auth(cfg)
    enforce_production_debug(cfg)
    enforce_sandbox_backend(cfg)

    logger.info("Starting GEO_COPILOT API...")
    # S1.2: el núcleo emite su progreso a una interfaz; aquí se conecta al WS.
    from geo_copilot.api.websocket import WebSocketEventSink
    from geo_copilot.platform import events
    events.set_sink(WebSocketEventSink())

    app_state = get_app_state()
    try:
        await app_state.initialize()
    except Exception as exc:
        # No silenciar fallos de arranque: si la inicialización falla,
        # el proceso debe morir en vez de servir tráfico en estado roto.
        logger.error(f"Startup failed: {exc}", exc_info=True)
        raise
    logger.info("GEO_COPILOT API started successfully")

    yield

    logger.info("Shutting down GEO_COPILOT API...")
    await app_state.shutdown()
    reset_app_state()
    logger.info("GEO_COPILOT API shut down successfully")


#: Un X-Request-ID del cliente se acepta si parece un id (no texto libre que acabe en los logs).
_ID_VALIDO = re.compile(r"[A-Za-z0-9._-]{8,64}")
#: Los métodos que pueden ser etiqueta de la métrica HTTP (el resto cuenta como «OTRO»).
_METODOS_HTTP = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})


#: Ver create_app: la telemetría nativa de FastAPI, apagada entera (la lleva platform/observabilidad).
TELEMETRIA_FASTAPI_APAGADA = {"auto_configure": False, "tracing": False, "metrics": False, "logs": False,
                              "operation_spans": False}


def plantilla_de_ruta(scope: MutableMapping[str, Any]) -> str:
    """La ruta como plantilla (``/api/v1/session/{session_id}``), nunca la URL con el id.

    FastAPI ≥0.14x deja en ``scope["route"].path`` solo el tramo del router (``/session/{session_id}``,
    sin el prefijo con que se incluyó): la plantilla se reconstruye con el prefijo real, que es lo que
    de la URL no cubre la ruta. Igual con las versiones que ya traían la ruta completa.
    """
    ruta = scope.get("route")
    plantilla: str | None = getattr(ruta, "path", None)
    if not plantilla:
        return "sin_ruta"
    formato: str = getattr(ruta, "path_format", plantilla)
    try:
        sufijo = formato.format(**dict(scope.get("path_params") or {}))
    except (KeyError, IndexError, ValueError):
        return plantilla
    camino: str = scope.get("path") or ""
    if sufijo and camino.endswith(sufijo):
        return camino[: len(camino) - len(sufijo)] + plantilla
    return plantilla


def create_app() -> FastAPI:  # noqa: PLR0915
    """
    Crear y configurar la aplicación FastAPI.

    Returns:
        Aplicación FastAPI configurada
    """
    config = get_settings()

    # R0.12 (AUD-110): mismo predicado que usan los guards de arranque.
    from geo_copilot.api.auth import is_production_environment

    _is_prod = is_production_environment(config)

    app = FastAPI(
        title="GEO_COPILOT API",
        description="""
        API REST para el copiloto geoespacial multiagente.

        ## Funcionalidades

        - **Query**: Procesar consultas en lenguaje natural sobre datos geoespaciales
        - **Approval**: Sistema HITL para aprobar SQL y código antes de ejecutar
        - **Session**: Gestión de sesiones de conversación
        - **Metadata**: Información sobre entidades, workflows e intenciones disponibles
        - **WebSocket**: Streaming de respuestas en tiempo real

        ## Flujo típico

        1. Crear sesión (`POST /session/`)
        2. Enviar consulta (`POST /query/`)
        3. Si requiere aprobación, revisar y aprobar (`POST /approval/{id}`)
        4. Recibir resultados con visualizaciones
        """,
        version=API_VERSION,
        # R0.12 (auditoría 2026-07-26, AUD-110): la documentación interactiva
        # sólo fuera de producción. Publicaba el inventario completo de
        # endpoints y esquemas —incluidos los de approval/HITL— a cualquiera
        # que alcanzase el puerto, sin credencial.
        docs_url=None if _is_prod else "/docs",
        redoc_url=None if _is_prod else "/redoc",
        openapi_url=None if _is_prod else "/openapi.json",
        lifespan=lifespan,
        # F7 (E7.5): FastAPI ≥0.14x trae telemetría propia que, con OTEL_EXPORTER_OTLP_ENDPOINT,
        # AÑADE otro exportador a nuestro TracerProvider (cada span llegaba dos veces), manda
        # métricas y logs a un colector que no los acepta (404 en bucle) y exporta como logs los
        # mensajes y trazas de las excepciones, sin el filtro de ?token= de platform/observabilidad.
        # La telemetría la lleva platform/observabilidad; las versiones viejas ignoran el argumento.
        telemetry=TELEMETRIA_FASTAPI_APAGADA,
    )

    # Configurar CORS de forma segura
    # IMPORTANTE: credentials=True NO es compatible con allow_origins=["*"]
    # Esto es una vulnerabilidad de seguridad documentada en OWASP
    if config.cors_allow_all:
        logger.warning(
            "CORS allow_all is enabled - credentials will be disabled for security. "
            "Configure cors_origins explicitly for production."
        )
        cors_origins = ["*"]
        allow_credentials = False  # NUNCA usar credentials con wildcard
    else:
        cors_origins = config.cors_origins if config.cors_origins else ["http://localhost:5173"]
        allow_credentials = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        # DELETE/PATCH son necesarios: el router de sesión expone
        # DELETE /session/{id} y PATCH /session/{id}/preferences.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Session-ID"],
    )

    # Configurar rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, cast(Any, _rate_limit_exceeded_handler))
    app.add_middleware(SlowAPIMiddleware)
    logger.info(f"Rate limiting configurado: {config.rate_limit_requests} requests/minuto")

    # Registrar rutas
    app.include_router(query_router, prefix="/api/v1")
    app.include_router(approval_router, prefix="/api/v1")
    app.include_router(session_router, prefix="/api/v1")
    app.include_router(metadata_router, prefix="/api/v1")
    app.include_router(discovery_router, prefix="/api/v1")
    app.include_router(proxy_router, prefix="/api/v1")
    app.include_router(tiles_router, prefix="/api/v1")
    # F2 (E2.4): datasets del workspace de la sesión (restaurar capas al recargar).
    from geo_copilot.api.routes.workspace import router as workspace_router

    app.include_router(workspace_router, prefix="/api/v1")
    # F3: servidores MCP conectados (estado + re-aprobación de tools).
    from geo_copilot.api.routes.connections import router as connections_router

    app.include_router(connections_router, prefix="/api/v1")
    # FH.8: acciones contextuales del mapa (capacidades aplicables a lo señalado).
    from geo_copilot.api.routes.acciones import router as acciones_router

    app.include_router(acciones_router, prefix="/api/v1")
    # FH.11: proyectos (guardar y reabrir el mapa con su conversación).
    from geo_copilot.api.routes.proyectos import router as proyectos_router

    app.include_router(proyectos_router, prefix="/api/v1")
    # F6: configuración de inicio de sesión (pública) y el usuario actual.
    from geo_copilot.api.routes.identidad import router as identidad_router

    app.include_router(identidad_router, prefix="/api/v1")
    # F6 (S6.4): registro de auditoría de la organización (administración).
    from geo_copilot.api.routes.auditoria import router as auditoria_router

    app.include_router(auditoria_router, prefix="/api/v1")

    # WebSocket
    app.websocket("/ws/{session_id}")(websocket_endpoint)

    # Health check. API-4: devuelve 503 si componentes críticos faltan.
    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["Health"],
        summary="Health check",
        description="Check the health status of the API and its components.",
    )
    async def health_check() -> JSONResponse:
        app_state = get_app_state()

        components = {
            "api": "healthy",
            "semantic_layer": "healthy" if app_state.semantic_layer else "unavailable",
            "llm_client": "healthy" if app_state.llm_client else "unavailable",
            "orchestrator": "healthy" if app_state.orchestrator else "unavailable",
            "database": "healthy" if app_state.db_pool else "unavailable",
        }

        # La BD y el LLM son críticos: el grafo no procesa nada sin ellos.
        is_healthy = bool(app_state.db_pool) and bool(app_state.llm_client)
        body = HealthResponse(
            status="healthy" if is_healthy else "degraded",
            version=API_VERSION,
            components=components,
            timestamp=datetime.now(UTC),
        )
        return JSONResponse(
            status_code=200 if is_healthy else 503,
            content=body.model_dump(mode="json"),
        )

    # Root endpoint
    @app.get("/", tags=["Root"])
    async def root():
        """Endpoint raíz con información básica."""
        return {
            "name": "GEO_COPILOT API",
            "version": API_VERSION,
            "docs": "/docs",
            "health": "/health"
        }

    # Manejador de errores global
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Manejador de excepciones global."""
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="Internal server error",
                detail=str(exc) if config.debug else None,
                code="INTERNAL_ERROR"
            ).model_dump(mode="json")
        )

    # Middleware de logging + correlation-id (API-3 / API-8).
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Logueo HTTP con request-id propagable.

        Toma el ``X-Request-ID`` del cliente si viene; si no, genera uno
        nuevo. Lo devuelve en la respuesta para que el cliente lo encadene
        con sus propios logs.
        """
        start_time = datetime.now(UTC)
        # Un id por petición, aceptado del cliente solo si es un id razonable (no texto arbitrario
        # que acabe en los logs).
        entrante = request.headers.get("X-Request-ID") or ""
        request_id = entrante if _ID_VALIDO.fullmatch(entrante) else uuid.uuid4().hex
        # Disponible para handlers vía request.state.request_id
        request.state.request_id = request_id
        # F7 (S7.3): y para TODAS las líneas de log de esta petición (y los avisos a los MCP)
        from geo_copilot.platform import observabilidad

        ficha = observabilidad.fijar_correlacion(request_id)
        # F7 (auditoría): el método va a una etiqueta de la métrica y lo elige el cliente sin
        # autenticar (h11 acepta cualquier token): fuera del conjunto conocido, «OTRO». Si no,
        # cada método inventado creaba series nuevas sin tope.
        metodo = request.method if request.method in _METODOS_HTTP else "OTRO"
        try:
            try:
                response = await call_next(request)
            except Exception:
                # F7 (auditoría): una excepción no capturada la convierte en 500 el manejador
                # global, que Starlette pone POR FUERA de este middleware: sin esto, ese 500 no
                # llegaba a geo_http_segundos y ErroresHttpAltos no veía justo las caídas.
                duration = (datetime.now(UTC) - start_time).total_seconds()
                ruta = plantilla_de_ruta(request.scope)
                observabilidad.registrar_http(metodo, ruta, 500, duration)
                logger.info(
                    f"{metodo} {request.url.path} - Status: 500 (excepción no capturada) "
                    f"- Duration: {duration:.3f}s - RequestId: {request_id}"
                )
                raise

            duration = (datetime.now(UTC) - start_time).total_seconds()
            logger.info(
                f"{metodo} {request.url.path} "
                f"- Status: {response.status_code} "
                f"- Duration: {duration:.3f}s "
                f"- RequestId: {request_id}"
            )
            # La ruta como plantilla (/session/{id}), no la URL: si no, cada sesión sería una serie
            ruta = plantilla_de_ruta(request.scope)
            observabilidad.registrar_http(metodo, ruta, response.status_code, duration)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            observabilidad._correlacion.reset(ficha)

    # F7 (S7.3): métricas para Prometheus. Solo por la red interna: nginx no la expone y en
    # producción la app no publica puerto.
    @app.get("/metrics", include_in_schema=False)
    async def metricas() -> Response:
        from geo_copilot.platform import observabilidad

        cuerpo, tipo = observabilidad.exponer_metricas()
        return Response(content=cuerpo, media_type=tipo)

    from geo_copilot.platform import observabilidad

    observabilidad.iniciar(app)
    return app


# Instancia de la aplicación para ASGI
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "geo_copilot.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
