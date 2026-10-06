"""
Configuración compartida para tests de pytest.
"""

import os
import sys

import pytest

# Agregar src al path para imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Windows dev: el instalador de PostgreSQL/PostGIS deja un PROJ_LIB GLOBAL apuntando a su proj.db
# (más viejo). La primera librería que inicializa PROJ en el proceso (pyproj, geopandas, rasterio)
# lo toma, y desde ahí rasterio no resuelve EPSG: test_imagery_mcp fallaba o no según qué prueba
# corriera antes (CRSError «EPSG code is unknown»; 502 en las teselas). Sin esas variables, cada
# wheel usa los datos PROJ que trae. Solo se quitan si apuntan FUERA del entorno de Python.
if os.name == "nt":
    for _var in ("PROJ_LIB", "PROJ_DATA"):
        _valor = os.environ.get(_var)
        if _valor and not os.path.normcase(os.path.abspath(_valor)).startswith(
                os.path.normcase(os.path.abspath(sys.prefix))):
            del os.environ[_var]

# S1 (Fase 2): la suite corre en contexto de desarrollo. El guard de
# arranque ``enforce_production_auth`` rechaza booear con debug=False y sin
# API key (lo correcto en producción), y los tests que entran al lifespan
# vía ``with TestClient(app)`` lo dispararían. Marcamos el entorno como dev
# ANTES de cualquier import de geo_copilot para que ``get_settings()`` lea
# debug=True. La auth sigue deshabilitada (api_key=None) como esperan los
# tests; el comportamiento de producción se valida aparte en test_auth.py.
os.environ.setdefault("DEBUG", "true")

# Auditoría 2026-09-08 (§7 punto 8): el default de ``environment`` pasó a
# "production" (core/config.py), así que la suite ya no puede HEREDAR el
# contexto de desarrollo: tiene que DECLARARLO, igual que hace arriba con
# DEBUG. Sin esta línea, los 20 tests que arrancan la app con
# ``TestClient(app)`` (test_hitl_full_flow, test_session_idor,
# test_websocket_handshake_sec4) mueren en el lifespan con el
# ``RuntimeError`` de ``enforce_production_debug`` — DEBUG=true, que la línea
# de arriba fija a propósito, es ilegal en producción.
# ``setdefault`` y no asignación: quien quiera correr la suite contra el
# comportamiento de producción exporta ENVIRONMENT y este archivo no lo pisa.
# En la máquina de un colaborador el ``.env`` de la raíz también trae
# ENVIRONMENT=development, pero el CI no tiene ``.env``: ahí esta línea es
# lo único que separa la suite del guard de producción.
os.environ.setdefault("ENVIRONMENT", "development")

# F3: la suite NUNCA escribe en un Redis real. El `.env` de desarrollo puede traer
# SESSION_BACKEND=redis (sesiones persistentes del stack); heredado aquí, las
# sesiones de los tests sobrevivían entre corridas y la segunda daba 409
# "La sesión ya existe". Asignación, no setdefault: es aislamiento, no default.
os.environ["SESSION_BACKEND"] = "memory"
# F6: lo mismo con la identidad. El `.env` de desarrollo puede activar OIDC (el login del
# stack); los tests que prueban la identidad lo configuran ellos mismos (test_identidad.py).
# Vacío (no borrado): pydantic lee el .env si la variable no existe en el entorno.
os.environ["OIDC_ISSUER"] = ""

# F7 (auditoría): y con el cliente de servicio. El `.env` de desarrollo trae API_KEY (y su rol,
# y TRUST_PROXY_HEADERS) para la prueba de carga; heredada aquí, la auth se activaba y ~150 tests
# daban 401 «No autenticado». No vale el truco de OIDC_ISSUER: API_KEY="" es una clave MAL
# configurada (fail-closed), no «sin auth»; hay que quitarla del entorno Y de lo que pydantic lee
# del `.env`. Solo estas variables: el resto del `.env` (las claves del LLM de los tests
# @pytest.mark.llm) sigue llegando. Quien prueba la auth la configura él mismo (test_auth.py).
_IDENTIDAD_DE_SERVICIO = ("api_key", "api_key_org", "api_key_role", "trust_proxy_headers")
for _var in _IDENTIDAD_DE_SERVICIO:
    os.environ.pop(_var.upper(), None)


def _aislar_identidad_del_dotenv() -> None:
    from pydantic_settings import DotEnvSettingsSource, PydanticBaseSettingsSource

    from geo_copilot.core import config

    class _DotenvSinIdentidad(PydanticBaseSettingsSource):
        def __init__(self, settings_cls, fuente):
            super().__init__(settings_cls)
            self._fuente = fuente

        def get_field_value(self, field, field_name):  # no se usa: __call__ filtra el lote
            return None, field_name, False

        def __call__(self):
            return {k: v for k, v in self._fuente().items() if k.lower() not in _IDENTIDAD_DE_SERVICIO}

    original = config.Settings.settings_customise_sources

    def _fuentes(cls, settings_cls, **fuentes):
        return tuple(_DotenvSinIdentidad(settings_cls, f) if isinstance(f, DotEnvSettingsSource) else f
                     for f in original.__func__(cls, settings_cls, **fuentes))

    config.Settings.settings_customise_sources = classmethod(_fuentes)
    # el módulo ya construyó su singleton al importarse: se rehace con las fuentes aisladas
    config.get_settings.cache_clear()
    config.settings = config.get_settings()


_aislar_identidad_del_dotenv()


# =============================================================================
# Fixture: pool de PostGIS de tests (F3 del roadmap)
# =============================================================================
# URL del PostGIS de tests configurado vía docker-compose.test.yml:
#   docker compose -f docker-compose.test.yml up -d
#
# Para tests marcados con @pytest.mark.integration. Si la BD no está
# disponible, los tests se SKIPEAN (no fallan) para no romper la suite
# en máquinas sin Docker. CI debe levantar el compose antes.

# 5434: el puerto que publica `docker-compose.test.yml`. Estaba en 5433 (el del
# PostGIS de desarrollo) desde que el compose cambió de puerto, así que los tests
# de integración se SALTABAN en silencio aunque el stack de tests estuviera arriba.
# `gc_reader`: rol de solo lectura (tests/fixtures/test_roles.sql), no el
# superusuario que crea la imagen.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://gc_reader:gc_reader_pw@localhost:5434/geocopilot_test",
)

# En CI (`REQUIRE_TEST_DB=1`) la BD ausente es un FALLO, no un salto: un job de
# integración que se salta todo sale verde sin haber probado nada.
_REQUIRE_TEST_DB = os.environ.get("REQUIRE_TEST_DB") == "1"


def _sin_bd(motivo: str):
    if _REQUIRE_TEST_DB:
        pytest.fail(f"REQUIRE_TEST_DB=1 y {motivo}", pytrace=False)
    pytest.skip(motivo)


# S2.1: el workspace escribe con el rol de APLICACIÓN (espejo de geo_app:
# NOINHERIT, asume geo_workspace/gis_readonly con SET LOCAL ROLE), no con el
# lector gc_reader. tests/fixtures/test_roles.sql lo crea.
TEST_APP_DATABASE_URL = os.environ.get(
    "TEST_APP_DATABASE_URL",
    "postgresql://gc_app:gc_app_pw@localhost:5434/geocopilot_test",
)


@pytest.fixture(scope="session")
async def workspace_pool():
    """Pool como `gc_app` para el Dataset Store (tests `postgis`)."""
    asyncpg = pytest.importorskip("asyncpg")
    try:
        pool = await asyncpg.create_pool(TEST_APP_DATABASE_URL, min_size=1, max_size=4, timeout=2)
    except (OSError, asyncpg.PostgresError) as e:
        _sin_bd(
            f"PostGIS de tests no disponible como gc_app ({e}). Recrea el stack de "
            "tests: docker compose -f docker-compose.test.yml down -v && ... up -d"
        )
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
async def postgis_pool():
    """Pool de asyncpg apuntando al PostGIS de tests.

    Si la BD no está disponible (Docker apagado), los tests que dependan
    de esta fixture se skipean. Para activar:

        docker compose -f docker-compose.test.yml up -d
        pip install -e .[dev,integration]
        pytest -m integration
    """
    asyncpg = pytest.importorskip(
        "asyncpg",
        reason="asyncpg no instalado — corre `pip install -e .[dev]`",
    )

    try:
        pool = await asyncpg.create_pool(
            TEST_DATABASE_URL,
            min_size=1,
            max_size=4,
            timeout=2,
        )
    except (OSError, asyncpg.PostgresError) as e:
        _sin_bd(
            f"PostGIS de tests no disponible en {TEST_DATABASE_URL}: {e}. "
            f"Levanta con: docker compose -f docker-compose.test.yml up -d"
        )

    # Smoke check: verificar que el seed cargó.
    async with pool.acquire() as conn:
        try:
            count = await conn.fetchval(
                "SELECT count(*) FROM catastro.construcciones"
            )
            if count == 0:
                _sin_bd(
                    "Schema catastro existe pero no tiene datos — "
                    "verifica que seed_catastro.sql se cargó"
                )
        except Exception as e:  # noqa: BLE001 — cualquier fallo del smoke check (tabla ausente, permisos, tipo) significa "BD de tests no lista": salta o, con REQUIRE_TEST_DB=1, falla
            _sin_bd(f"Schema catastro no inicializado: {e}")

    yield pool
    await pool.close()


@pytest.fixture(scope="session")
def test_data_dir(tmp_path_factory):
    """Directorio temporal para datos de test."""
    return tmp_path_factory.mktemp("test_data")


# =============================================================================
# LLM real (para tests @pytest.mark.llm). Valida que los PROMPTS provoquen el
# juicio correcto en un modelo de verdad — lo que un mock no puede. Cacheado +
# smoke una vez; SKIP si no hay LLM disponible (CI sin key no falla).
# =============================================================================
_REAL_LLM_CACHE: dict = {}


def _sin_credenciales(exc: Exception) -> bool:
    """¿El fallo es «no hay LLM» (sin clave, clave inválida, sin red)? Eso se salta. Cualquier otro
    (p. ej. 400: el proveedor rechaza lo que le mandamos) es una INCOMPATIBILIDAD con el modelo y
    debe fallar: antes se saltaba y la batería entera daba un falso verde al cambiar de modelo."""
    nombre = type(exc).__name__
    texto = str(exc).lower()
    return (nombre in {"AuthenticationError", "PermissionDeniedError", "APIConnectionError", "APITimeoutError"}
            or "api_key" in texto or "api key" in texto or "401" in texto)


async def get_real_llm_or_skip():
    """Devuelve el LLMClient configurado, o ``pytest.skip`` si no hay LLM. Con ``LLM_REQUIRED=1``
    (corrida de validación de un modelo) no se salta nunca: si no responde, falla."""
    if "client" not in _REAL_LLM_CACHE:
        from geo_copilot.core.config import get_settings
        from geo_copilot.core.llm_client import LLMClient, LLMMessage
        client = LLMClient.from_settings(get_settings())
        try:
            # tope holgado: con un modelo que razona, 3 tokens no alcanzan ni para pensar
            await client.chat([LLMMessage(role="user", content="ping")], max_tokens=16)
            _REAL_LLM_CACHE["client"] = client
        except Exception as exc:  # noqa: BLE001
            _REAL_LLM_CACHE["client"] = None
            _REAL_LLM_CACHE["err"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            _REAL_LLM_CACHE["saltable"] = _sin_credenciales(exc)
    if _REAL_LLM_CACHE.get("client") is None:
        msg = f"LLM real no disponible: {_REAL_LLM_CACHE.get('err', '')}"
        if os.environ.get("LLM_REQUIRED") == "1" or not _REAL_LLM_CACHE.get("saltable"):
            pytest.fail(msg)
        pytest.skip(msg)
    # Cada test corre en su propio event loop y el cliente HTTP del SDK queda atado al loop en que
    # se creó: reusarlo en el siguiente daba «Event loop is closed» (30 de 68 fallos, ninguno del
    # juicio del modelo). Un cliente por loop; el ping de arriba ya dijo que el LLM responde.
    import asyncio

    loop = asyncio.get_running_loop()
    if _REAL_LLM_CACHE.get("loop") is not loop:
        if "loop" in _REAL_LLM_CACHE:
            from geo_copilot.core.config import get_settings
            from geo_copilot.core.llm_client import LLMClient
            _REAL_LLM_CACHE["client"] = LLMClient.from_settings(get_settings())
        _REAL_LLM_CACHE["loop"] = loop
    return _REAL_LLM_CACHE["client"]


@pytest.fixture
def mock_llm_response():
    """Respuesta mock de LLM para tests."""
    return {
        "content": '{"intent": "search_internal", "data_type": "parcelas", "keywords": ["parcela"], "suggested_entities": ["parcela"], "external_sources": [], "reasoning": "User wants to search for parcels"}',
        "model": "test-model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    }


@pytest.fixture
def sample_geojson():
    """GeoJSON de ejemplo para tests."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-74.0, 4.0],
                        [-74.0, 5.0],
                        [-73.0, 5.0],
                        [-73.0, 4.0],
                        [-74.0, 4.0]
                    ]]
                },
                "properties": {
                    "id": 1,
                    "name": "Test Polygon"
                }
            }
        ]
    }


@pytest.fixture
def sample_records():
    """Registros de ejemplo para tests de validación."""
    return [
        {"id": 1, "name": "Record 1", "value": 100.0, "category": "A"},
        {"id": 2, "name": "Record 2", "value": 200.0, "category": "B"},
        {"id": 3, "name": "Record 3", "value": 150.0, "category": "A"},
        {"id": 4, "name": "Record 4", "value": None, "category": "C"},
        {"id": 5, "name": "Record 5", "value": 300.0, "category": "B"},
    ]


# =============================================================================
# Fixture: servidor MCP real (hello-geo) para los tests del cliente y del hub (F3)
# =============================================================================
# UNO por sesión de tests: el SDK solo deja arrancar una vez el gestor de
# sesiones de cada FastMCP por proceso.


@pytest.fixture(scope="session")
def evil_mcp_url():
    """Servidor MCP hostil en proceso (T3.10: inyección de prompt)."""
    from tests.mcp_helpers import ServidorMalicioso

    srv = ServidorMalicioso()
    yield srv.url
    srv.parar()


@pytest.fixture(scope="session")
def hello_mcp_url():
    from tests.mcp_helpers import ServidorHello

    srv = ServidorHello()
    yield srv.url
    srv.parar()


@pytest.fixture(autouse=True)
def _descubrimiento_oidc_limpio():
    """F7 (auditoría): la caché del descubrimiento OIDC es del módulo (por emisor, y recuerda un
    fallo 30 s): sin vaciarla, el documento —o el error— de un test lo leía el siguiente."""
    from geo_copilot.platform.identidad import descubrimiento

    descubrimiento._cache.clear()
    yield
    descubrimiento._cache.clear()
