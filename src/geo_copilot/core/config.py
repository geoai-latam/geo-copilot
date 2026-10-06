"""
Configuración centralizada del sistema GEO_COPILOT.
"""

import os
import tempfile
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings

from geo_copilot.core.config_grupos import ConfigAutonomia, ConfigPresentacion
from geo_copilot.core.constants import (
    DefaultTimeouts,
)


class Settings(ConfigAutonomia, ConfigPresentacion, BaseSettings):
    """Configuración del sistema cargada desde variables de entorno."""

    # Aplicación
    app_name: str = "GEO_COPILOT"
    app_version: str = "0.1.0"
    debug: bool = False
    # Auditoría 2026-09-08 (§3, §7 punto 8): el DEFAULT se invierte a
    # "production". Este campo no es una etiqueta: `api/auth.py` lo usa como
    # el interruptor maestro de cuatro controles, y con el default anterior
    # ("development") la OMISIÓN de la variable los apagaba todos a la vez —
    # API sin autenticación (`enforce_production_auth` no exige `API_KEY`),
    # `/docs`+`/redoc`+`/openapi.json` publicados (`app.py:122-124`),
    # `SANDBOX_BACKEND=subprocess` permitido para ejecutar el código que
    # escribe el LLM dentro del proceso de la API (`enforce_sandbox_backend`)
    # y WebSocket sin token (`validate_websocket_auth`). Olvidar una variable
    # tiene que fallar del lado seguro, no del cómodo.
    # Para desarrollo se DECLARA: `ENVIRONMENT=development` está en
    # `.env.example` (y la suite lo fija en `tests/conftest.py`), así que
    # `cp .env.example .env` sigue dando el quickstart de siempre.
    environment: str = Field(
        default="production",
        description=(
            "Entorno de ejecución. Cualquier valor fuera de "
            "{development, dev, local, test, testing} se trata como producción "
            "(ver api/auth.py:_NON_PROD_ENVIRONMENTS). El default es el seguro: "
            "si la variable falta, la app exige API_KEY, oculta /docs y rehúsa "
            "el sandbox 'subprocess'."
        ),
    )

    # LLM Configuration. Credentials wrapped in ``SecretStr`` so they never
    # leak via ``repr(settings)``, validation errors, debug dumps, or log
    # lines. Read the actual value with ``.get_secret_value()`` only at the
    # point of use.
    llm_provider: str = Field(
        default="openai", description="openai, anthropic, azure u openai_compatible (alias: local; requiere LLM_BASE_URL)")
    openai_api_key: SecretStr = Field(default=SecretStr(""), description="OpenAI API Key")
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), description="Anthropic API Key")
    azure_openai_api_key: SecretStr = Field(default=SecretStr(""), description="Azure OpenAI API Key")
    azure_openai_endpoint: str = Field(default="", description="Azure OpenAI Endpoint")
    openai_api_version: str = Field(default="2024-12-01-preview", description="Azure OpenAI API Version")
    llm_model: str = Field(default="gpt-5.4", description="Modelo LLM a usar (en Azure: el deployment)")
    # Servidores compatibles con la API de OpenAI (vLLM, Ollama, LM Studio, gateway corporativo).
    llm_base_url: str = Field(default="", description="URL base de un servidor compatible con OpenAI")
    llm_api_key: SecretStr = Field(default=SecretStr(""), description="Clave del servidor compatible (si la pide)")
    # Rasgos del modelo que no se deducen de su nombre (sobre todo en Azure, donde LLM_MODEL es el
    # deployment). JSON con campos de ModelProfile, p. ej. {"reasoning": true, "supports_temperature": false,
    # "max_tokens_param": "max_completion_tokens", "min_output_tokens": 8192}.
    llm_model_profile: str = Field(default="", description="Perfil del modelo (JSON) que manda sobre la inferencia")
    llm_max_retries: int = Field(default=2, ge=0, le=10, description="Reintentos del SDK ante 429/5xx")
    # F7 (E7.5): los reintentos del SDK esperan 1–3 s y se rinden; con la cuota por minuto agotada caía
    # el 20 % de las consultas con 5 usuarios. Ante un 429 se sigue esperando hasta este presupuesto.
    # F7 (auditoría): se mide con el reloj desde la primera llamada (reintentos del SDK incluidos) y es
    # POR LLAMADA y aproximado: decide si se reintenta, no corta un intento en curso (el primero, con
    # los reintentos del SDK que siguen Retry-After de hasta 60 s, puede pasarse). Si el turno fija su
    # plazo (llm_client.plazo_turno), tampoco se reintenta más allá de él.
    llm_espera_saturacion_s: float = Field(default=60.0, ge=0, le=600,
                                           description="Segundos (aprox., de reloj) tras los que una llamada "
                                                       "deja de reintentar ante un proveedor saturado (429)")
    llm_request_timeout: int = Field(
        default=60,
        ge=5,
        le=600,
        description="Timeout HTTP por llamada al LLM en segundos",
    )

    # API authentication. If unset, auth is disabled (development mode).
    # When set, every REST request must carry ``X-API-Key: <value>`` and
    # every WebSocket connection must include ``?token=<value>``.
    api_key: SecretStr | None = Field(
        default=None,
        description="API key required to call the API. None disables auth (dev only).",
    )
    # F6 (S6.1): con la API key entra un cliente de SERVICIO (scripts, CI), no una persona.
    # Su organización y su rol se fijan aquí; las personas entran por OIDC.
    api_key_org: str = Field(default="default", description="Organización del cliente de la API key")
    api_key_role: str = Field(default="admin", description="Rol del cliente de la API key (viewer|analyst|admin)")
    # F6: la organización que OPERA la plataforma. Solo sus administradores re-aprueban las
    # herramientas de los servidores de la plataforma (afectan a todas las organizaciones). Sin
    # definir (una sola organización, D4), cualquier administrador.
    org_plataforma: str | None = Field(default=None, description="Organización que administra la plataforma")

    # F6 (S6.1) — identidad de personas por OIDC. Con `oidc_issuer` definido, la API exige un
    # Bearer JWT firmado por ese emisor (o la API key de servicio). El frontend lee esta misma
    # configuración de GET /api/v1/auth/config: una sola fuente de verdad.
    oidc_issuer: str | None = Field(
        default=None, description="Emisor OIDC tal como aparece en el `iss` del token (URL pública)")
    oidc_jwks_url: str | None = Field(
        default=None,
        description="URL de las claves públicas (JWKS) vista desde la API; sin ella, la `jwks_uri` del "
                    "documento de descubrimiento del emisor (/.well-known/openid-configuration)")
    oidc_audience: str = Field(default="geo-copilot-api", description="`aud` que debe traer el token")
    oidc_client_id: str = Field(default="geo-copilot-web", description="Cliente público (PKCE) del frontend")
    oidc_org_claim: str = Field(default="org", description="Claim con la organización del usuario")
    oidc_roles_claim: str = Field(
        default="realm_access.roles", description="Ruta (con puntos) del claim con los roles")
    oidc_token_url: str | None = Field(
        default=None,
        description="Endpoint de token visto desde la API (token exchange hacia MCP); sin él, el "
                    "`token_endpoint` del documento de descubrimiento del emisor")
    ws_ticket_ttl_s: int = Field(default=30, ge=5, le=300, description="Vida del ticket de un solo uso del WS")
    # T6.4: la API como cliente del proveedor para el token exchange hacia los servidores MCP
    oidc_api_client_id: str = Field(default="geo-copilot-api", description="Cliente de la API en el proveedor")
    oidc_api_client_secret: SecretStr | None = Field(default=None, description="Secreto de ese cliente")

    # Database. ``database_url`` may contain credentials so we wrap it too.
    database_url: SecretStr = Field(
        default=SecretStr("postgresql://user:password@localhost:5432/geo_copilot"),
        description="URL de conexión a PostGIS",
    )
    database_read_only: bool = Field(default=True, description="Solo lectura para seguridad")

    # Redis Cache
    redis_url: str = Field(default="redis://localhost:6379/0", description="URL de Redis")
    cache_ttl: int = Field(default=3600, description="TTL del cache en segundos")

    # Backend de sesiones (Fase 6 #11). "memory" mantiene el dict en
    # proceso (default histórico, apto para desarrollo y tests).
    # "redis" usa Redis con TTL nativo — necesario para multi-worker y
    # persistencia entre reinicios.
    session_backend: str = Field(
        default="memory",
        description="Backend de almacenamiento de sesiones: 'memory' o 'redis'",
    )
    session_max_count: int = Field(
        default=100,
        ge=1,
        description="Máximo de sesiones activas (relevante para el backend in-memory)",
    )
    session_timeout_minutes: int = Field(
        default=60,
        ge=1,
        description="Timeout de inactividad de una sesión, en minutos",
    )

    # Security
    hitl_enabled: bool = Field(default=True, description="Habilitar Human-in-the-Loop")
    hitl_timeout: int = Field(default=DefaultTimeouts.HITL_APPROVAL, description="Timeout HITL en segundos")

    # =================================================================
    # LÍMITES Y CONFIGURACIONES CENTRALIZADAS
    # =================================================================
    sql_result_limit: int = Field(
        # H19 (V5 F2): con el workspace y las teselas, una capa grande no viaja
        # entera al navegador; el tope de 1000 solo truncaba resultados reales.
        # 10.000 seguía recortando capas reales (el usuario: «debe traer todo»): el resultado va
        # al workspace y se dibuja por teselas, así que el tope es el mismo de las fuentes externas.
        default=200_000,
        ge=10,
        le=500_000,
        description="Límite por defecto de resultados SQL"
    )
    sandbox_timeout: int = Field(
        default=60,
        ge=5,
        le=300,
        description=(
            "Timeout de ejecución del sandbox (s). Subido a 60s default / 300s max: "
            "el sandbox es un motor de código general (análisis espacial avanzado, "
            "regresión, redes) donde tareas legítimas pueden tardar más que una "
            "transformación simple. Lo envuelve total_execution_timeout+hitl_timeout."
        ),
    )
    sandbox_memory_mb: int = Field(
        default=2048,
        ge=512,
        le=16384,
        description=(
            "Límite de memoria virtual (RLIMIT_AS) del sandbox en MB. "
            "OpenBLAS/numpy/geopandas reservan ~1-2 GB virtuales al import "
            "aun con OPENBLAS_NUM_THREADS=1; menos de 1024 MB suele matar "
            "al child antes de ejecutar código de usuario."
        ),
    )
    # SEC-SANDBOX-RCE (parte 2) / SBX-01: dónde corre el código del PythonAgent.
    #   "subprocess" → child local dentro del contenedor `app` (aísla la
    #       memoria del proceso, pero comparte red/FS del app). Es el default de
    #       CÓDIGO solo por seguridad de dev/tests: en SO no-POSIX (Windows) no
    #       hay rlimits y `sandbox.py` rechaza este backend explícitamente.
    #   "docker"     → `docker exec` contra el contenedor endurecido `sandbox`
    #       (network:none, read_only, cap_drop:ALL, non-root). Contención real
    #       de RCE/egreso. **Es el DEFAULT DEL DESPLIEGUE**: docker-compose fija
    #       SANDBOX_BACKEND=docker en `app` y le da acceso al daemon SOLO vía un
    #       docker-socket-proxy (allowlist EXEC), no el socket crudo del host.
    # R0.5 (auditoría 2026-07-26, AUD-03): el DEFAULT se invierte a "docker".
    # El filtro AST de `sandbox.py` NO es una barrera —se evade con
    # `json.__builtins__`, verificado ejecutando el runner real— así que la
    # única contención efectiva es el contenedor. Con el default anterior
    # ("subprocess") cualquiera que arrancase la app fuera de docker —el camino
    # que el propio README documenta— ejecutaba el código del LLM dentro del
    # proceso `app`, con DATABASE_URL, las claves del LLM y DOCKER_HOST en el
    # entorno. `enforce_sandbox_backend` (api/auth.py) rehúsa "subprocess"
    # fuera de desarrollo.
    sandbox_backend: Literal["docker", "subprocess"] = Field(
        default="docker",
        description="Motor de ejecución del sandbox: 'docker' (por defecto, contención real) | 'subprocess' (SOLO desarrollo)",
    )
    # R0.7 (auditoría 2026-07-26, AUD-04): validación estructural del SQL.
    #   off     → no se ejecuta.
    #   shadow  → analiza y REGISTRA qué rechazaría, sin bloquear.
    #   enforce → bloquea (default desde el 2026-09-24).
    # Arrancó en `shadow` para medir antes de bloquear. Medición (S0.2 del plan
    # de plataforma, acta docs/validacion/FASE_0_*): bench agéntico completo con
    # LLM y PostGIS reales y allowlist de tablas del semantic layer → 0 SQL
    # legítimos rechazados; solo se rechazó `public.hospitales`, que no existe.
    # `shadow` sigue disponible para medir una BD o un modelo nuevos.
    sql_ast_validation: Literal["off", "shadow", "enforce"] = Field(
        default="enforce",
        description="Validación del SQL por AST: 'off' | 'shadow' (registra) | 'enforce' (bloquea)",
    )
    sandbox_docker_container: str = Field(
        default="geo_copilot_sandbox",
        description="Nombre/ID del contenedor endurecido para sandbox_backend='docker'",
    )
    sandbox_docker_runner_path: str = Field(
        default="/opt/sandbox/sandbox_runner.py",
        description="Ruta del runner DENTRO del contenedor sandbox (montado read-only)",
    )
    # S2.4: los datasets del workspace llegan al sandbox como GeoParquet. `app`
    # los escribe en `workspace_export_dir`; el sandbox docker ve ese mismo
    # volumen, de solo lectura, en `sandbox_docker_datasets_root`.
    # F3: servidores MCP enchufables (plan §3.2). Un archivo ausente = sin servidores.
    mcp_servers_path: str = Field(
        default="config/mcp_servers.yaml",
        description="YAML con los servidores MCP que el núcleo conecta",
    )
    mcp_tools_umbral: int = Field(
        default=25, ge=0, le=1000,
        description="S3.7: por encima de este nº de tools MCP el LLM las busca con find_tools en vez de verlas todas",
    )
    mcp_refresh_s: float = Field(
        default=60.0, ge=5, le=3600,
        description="Cada cuántos segundos se revalida el catálogo de tools de los servidores MCP",
    )
    # Límites de lo que cruza entre el agente y un servicio conectado. Antes eran constantes en el
    # código y cortaban en silencio; ahora se dicen (al LLM) y se ajustan por entorno — un modelo con
    # ventana grande puede ver más salida.
    mcp_obs_max_chars: int = Field(
        default=3000, ge=500, le=200_000,
        description="Caracteres de la salida de un servicio conectado que ve el LLM (el recorte se le dice)",
    )
    mcp_geo_max_features: int = Field(
        default=5000, ge=100, le=500_000,
        description="Elementos de una capa que se envían a un servicio; por encima no se ejecuta (no se trunca)",
    )
    workspace_export_dir: str = Field(
        default_factory=lambda: os.path.join(tempfile.gettempdir(), "geo_copilot_ws"),
        description="Directorio donde app exporta los datasets del workspace (GeoParquet)",
    )
    sandbox_docker_datasets_root: str = Field(
        default="/data/workspace",
        description="Ruta del volumen de datasets DENTRO del contenedor sandbox (read-only)",
    )
    sandbox_docker_bin: str = Field(
        default="docker",
        description="Binario del cliente Docker en el contenedor app (para `docker exec`)",
    )
    schema_cache_ttl: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="TTL del cache de schema de base de datos en segundos"
    )

    # External APIs
    http_timeout: int = Field(default=60, description="Timeout HTTP general en segundos")

    # ArcGIS: lo gestiona el servidor MCP de ArcGIS (services/arcgis_mcp), no el núcleo (T5.2).

    # Database query settings
    db_query_timeout: int = Field(default=30, description="Timeout para queries SQL en segundos")
    db_pool_min_size: int = Field(default=2, description="Mínimo de conexiones en pool")
    db_pool_max_size: int = Field(default=10, description="Máximo de conexiones en pool")

    # CORS settings. La lista se configura vía CORS_ORIGINS en .env
    # (fuente de verdad). Si queda vacía, app.py cae a localhost:5173.
    cors_origins: list[str] = Field(
        default=[],
        description="Orígenes permitidos para CORS. Configurar en .env (CORS_ORIGINS). Usar CORS_ALLOW_ALL=true solo en desarrollo."
    )
    cors_allow_all: bool = Field(default=False, description="Permitir todos los orígenes (solo desarrollo)")
    # S7: confiar en X-Forwarded-For para el rate-limit. Activar SOLO si la
    # app corre detrás de un proxy/LB de confianza que setea el header; de
    # lo contrario un cliente podría spoofear su IP y evadir el límite.
    trust_proxy_headers: bool = Field(
        default=False,
        description="Usar X-Forwarded-For para identificar al cliente (solo tras proxy de confianza)",
    )

    allowed_domains: list[str] = Field(
        default=[
            # =================================================================
            # PORTALES DE DATOS ABIERTOS
            # =================================================================
            "datos.gov.co",
            "www.datos.gov.co",
            # =================================================================
            # SERVIDORES ARCGIS VERIFICADOS (responden correctamente)
            # =================================================================
            # Entidades Nacionales
            "mapas.parquesnacionales.gov.co",  # Parques Nacionales
            "geoservicios.esri.co",            # IDEAM y otros en ESRI Colombia
            "geoservicios.upra.gov.co",        # UPRA
            "mapas2.igac.gov.co",              # IGAC principal
            "mapas.igac.gov.co",               # IGAC alterno
            "geoportal.dane.gov.co",           # DANE
            "geovisor.anh.gov.co",             # ANH - Hidrocarburos
            "geo.anm.gov.co",                  # ANM - Minería
            "visualizador.ideam.gov.co",       # IDEAM directo
            "hermes.invias.gov.co",            # INVIAS - Vías
            "gisart.renovacionterritorio.gov.co",  # ART - Renovación Territorio
            "portalsig.anla.gov.co",           # ANLA - Licencias Ambientales
            "srvags.sgc.gov.co",               # SGC - Servicio Geológico
            # Bogotá D.C.
            "serviciosgis.catastrobogota.gov.co",  # Catastro Bogotá - IDECA
            "secretariadeambiente.gov.co",     # SDA
            "portalgis.habitatbogota.gov.co",  # Hábitat
            "sig.simur.gov.co",                # Movilidad
            "sigau.jbb.gov.co",                # Jardín Botánico
            "www.acueducto.com.co",            # Acueducto Bogotá - EAAB
            "oaiee.scj.gov.co",                # Seguridad Bogotá
            # Municipios
            "sigmzl.manizales.gov.co",         # Manizales
            "idep.palmira.gov.co",             # Palmira
            # Corporaciones Autónomas
            "sia.cortolima.gov.co",            # CorTolima
            "www.geo.cvc.gov.co",              # CVC
            # =================================================================
            # SERVICIOS ARCGIS GENÉRICOS (siempre disponibles)
            # =================================================================
            "services.arcgis.com",
            "services1.arcgis.com",
            "services2.arcgis.com",
            "services3.arcgis.com",
            "services7.arcgis.com",
            "services9.arcgis.com",
            "arcgis.com",
            "www.arcgis.com",
            # ArcGIS Hub Open Data API (descubrimiento global)
            "hub.arcgis.com",
            "opendata.arcgis.com",
            "server.arcgisonline.com",  # Imagery base providers
        ],
        description="Dominios permitidos para APIs externas y descarga de datos"
    )
    max_external_file_size_mb: int = Field(
        default=50,
        description="Tamaño máximo de archivos externos en MB"
    )
    max_external_features: int = Field(
        # S2.3: con el workspace (PostGIS) y la entrega por teselas, una capa
        # grande ya no viaja entera al navegador ni a la sesión. Antes 10000. 200.000 es también el
        # tope del servidor MCP de ArcGIS (paginado): más allá, el hecho dice «es una MUESTRA».
        default=200_000,
        ge=1,
        le=500_000,
        description="Máximo de features a obtener de fuentes externas (chat y panel)"
    )

    # =================================================================
    # SEGURIDAD: WebSocket y Rate Limiting
    # =================================================================
    websocket_receive_timeout: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Timeout de recepción WebSocket en segundos"
    )
    websocket_max_message_size: int = Field(
        default=1048576,
        description="Tamaño máximo de mensaje WebSocket (1MB por defecto)"
    )
    max_geojson_features: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="Máximo de features permitidos en GeoJSON entrante"
    )
    workspace_inline_max_features: int = Field(
        default=5000,
        ge=0,
        le=100000,
        description=(
            "S2.3: hasta cuántas features una capa del workspace viaja como GeoJSON "
            "inline en la respuesta; por encima se entrega como teselas MVT"
        ),
    )
    rate_limit_requests: int = Field(
        default=30,
        ge=1,
        le=1000,
        description="Máximo de requests por minuto para rate limiting"
    )

    # Debug
    debug_console_output: bool = Field(
        default=False,
        description="Mostrar output de debug en consola (prints decorativos)"
    )

    # Logging
    log_level: str = Field(default="INFO", description="Nivel de logging")
    log_format: str = Field(default="json", description="Formato de logs: json o text")

    # Paths
    config_dir: str = Field(
        default="config",
        description="Directorio de archivos de configuración"
    )
    semantic_layer_path: str = Field(
        default="semantic_layer/entities.yaml",
        description="Ruta a la capa semántica"
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"
    }


@lru_cache
def get_settings() -> Settings:
    """Obtener instancia singleton de configuración."""
    return Settings()


# Instancia global
settings = get_settings()
