# 9. Configuración y deploy

Este capítulo explica **cómo se configura y cómo se levanta** GEO_COPILOT: qué
archivos controlan su comportamiento, qué variables de entorno existen, cómo es
el `docker-compose.yml` real (siete servicios), cómo se prepara la base de datos
(rol `gis_readonly`, migraciones) y qué plataformas están soportadas para el
sandbox de Python.

Está escrito para que alguien que **no** tocó el código pueda desplegar el
sistema o entender por qué algo no arranca.

> Referencias cruzadas: la arquitectura general está en
> [01-arquitectura](01-arquitectura.md); el detalle de cada agente en
> [02-agentes](02-agentes.md); el orquestador en [03-orquestador](03-orquestador.md);
> cómo probarlo todo en [11-como-probar-todo](11-como-probar-todo.md).

---

## 9.1 Archivos de configuración

Nada del comportamiento del sistema está "hardcodeado" en un solo lugar: se
reparte entre variables de entorno, YAMLs editables y los Dockerfiles.

| Archivo | Para qué sirve |
|---------|---------------|
| `.env` (raíz) | Variables de entorno: secretos (API keys), URLs, timeouts y toggles. Lo carga `src/geo_copilot/core/config.py` vía Pydantic Settings. |
| `docker/docker-compose.yml` | Orquesta los **7 servicios**: `postgis`, `redis`, `app`, `frontend`, `sandbox`, `docker-socket-proxy`, `imagery-mcp` (+ redes `geo_network` y `docker_proxy`). Además `hello-geo`, servidor MCP de ejemplo, solo con el perfil `examples`. |
| `docker/Dockerfile` | Imagen del backend (`app`): FastAPI + uvicorn + LangGraph. |
| `docker/Dockerfile.frontend` | Build de Vite + nginx que sirve el bundle e inyecta la API key (patrón BFF). |
| `docker/Dockerfile.sandbox` | Contenedor endurecido donde corre el código Python del LLM en despliegue. |
| `docker/nginx.frontend.conf` | Config de nginx: proxy de `/api`, `/ws` y `/health`; **inyecta `X-API-Key` server-side** con `envsubst` de `${GEO_API_KEY}`. |
| `docker/init-db/*` | Scripts que corren **una sola vez** en un volumen fresco de PostGIS (extensiones → restore del catastro → rol `gis_readonly`). |
| `src/geo_copilot/migraciones/` | F7: migraciones **Alembic** del esquema de la aplicación (`ws_meta`, `plataforma`; las revisiones llevan su SQL congelado). `python -m geo_copilot.migraciones`; en producción las corre el servicio `migrar` antes de la app (ver 9.5). |
| `docker/migrations/*` | Scripts manuales idempotentes **anteriores a F7** para una BD ya desplegada (rol `gis_readonly`, contraseña de `geo_app`, `mcp_lector`…). El esquema de la plataforma ya no llega por aquí: lo versiona Alembic. |
| `config/discovery_catalog.yaml` | Catálogo multi-región (entidades, publicadores, zonas) del DiscoveryAgent. **Editable sin recompilar** (montado `:ro` en `app`). |
| `config/arcgis_servers.yaml` | Servidores ArcGIS verificados para el connector de discovery legacy. |
| `semantic_layer/entities.yaml` | Mapa entidad de negocio → tabla PostGIS. **Hoy es solo un fallback/override**: la fuente primaria es la introspección automática del esquema (ver 9.6). |
| `config/mcp_servers.yaml` | **Servidores MCP que conecta el MCP Hub** (id, url, auth por referencia `env:VAR`, conformance, trust, allowlist de tools, política de riesgo/HITL, prefijos de teselas). Enchufar un servicio = editar este YAML, sin tocar código. Ruta alternativa con `MCP_SERVERS_PATH` (p. ej. `config/mcp_servers.local.yaml`, no versionado). Ver [12-como-enchufar-un-mcp](12-como-enchufar-un-mcp.md). |
| `services/imagery_mcp/` | Servicio MCP aparte (imagery satelital); tiene su propio `Dockerfile`, `requirements.txt` y `config.py`. Para la app es el servidor `id: imagery` del YAML. |
| `services/hello_geo/`, `packages/geo_mcp_kit/` | Servidor MCP de ejemplo y kit compartido para escribir servidores MCP geo. |
| `pyproject.toml` | Dependencias Python + configuración de pytest, ruff, mypy. |
| `frontend/package.json` | Dependencias JS + scripts (`dev`/`build`/`test`/`test:e2e`). |

---

## 9.2 Variables de entorno (`.env`)

Las variables se declaran como campos de `Settings` en `core/config.py`. Pydantic
las lee de `.env` o del entorno del proceso (mayúsculas). Los secretos
(`OPENAI_API_KEY`, `DATABASE_URL`, `API_KEY`, `IMAGERY_MCP_API_KEY`) se guardan
como `SecretStr` para que **nunca** aparezcan en logs ni en trazas de error.

```bash
# === Aplicación ===
ENVIRONMENT=production                  # development | test | production
DEBUG=false                             # true FUERA de dev aborta el arranque (guardrail)

# === LLM ===
LLM_PROVIDER=openai                     # openai | anthropic | azure | local
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://...
LLM_MODEL=gpt-4o                        # default de código: gpt-4
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=4096
LLM_REQUEST_TIMEOUT=60                  # seg (5–600)

# === API / auth (F6: ver docs/sistema/18) ===
OIDC_ISSUER=                            # personas por OIDC: el `iss` PÚBLICO del proveedor. Vacío = sin login (dev)
OIDC_AUDIENCE=geo-copilot-api           # `aud` que deben traer los tokens
OIDC_CLIENT_ID=geo-copilot-web          # cliente público (PKCE) del frontend
OIDC_ORG_CLAIM=org                      # claim con la organización
OIDC_ROLES_CLAIM=realm_access.roles     # claim con los roles (viewer|analyst|admin)
# OIDC_JWKS_URL / OIDC_TOKEN_URL: las URLs vistas DESDE la API (el compose las pone a la red interna)
OIDC_API_CLIENT_SECRET=                 # la API ante el proveedor (token exchange hacia los MCP)
SECRETS_KEK=                            # 32 bytes base64: cifra las credenciales de las conexiones
SECRETS_KEK_ANTERIORES=                 # rotación: claves anteriores, separadas por comas
MCP_HOSTS_PERMITIDOS=                   # hosts internos que una organización puede usar (si no, solo https público)
ORG_PLATAFORMA=                         # la organización que re-aprueba las tools de la plataforma
API_KEY=                                # SOLO clientes de servicio (scripts, CI). "" = mal config → fail-closed
API_KEY_ORG=default                     # organización del cliente de servicio
API_KEY_ROLE=admin                      # rol del cliente de servicio
CORS_ORIGINS=["https://midominio.com"]  # nunca "*" en prod
CORS_ALLOW_ALL=false                    # true SOLO en desarrollo
RATE_LIMIT_REQUESTS=30                  # por minuto por cliente
TRUST_PROXY_HEADERS=false               # true SOLO detrás de un proxy de confianza

# === Base de datos ===
DATABASE_URL=postgresql://geo_user:pass@postgis:5432/geo_copilot
DATABASE_READ_ONLY=true                 # el SQL del LLM corre en transacción read-only

# === Cache / sesiones ===
REDIS_URL=redis://redis:6379/0
SESSION_BACKEND=memory                  # memory | redis (redis = multi-worker + persistencia)
SESSION_TIMEOUT_MINUTES=60
SESSION_MAX_COUNT=100

# === HITL (aprobación humana) ===
HITL_ENABLED=true
HITL_TIMEOUT=300                        # seg de espera por la aprobación humana
HITL_MODE=blocking                      # blocking (producción) | interrupt (spike experimental)
HITL_CHECKPOINTER=memory                # memory | postgres (solo para hitl_mode=interrupt)

# === Orquestación / autonomía ===
REACT_POLICY=hybrid                     # off | hybrid | always (ver 9.3)
REACT_MODE=false                        # flag legado; true == always (tiene precedencia)
REACT_MAX_TOOL_CALLS=8                  # circuit-breaker del bucle ReAct
REACT_MAX_REFLECTIONS=1                 # re-trabajos por auto-verificación composicional (0 = off)
AUTONOMOUS_MODE=true
MAX_RETRIES=2                           # reintentos por agente (auto-corrección)
ENABLE_PLANNING=true
MAX_PLAN_STEPS=5
PLANNING_COMPLEXITY_THRESHOLD=2
TOTAL_EXECUTION_TIMEOUT=120             # reloj de CÓMPUTO (separado del de espera humana)
STEP_TIMEOUT=30
CORRECTION_TIMEOUT=10
PLAN_TIMEOUT=300

# === Sandbox de Python ===
SANDBOX_BACKEND=docker                  # docker = DEFAULT (contención real) | subprocess = SOLO desarrollo
SANDBOX_TIMEOUT=60                      # seg (5–300); el corte duro es timeout+5
SANDBOX_MEMORY_MB=2048                  # RLIMIT_AS del child (solo backend subprocess)

# === Servidores MCP (MCP Hub) ===
MCP_SERVERS_PATH=config/mcp_servers.yaml      # YAML de servidores (archivo ausente = sin servidores)
MCP_TOOLS_UMBRAL=25                           # por encima, el LLM busca tools con find_tools
MCP_REFRESH_S=60                              # cada cuánto se revalida el catálogo de tools
IMAGERY_MCP_API_KEY=<GENERA_LA_TUYA>          # Bearer del servidor `imagery`: el YAML lo referencia
                                              # como secret_ref: env:IMAGERY_MCP_API_KEY
                                              # genérala con: openssl rand -hex 24
# La URL de cada servidor va en el YAML (ya no existe IMAGERY_MCP_URL).

# === Discovery ===
DISCOVERY_DEFAULT_REGION=colombia       # override del default del YAML

# === Logging / observabilidad ===
LOG_LEVEL=INFO                          # DEBUG | INFO | WARNING | ERROR
LOG_FORMAT=json                         # json (default) | text
DEBUG_CONSOLE_OUTPUT=false              # true = banners ASCII por nodo (dev)

# === Límites (defensa DoS) ===
MAX_GEOJSON_FEATURES=10000
MAX_EXTERNAL_FILE_SIZE_MB=50
MAX_EXTERNAL_FEATURES=100000           # F2: antes 10000 (lo grande va al workspace)
SQL_RESULT_LIMIT=1000                   # cap duro de filas por query SQL

# === Workspace espacial (F2) ===
WORKSPACE_INLINE_MAX_FEATURES=5000      # por encima, la capa va por teselas MVT
WORKSPACE_EXPORT_DIR=/data/workspace    # app exporta aquí los GeoParquet (volumen ws_exports)
SANDBOX_DOCKER_DATASETS_ROOT=/data/workspace  # el mismo volumen, visto desde el sandbox (ro)
WEBSOCKET_MAX_MESSAGE_SIZE=1048576      # 1 MB
WEBSOCKET_RECEIVE_TIMEOUT=60
```

> **Ojo con los defaults reales del código** (por si el `.env` no los define):
> `SQL_RESULT_LIMIT=1000` (no 10000), `MAX_RETRIES=2`, `MAX_PLAN_STEPS=5`,
> `TOTAL_EXECUTION_TIMEOUT=120`, `CORRECTION_TIMEOUT=10`, `LOG_FORMAT=json`,
> `REACT_POLICY=hybrid`, **`SANDBOX_BACKEND=docker`**, **`ENVIRONMENT=production`**,
> `SQL_AST_VALIDATION=enforce` (con allowlist de tablas del semantic layer; era `shadow` hasta el 2026-09-24). El `docker-compose.yml` **pisa** algunos de estos
> en el servicio `app` (las URLs internas), lo cual es correcto porque `.env`
> está pensado para correr **fuera** de Docker.
>
> ⚠️ **Los dos defaults en negrita se invirtieron a propósito** (R0.5 y R0.6 de
> la auditoría 2026-07-26) y son *fail-safe*: si la variable falta, la app exige
> `API_KEY`, oculta `/docs` y **rehúsa** el sandbox `subprocess`. Documentación
> anterior decía que `SANDBOX_BACKEND` venía en `subprocess` — era cierto hasta
> R0.5 y **ya no lo es** (`core/config.py:168`). `subprocess` ejecuta el código
> del LLM dentro del proceso `app`, con `DATABASE_URL`, las claves del LLM y
> `DOCKER_HOST` en el entorno; el filtro AST **no** es una barrera suficiente
> (se evade con `json.__builtins__`, verificado). No lo pongas en `subprocess`
> fuera de desarrollo — y `enforce_sandbox_backend` (`api/auth.py`) no te dejará.

### El servicio `imagery-mcp` tiene sus PROPIAS variables

El MCP de imagery es un proceso aparte con su config env-first (no depende de
`Settings` de la app):

```bash
IMAGERY_PROVIDER=planetary-computer     # planetary-computer | earth-search
IMAGERY_PORT=9100
IMAGERY_TILE_CACHE_DIR=/tmp/ndvi-tiles  # caché en disco de teselas PNG
# Claves del MCP (JSON): cada una con name, key, scopes y rate limit.
# La `key` debe ser la MISMA que el IMAGERY_MCP_API_KEY del backend.
IMAGERY_MCP_KEYS=[{"name":"geo-copilot-app","key":"<GENERA_LA_TUYA>","scopes":["imagery:read","imagery:compute"],"rate_limit_per_min":120}]
```

> 🔑 **Genera esa clave, no la copies de aquí.** `openssl rand -hex 24`. Hubo un
> `dev-imagery-key` de ejemplo **publicado en este repositorio**, y por eso el
> compose declara hoy `${IMAGERY_MCP_APP_KEY:?}` y `${IMAGERY_MCP_KEYS:?}`:
> **fail-closed**, el stack no arranca si no las defines. Un valor de ejemplo
> copiable derrota justo esa protección. Por la misma razón esas dos líneas van
> **comentadas** en `.env.example` (`:56` y `:64`): un valor asignado ahí
> satisface el `:?` y arranca el servicio con una credencial conocida.

> **El servicio `imagery-mcp` REHÚSA arrancar si `IMAGERY_MCP_KEYS` está vacío**
> (`build_app()` lanza `RuntimeError`). No hay modo "sin auth" para imagery.

### Mínimo para producción

- `LLM_PROVIDER` + la API key correspondiente.
- `DATABASE_URL` apuntando a PostGIS **con la extensión instalada**.
- **Identidad (F6):** `OIDC_ISSUER` con el proveedor de la organización (o, sin personas,
  `API_KEY` **no vacía**; si queda `""`, fail-closed). `SECRETS_KEK` si las organizaciones darán de
  alta conexiones con credencial. El esquema `plataforma` aplicado con
  `python -m geo_copilot.migraciones` (en el compose de producción lo hace el servicio `migrar`
  antes de la app); sin él, en producción la app **no arranca**.
- **TLS:** el certificado de la organización en `docker/certs/` (sin él, autofirmado y avisado).
- `CORS_ORIGINS` con el dominio real del frontend (nunca `"*"`).
- `SESSION_BACKEND=redis` + `REDIS_URL` si hay múltiples workers.
- `IMAGERY_MCP_KEYS` (en el servicio imagery) + `IMAGERY_MCP_API_KEY` (en `app`)
  si se quiere el análisis satelital, y el servidor `imagery` declarado en
  `config/mcp_servers.yaml`. Cada servidor MCP extra necesita en `app` la
  variable a la que apunta su `secret_ref`.
- `ENVIRONMENT=production` con `DEBUG=false` (ambos verificados al arrancar).

---

## 9.3 Cómo decide el sistema qué camino de orquestación usar

Dos variables gobiernan el "cerebro" del orquestador. No hacen falta para
levantar el sistema, pero explican su comportamiento:

- **`REACT_POLICY`** (default `hybrid`):
  - `off` → siempre el **router clásico** por intent (barato, predecible).
  - `hybrid` → las consultas **compuestas/complejas** van al bucle **ReAct**
    (`agent_loop`, tool-calling nativo con circuit-breaker); las simples van al
    router clásico. Es el default validado por benchmark (32 tareas, LLM real).
  - `always` → todo pasa por el bucle ReAct.
  - `REACT_MODE=true` (flag viejo) equivale a `always` y **tiene precedencia**.
- **`HITL_MODE`** (default `blocking`): mecanismo de aprobación humana.
  `blocking` es el de producción (`asyncio.Event` + `request_approval`);
  `interrupt` es un spike experimental sobre `LangGraph interrupt()` +
  checkpointer, **diferido** (ver [03-orquestador](03-orquestador.md)).

> Rollback sin redeploy de código: `export REACT_POLICY=off`.

---

## 9.4 Docker Compose: los siete servicios

El stack real (`docker/docker-compose.yml`) tiene siete servicios en dos redes.
Este es el modelo mental:

```mermaid
flowchart TB
    subgraph geo_network["Red geo_network (bridge)"]
        FE["frontend<br/>(nginx BFF: inyecta X-API-Key)"]
        APP["app<br/>(FastAPI + LangGraph)"]
        PG[("postgis<br/>18-master, rol gis_readonly")]
        RD[("redis<br/>cache / sesiones")]
        IMG["imagery-mcp<br/>(STAC → COG Sentinel-2, auth + scopes)"]
    end

    subgraph docker_proxy["Red docker_proxy (internal, aislada)"]
        DSP["docker-socket-proxy<br/>(haproxy, allowlist de ruta exacta)"]
    end

    SBX["sandbox<br/>(network:none, read_only, cap_drop ALL, non-root)"]
    HOST["/var/run/docker.sock<br/>(daemon del host)"]
    STAC["Catálogos STAC externos<br/>(Planetary Computer / Earth Search)"]

    Usuario(["Navegador"]) -->|":3000"| FE
    FE -->|"proxy /api y /ws"| APP
    APP -->|"SELECT vía SET ROLE gis_readonly"| PG
    APP --> RD
    APP -->|"MCP Hub: Bearer (MCP streamable HTTP)"| IMG
    APP -->|"DOCKER_HOST=tcp://...:2375"| DSP
    DSP -->|"solo docker exec"| HOST
    HOST -.->|"exec en"| SBX
    IMG -->|"único egress externo"| STAC
```

Puntos clave (por qué el diseño es así):

- **`postgis`** — imagen `postgis/postgis:18-master` **pinneada por digest**
  (`:18-master` es un tag mutable nightly). Puerto host **`5433→5432`** para no
  chocar con un Postgres nativo del host en `:5432`. Volumen `postgis18_data`
  montado en `/var/lib/postgresql` (convención PG18). Scripts de `init-db/`
  corren solo en volumen fresco.
- **`redis`** — cache y, opcionalmente, backend de sesiones (`SESSION_BACKEND=redis`).
- **`app`** — backend. Monta `src/`, `semantic_layer/` y `config/` como `:ro`
  (sin rebuild, pero **sin hot-reload**: uvicorn corre sin `--reload`; tras
  cambiar código, `docker compose restart app`). Monta además el volumen
  **`ws_exports`** en `/data/workspace` (rw): ahí exporta los GeoParquet de los
  datasets del workspace que pide el sandbox (F2). Recibe overrides internos:
  `DATABASE_URL=...@postgis:5432/...`, `REDIS_URL`,
  `IMAGERY_MCP_API_KEY` (la credencial a la que apunta el YAML de MCP; la URL
  del servidor vive en `config/mcp_servers.yaml`), `SANDBOX_BACKEND=docker` y
  `DOCKER_HOST=tcp://docker-socket-proxy:2375`. **Ya no monta el socket de Docker
  crudo.** Está en las dos redes (`geo_network` + `docker_proxy`).
- **`frontend`** — build de Vite servido por nginx. nginx inyecta `X-API-Key`
  server-side en `/api` y `/ws` (`${GEO_API_KEY}` = `${API_KEY}` del compose), de
  modo que **el bundle del navegador nunca lleva la clave** (patrón BFF).
- **`sandbox`** — contenedor de larga vida, endurecido: `network_mode: none`,
  `read_only: true`, `cap_drop: ALL`, `tmpfs /tmp` (100M), `no-new-privileges`,
  límites de CPU/RAM. Con `SANDBOX_BACKEND=docker`, `app` ejecuta
  `docker exec ... python -I /opt/sandbox/sandbox_runner.py` contra él. El runner
  se monta `:ro` desde `src/.../gis_agent/sandbox_runner.py`. Ve el volumen
  **`ws_exports`** en `/data/workspace` **de solo lectura**: el runner carga solo
  las rutas `ws_<hex>/ds_<hex>.parquet` que `app` le pasa, y el código del LLM
  no puede leer archivos (`open`/`read_*` prohibidos, `pyarrow` fuera de la
  allowlist).
- **`docker-socket-proxy`** — mediador de mínimos privilegios entre `app` y el
  daemon Docker. Vive en la red `docker_proxy` **interna** (nadie más la
  comparte) y monta el socket `:ro`. Es un **haproxy propio**
  (`haproxy:3.0-alpine`) con **allowlist de ruta exacta**. Tiene su propia
  sección abajo: **léela antes de tocarlo**.
- **`imagery-mcp`** — servicio MCP aparte (build en `services/imagery_mcp/`).
  **Único servicio con egress a catálogos STAC externos.** Claves vía
  `IMAGERY_MCP_KEYS` (JSON con scopes y rate limit). La app lo usa como un
  servidor más del MCP Hub (`id: imagery` en `config/mcp_servers.yaml`).
- **`hello-geo`** (perfil `examples`, no arranca por defecto) — servidor MCP de
  ejemplo sobre `packages/geo_mcp_kit`. Se levanta con
  `docker compose --profile examples up -d hello-geo` (pide `HELLO_GEO_KEYS`) y se
  registra en el YAML. Solo red interna.

### La allowlist del `docker-socket-proxy` — no la cambies sin leer esto

El mediador es un **haproxy propio** (`haproxy:3.0-alpine`) configurado por
[`docker/docker-socket-mediator.cfg`](../../docker/docker-socket-mediator.cfg).
La política es una **allowlist de ruta exacta** con `http-request deny` final.
Lo único que pasa es lo que necesita
`docker exec -i geo_copilot_sandbox python -I <runner>`:

| Método | Ruta permitida |
|---|---|
| `GET` | `/_ping` |
| `GET` | `/version` |
| `GET` | `/containers/geo_copilot_sandbox/json` |
| `GET` | `/exec/{id}/json` |
| `POST` | `/containers/geo_copilot_sandbox/exec` |
| `POST` | `/exec/{id}/start` |

Todas admiten el prefijo de versión opcional (`/v1.4x`). **Todo lo demás cae en
la denegación final y devuelve 403**: `/containers/create`, `/containers/json`,
`/images`, `/networks`, `/volumes`, `exec` sobre cualquier **otro** contenedor,
`DELETE`, `PUT`.

Además (**R0.4b**) se filtra el **cuerpo** del `exec` — de ahí el
`option http-buffer-request`. Se rechaza con 403 cualquier `POST .../exec` que
lleve `"Privileged": true`, `"User"` no vacío, `"WorkingDir"` o `"HostConfig"`:
por HTTP crudo se podía pedir `"User": "0"` y conseguir uid=0 dentro del
sandbox (medido). El filtro mira el **valor**, no el nombre del campo, porque el
cliente legítimo de Docker manda siempre `"User":""` y `"Privileged":false`.

El contenedor corre como `root` a propósito: el socket es `root:root 0660` y el
usuario `haproxy` de la imagen no puede abrirlo (daba 503 en todas las rutas).
La frontera de seguridad no es su uid sino la allowlist — el contenedor va con
`cap_drop: ALL`, `no-new-privileges` y su config montada read-only.

> ⚠️ **No lo reemplaces por `tecnativa/docker-socket-proxy` ni por una allowlist
> por variables (`CONTAINERS=1 EXEC=1 POST=1`).** Esa era la configuración
> anterior y la remediación **R0.4** (auditoría 2026-07-26, AUD-02) la declaró
> **insuficiente**: filtra por *sección* del API, no por endpoint. Su única
> regla de ruta era
>
> ```
> http-request allow if { path -m reg -i ^(/v[\d\.]+)?/containers } { env(CONTAINERS) }
> ```
>
> lo que dejaba permitidos `POST /containers/create`,
> `POST /containers/{id}/start` y `POST /containers/CUALQUIERA/exec` — es decir,
> **root del host** desde `app`, encadenable con un escape del sandbox. Se midió
> antes del cambio: `POST /containers/create` devolvía **400 del daemon**
> («invalid reference format»), no 403 del proxy; la petición atravesaba el
> filtro. **Cualquier variante que filtre por sección reabre ese agujero.**
> Detalle en [`AUDITORIA_SEGURIDAD_2026-07-27.md`](../AUDITORIA_SEGURIDAD_2026-07-27.md).

Arrancar todo:

```bash
# Desde la RAÍZ del repo, no desde docker/. Con `cd docker` el project directory
# de Compose es `docker/`, donde no hay `.env`: las interpolaciones ${...} no
# encuentran POSTGRES_PASSWORD / REDIS_PASSWORD / API_KEY y el arranque aborta.
docker compose --env-file .env -f docker/docker-compose.yml up -d       # los 7 servicios
docker compose --env-file .env -f docker/docker-compose.yml ps          # estado / healthchecks
docker compose --env-file .env -f docker/docker-compose.yml logs -f app # seguir el backend
```

El orden de arranque está gobernado por `depends_on` con `condition`:

```mermaid
sequenceDiagram
    participant DC as docker compose
    participant PG as postgis
    participant RD as redis
    participant DSP as docker-socket-proxy
    participant SBX as sandbox
    participant IMG as imagery-mcp
    participant APP as app
    participant FE as frontend

    DC->>PG: iniciar + healthcheck (pg_isready)
    DC->>RD: iniciar + healthcheck (redis-cli ping)
    DC->>DSP: iniciar
    DC->>SBX: iniciar
    DC->>IMG: iniciar (rehúsa si IMAGERY_MCP_KEYS vacío)
    Note over PG: primer arranque: init-db/ corre<br/>00 postgis → 01/02 restore → 03 gis_readonly
    PG-->>DC: service_healthy
    RD-->>DC: service_healthy
    DC->>APP: iniciar (espera postgis+redis healthy, sandbox+proxy started)
    APP->>APP: enforce_production_auth / enforce_production_debug
    APP-->>DC: /health → service_healthy
    DC->>FE: iniciar (espera app healthy) → nginx sirve :3000
```

---

## 9.5 Preparación de la base de datos (init-db, rol `gis_readonly`, migraciones)

La BD se prepara de dos formas según si el volumen es nuevo o ya existe. Desde F7, el
esquema que es de la **aplicación** (`ws_meta` y `plataforma`: 06 y 10 de `init-db`) tiene
versión: lo gestiona Alembic (`src/geo_copilot/migraciones/`, ver «Cambiar el esquema» abajo).
Los datos de dominio y los roles con contraseña siguen siendo de `init-db`.

**Volumen fresco** — los scripts de `docker/init-db/` corren automáticamente en
orden alfabético (una sola vez):

| Script | Qué hace |
|--------|----------|
| `00_postgis.sql` | `CREATE EXTENSION postgis` + `postgis_topology`. Sin esto, `ST_*` y `geometry_columns` no existen. |
| `01_restore_catastro.sh` | Crea el rol `catastro` si el dump lo referencia y hace `pg_restore` del dump real. |
| `02_catastro_real.dump` | Dump custom (~500 MB) del catastro real (esquema `catastro`: `lotes`, `construcciones`, ...). |
| `03_gis_readonly.sql` | Crea el rol **`gis_readonly` NOLOGIN** con `SELECT` solo sobre `catastro`/`public`, y lo otorga a `geo_user`. |
| `04_geo_app_role.sql` / `05_geo_app_password.sh` | Rol de aplicación **`geo_app`** (NOSUPERUSER) y su contraseña, desde `GEO_APP_PASSWORD`. |
| `06_workspace.sql` | F2: rol **`geo_workspace`** (NOLOGIN, `CREATE` en la BD), esquema `ws_meta` con el catálogo `ws_meta.datasets`; `gis_readonly` lo lee. **Esquema de la aplicación** (Alembic, revisión `0001`). |
| `07_srid_colombia.sql` | F2: registra **EPSG:9377** (MAGNA-SIRGAS origen nacional) en `spatial_ref_sys` si la imagen de PostGIS no lo trae. |
| `08_mcp_lector.sh` | Rol de LOGIN **`mcp_lector`** (solo lectura del catastro) para el servidor MCP de SQL, con `MCP_SQL_LECTOR_PASSWORD`; sin ella lo avisa y no lo crea. |
| `09_catastro_comentarios.sql` | El alcance de cada tabla del catastro como `COMMENT` (lo lee el generador de SQL). |
| `10_plataforma.sql` | F6: esquema **`plataforma`** (dueños de sesión, conexiones por organización, pins, auditoría) y dueño en `ws_meta.proyectos`. **Esquema de la aplicación** (Alembic, revisión `0002`). |

**Por qué el rol `gis_readonly`** (SEC-02): el SQL que genera el LLM ya corre en
una transacción read-only con `statement_timeout`, pero corría como `geo_user`,
que puede **leer cualquier tabla del cluster**. Antes de ejecutar, el GISAgent
hace `SET LOCAL ROLE gis_readonly`, acotando la lectura a los esquemas de dominio.
El backend chequea `pg_has_role(...)` una vez y cachea el resultado por proceso.

**BD ya desplegada** — `init-db/` NO se re-ejecuta sobre un volumen con datos.
Para añadir el rol a una instalación existente, aplica la migración idempotente:

```bash
docker exec -i geo_copilot_db psql -U geo_user -d geo_copilot \
  < docker/migrations/2026-07-20_gis_readonly.sql
# Luego reinicia `app` para que re-chequee la disponibilidad del rol:
docker compose restart app
```

Qué pone al día cada parte de una BD existente:

| Qué | Cómo, en una BD ya desplegada |
|---|---|
| `06_workspace.sql` y `10_plataforma.sql` (esquema de la aplicación: `ws_meta`, `plataforma`) | **Alembic**: `python -m geo_copilot.migraciones` (abajo; en producción lo hace el servicio `migrar` antes de la app) |
| `07_srid_colombia.sql` (EPSG:9377) | **Ni Alembic ni ningún script de `docker/migrations/`**: es un dato de PostGIS, no esquema de la aplicación. Si falta (`SELECT count(*) FROM spatial_ref_sys WHERE srid = 9377` da 0), aplicarlo a mano, es idempotente: `docker exec -i geo_copilot_db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < docker/init-db/07_srid_colombia.sql` |
| `03` (`gis_readonly`), `08` (`mcp_lector`), `09` (comentarios del catastro) | sus scripts de `docker/migrations/` (`2026-07-20_gis_readonly.sql`, `2026-09-27_mcp_lector.sh`, `2026-09-27_catastro_comentarios.sh`) |

`docker/migrations/2026-09-25_workspace.sh` (06) y `2026-09-28_plataforma.sh` (10) son
**históricos, anteriores a F7**: aplican el SQL de `init-db` tal cual, sin registrar la versión en
`alembic_version`, y no traen los cambios que lleguen por revisiones nuevas (el de F2 aplica solo
06, no 07). Para 06 y 10 usar Alembic; en una BD de desarrollo que ya pasó por esos scripts,
las revisiones base (idempotentes) vuelven a aplicar el mismo SQL, completan lo que faltara y
registran la versión. Tras el workspace de F2, reconstruir app y sandbox (pyarrow + el
volumen `ws_exports`): `docker compose --env-file .env -f docker/docker-compose.yml up -d --build app sandbox`.

Las migraciones de Alembic corren con el rol dueño (`geo_user`, nunca `geo_app`). Una BD de
desarrollo anterior a F7 que no pasó por aquí falla al guardar un proyecto con «relation
ws_meta.proyectos does not exist»: la app ya no crea tablas en ejecución. El PostGIS de
desarrollo se publica en **`127.0.0.1:5433`** (no 5432, que suele ser un Postgres nativo del host:
la orden iría a otra BD); la variable se exporta para que la vean las dos órdenes:

```bash
export MIGRACIONES_DATABASE_URL=postgresql://geo_user:<POSTGRES_PASSWORD>@localhost:5433/geo_copilot
python -m geo_copilot.migraciones          # upgrade head
python -m geo_copilot.migraciones actual   # qué revisión tiene la BD
```

**Cambiar el esquema (`06_workspace.sql` o `10_plataforma.sql`)** — `init-db` solo crea los
volúmenes NUEVOS, y una BD que ya está en `head` no vuelve a correr nada: editar el SQL de
`init-db` **no basta**. Además del cambio en `init-db`, hay que:

1. Crear una revisión `src/geo_copilot/migraciones/versions/000N_<nombre>.py` con
   `down_revision` = la anterior, que lleve el cambio a las BD existentes con su SQL **congelado**
   en la propia revisión (`SQL = r"""…"""` + `ejecutar_sql(SQL)`; idempotente, sin `\set`).
   Las revisiones no leen archivos: la imagen no lleva `init-db`.
2. Declarar en esa revisión la huella del SQL de `init-db` que deja aplicado:
   `INIT_DB = {"10_plataforma.sql": "<huella>"}`, con la huella de
   `python -c "from geo_copilot import migraciones as m; print(m.huella(m.script_sql('10_plataforma.sql')))"`.

La huella es la del SQL **efectivo**: no cuentan las líneas que son solo un comentario `--` ni
las vacías, así que corregir una cabecera no exige revisión; cualquier otra edición de 06/10, sí
(también una línea de SQL con un comentario al final). `tests/test_f7_auditoria_migraciones.py` falla si un SQL de `init-db` cambió sin ella
(`init_db_sin_revision()`), y también si aparece en `init-db` un archivo nuevo que no sea de
dominio ni lo recoja una revisión.

---

## 9.6 Capa semántica: introspección vs. YAML

La capa semántica (cómo el sistema sabe qué tablas/columnas existen) hoy se
descubre **automáticamente** desde la BD conectada:

- `semantic/introspector.py` consulta `information_schema` + `geometry_columns` +
  `pg_catalog` respetando los GRANTs de la sesión (solo ve lo que el usuario
  puede ver). Detecta tablas, columnas, tipos, geometría y SRID reales.
- `semantic_layer/entities.yaml` pasó de ser **fuente primaria** a ser
  **overrides opcionales**: aliases custom, descripciones humanas, campos
  sensibles. Solo actúa como fallback si no hay BD conectada.

Consecuencia práctica: no hace falta mantener el YAML sincronizado a mano con el
esquema — al conectar otra BD, el sistema se re-introspecciona.

---

## 9.7 Stack de desarrollo local (sin Docker)

```bash
# Backend
cd <root>
python -m venv .venv && source .venv/bin/activate   # PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[dev]
# carga tu .env (o usa python-dotenv / direnv)
uvicorn geo_copilot.api.app:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev     # Vite en :5173, proxy a localhost:8000
```

PostGIS + Redis para desarrollo (sin levantar todo el stack):

```bash
cd docker
docker compose up -d postgis redis
```

### Plataformas soportadas para el sandbox de Python

El `PythonSandbox` con backend **`subprocess`** (solo dev y tests — el default
es `docker`) usa primitivas **POSIX** (`resource.RLIMIT_AS`, límites de memoria,
signals) que **no existen en Windows nativo**. La política oficial es:

| Plataforma | Sandbox `subprocess` | Notas |
|------------|:--------------------:|-------|
| Linux / macOS nativo | ✅ | Funciona end-to-end. |
| Windows + WSL2 | ✅ | Es Linux por dentro. |
| Windows / Linux / macOS + Docker | ✅ | En despliegue el backend es **`docker`**, no subprocess: el código corre en el contenedor `sandbox`. Es la contención real de RCE/egreso. |
| Windows **nativo** (sin WSL ni Docker) | ❌ | `sandbox.py` **rechaza** el backend `subprocess` en SO no-POSIX. El resto del producto funciona; solo fallan las features que ejecutan código Python (operaciones espaciales en memoria, análisis/ML). |

Los tests del subprocess (`test_gis_agent.py::TestPythonSandbox`) están marcados
para **saltarse en Windows** y no romper la suite local; en CI Linux corren.

**Dev en Windows nativo:** levanta el stack completo con `docker compose up -d` y
conecta tu IDE al contenedor `app` (VS Code Dev Containers o remote debug).

### Límites por ejecución del sandbox

Dos son configurables por `.env`; el resto están fijos en
`agents/gis_agent/sandbox_runner.py:110-128` y se aplican **antes** de que corra
una sola línea del código del LLM:

| Recurso | Valor | Dónde |
|---|---|---|
| Reloj de CPU (`RLIMIT_CPU`) | `SANDBOX_TIMEOUT` = **60 s** (rango 5–300) | `core/config.py:105-115` |
| Corte duro de pared | **`timeout + 5`** = 65 s por defecto — es el que realmente mata el proceso | `sandbox.py:433,471` |
| Memoria virtual (`RLIMIT_AS`) | `SANDBOX_MEMORY_MB` = **2048 MB** (rango 512–16384) | `core/config.py:116-126` |
| Procesos/threads (`RLIMIT_NPROC`) | **512** | `sandbox_runner.py:124` |
| Descriptores de archivo (`RLIMIT_NOFILE`) | **256** | `sandbox_runner.py:126` |
| Volcado de core (`RLIMIT_CORE`) | **0** — un crash no puede filtrar secretos de memoria al disco | `sandbox_runner.py:128` |
| Tamaño de salida (`RLIMIT_FSIZE`) | **1 MB** por defecto | `sandbox_runner.py:118` |
| `tmpfs` del contenedor | **100 MB** en `/tmp`, el único sitio escribible | `docker-compose.yml:243-246` |

> **Por qué `NPROC` es 512 y no 64.** `NPROC` cuenta *todos* los procesos y
> threads del usuario. Con 64, el Python científico con threading
> (numba/joblib dentro de `esda`/`libpysal` levantan threads para el JIT)
> reventaba con `EAGAIN`/`BlockingIOError` en pleno análisis espacial. 512
> sigue frenando una fork-bomb pero deja pasar el threading legítimo — que
> además se acota con `NUMBA_*` y `OPENBLAS_NUM_THREADS=1`
> (`sandbox.py:79-92`).

**Ojo con `SANDBOX_MEMORY_MB`**: OpenBLAS/numpy/geopandas reservan ~1-2 GB
**virtuales** solo con importarse, aun con un thread. Por debajo de 1024 MB el
child muere antes de ejecutar el código del usuario.

---

## 9.8 Tests

### Backend (pytest)

`pyproject.toml` fija `addopts` para que **por defecto** solo corran los tests
deterministas: `-m 'not integration and not llm'` (los marcados `integration` o
`llm` quedan excluidos salvo invocación explícita).

> **Cifras medidas el 2026-09-08** (única fuente de verdad de este repo para
> números de tests; si otro documento dice otra cosa, está desactualizado):
> **1.795 passed, 10 skipped, 92 deselected** en la corrida por defecto, sobre
> 1.616 funciones `test_*` en 120 archivos (los parametrizados expanden a ~1.712
> casos colectados). **Cobertura de sentencias: 74 %** (el gate es
> `--cov-fail-under=60`).

```bash
pytest                          # suite determinista (excluye integration + llm)
pytest tests/test_discovery_agent.py -v
pytest -k "discovery" --no-cov
pytest --tb=short -q            # output compacto
```

Tres markers:

| Marker | Requiere | Cómo se corre |
|--------|----------|---------------|
| `integration` | PostGIS real (fixture `postgis_pool`; `pytest.skip` si no hay Docker) | `pytest -m integration` |
| `llm` | LLM real (`get_real_llm_or_skip`, hace ping y skip si no responde) | `pytest -m llm` |
| `agentic_bench` | Grafo + LLM + BD reales | `pytest -m agentic_bench` (ver `tests/agentic_bench/README.md`) |

Stack dedicado para tests de integración (Postgres/PostGIS en el puerto **5434**
para no chocar con el `5433` de dev):

```bash
docker compose -f docker-compose.test.yml up -d
pip install -e .[dev,integration]
pytest -m integration
docker compose -f docker-compose.test.yml down -v
```

Cobertura: `pytest --cov=geo_copilot --cov-report=html` → `htmlcov/index.html`
(gate en `--cov-fail-under=60`; real **74 %** al 2026-09-08). El comentario de
`pyproject.toml:151` todavía dice ~63 %: es el valor viejo, no lo tomes como bueno.

### Frontend

```bash
cd frontend
npm test                    # vitest (unit, jsdom) — 31 archivos, 464 casos
npm run test:coverage       # cobertura vitest
npm run test:e2e            # Playwright MOCKEADO (page.route), 15 specs / 28 casos, 1 worker
npm run test:e2e:real       # Playwright contra el stack Docker real (requiere :3000 arriba)
```

- `test:e2e` usa `playwright.config.ts` contra `frontend/e2e/` (backend y WS
  mockeados, deterministas, headless).
- `test:e2e:real` usa `playwright.integration.config.ts` contra
  `frontend/e2e-integration/` (sin mocks, timeouts largos, stack real).

### Qué corre CI realmente

El workflow (`.github/workflows/ci.yml`) corre: `ruff check` + `pytest -q`
(hereda el `addopts` → **excluye integration y llm**) para backend, y
`npm test -- --run` + `npm run build` para frontend. **Ningún job de CI corre
Playwright, ni `-m integration`, ni `-m llm`, ni el benchmark** — esas suites son
de ejecución manual/local.

---

## 9.9 Logs y observabilidad

- **Logs estructurados**: `LOG_FORMAT=json` (default) produce líneas JSON con
  `request_id`, `session_id`, `level`, `module`, `timestamp`, `message`.
  Ingestibles por CloudWatch / Datadog / Loki. `LOG_FORMAT=text` para dev legible.
- **Request-ID**: cada request HTTP recibe un UUID que aparece en todos sus logs.
- **Banners por nodo** (`DEBUG_CONSOLE_OUTPUT=true`, solo dev): cada nodo del
  grafo imprime un banner ASCII al iniciar:
  ```
  ============================================================
  [DataAgent] Validando datos...
     -> Intent: search_external, Entities: ['IGAC']
  ============================================================
  ```
- **Métricas**: `imagery-mcp` expone `/metrics` (requiere Bearer); `/health` es
  público (liveness). El backend no trae Prometheus por defecto (slot vía middleware).

---

## 9.10 Seguridad: checklist de producción

- [ ] `API_KEY` definida y **no vacía** (`""` → fail-closed, aborta).
- [ ] `ENVIRONMENT=production` con `DEBUG=false` (ambos verificados al arrancar).
- [ ] `CORS_ORIGINS` sin `"*"` y `CORS_ALLOW_ALL=false`.
- [ ] `DATABASE_READ_ONLY=true` y rol **`gis_readonly`** presente (ver 9.5).
- [ ] `SANDBOX_BACKEND=docker` (contenedor endurecido, NO subprocess dentro de `app`).
- [ ] `app` **no** monta el socket de Docker crudo — llega al daemon solo vía
      `docker-socket-proxy` (allowlist EXEC).
- [ ] `IMAGERY_MCP_KEYS` con scopes correctos; el MCP nunca se expone al navegador
      (todo pasa por el proxy de la app que inyecta el Bearer server-side).
- [ ] `HITL_ENABLED=true`.
- [ ] `SESSION_BACKEND=redis` si hay múltiples workers.
- [ ] HTTPS terminado en reverse proxy (nginx/traefik); `TRUST_PROXY_HEADERS=true`
      **solo** si hay un proxy de confianza que setea `X-Forwarded-For`.
- [ ] PostGIS y `imagery-mcp` no expuestos a internet (solo red interna).
- [ ] Secrets en variables de entorno o gestor de secretos (Vault, AWS SM),
      nunca en el repo.

Riesgos cubiertos en código:

- **SSRF**: `URLValidator` bloquea IPs privadas/loopback/metadata y esquemas no
  HTTP(S); los proxies de teselas validan `scene_id`/`z`.
- **SQL injection**: el GISAgent valida el SQL, lo corre read-only con
  `statement_timeout`, `LIMIT` capeado y rol `gis_readonly`.
- **RCE / egreso del código del LLM**: sandbox `network:none` + `read_only` +
  `cap_drop ALL` + non-root, alcanzado solo por `docker exec` vía socket-proxy.
- **DoS por GeoJSON enorme**: `validate_geojson()` con `max_geojson_features` en
  REST y WebSocket (paridad).
- **Approval hijacking / IDOR** (SEC-3): aprobaciones atadas a `session_id`; otra
  sesión no puede confirmarlas (404/403).
- **Imagery fail-closed**: `TOOL_SCOPES` en `services/imagery_mcp/imagery_mcp/auth.py`
  mapea cada tool a su scope; **toda tool nueva que no se agregue ahí queda
  denegada (403)**. El servicio rehúsa arrancar sin claves.
- **Error leakage**: los errores al cliente se sanitizan (sin paths ni stack
  traces); `DEBUG=true` fuera de dev está prohibido por guardrail.

Riesgo aceptado (single-tenant): una `API_KEY` por instalación, sin aislamiento
por usuario. Para multi-tenant real se requiere JWT/`user_id`, owner persistido
por sesión y checks de owner en REST + WebSocket.

---

## 9.11 Recetas de configuración

### Cambiar de modelo/proveedor LLM

```bash
# OpenAI → Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-5-20250929
```

El cliente se reconstruye al reinicio; los prompts son provider-agnostic.

### Cambiar la región por defecto de Discovery

Edita `config/discovery_catalog.yaml` (montado `:ro`, sin rebuild):

```yaml
default_region: peru   # en vez de colombia
```

Asegúrate de que `regions.peru` exista con sus `entities`/`zones`/`publishers`.
El override `DISCOVERY_DEFAULT_REGION` tiene prioridad sobre el YAML.

### Añadir un país o entidad a Discovery

Solo edita `config/discovery_catalog.yaml` (entidades con `aliases`/`owners`/
`sources`/`tags`, zonas con `bbox`). **No requiere tocar código.**

### Añadir una tool nueva al imagery-mcp

1. Regístrala con `@mcp.tool()` en `services/imagery_mcp/imagery_mcp/server.py`
   (que devuelva un `GeoResult`, ver `imagery_mcp/georesult.py`).
2. **Añádela a `TOOL_SCOPES`** en `auth.py` con su scope (`imagery:read` o
   `imagery:compute`). Si no, queda inalcanzable con **403** (fail-closed).
3. Si su nombre no casa con `tools.allow` del servidor en
   `config/mcp_servers.yaml` (hoy `imagery_*`), añádelo ahí. La app la recoge
   en la siguiente revalidación (`MCP_REFRESH_S`) o al reiniciar; no hay que
   tocar el núcleo ni el frontend.

### Enchufar un servidor MCP nuevo

Declara el servidor en `config/mcp_servers.yaml` (id, url, `auth` con
`secret_ref: env:VAR`, `tools.allow`, `policy`, `tiles.prefixes` si sirve
teselas), pon la variable del secreto en el entorno de `app` y reinicia. Por
defecto queda `trust: untrusted`. Estado y tools: `GET /api/v1/connections`.
Guía completa: [12-como-enchufar-un-mcp](12-como-enchufar-un-mcp.md).

### Añadir un nuevo agente

1. Crea `agents/<nombre>_agent/agent.py` (hereda de `BaseAgent`).
2. Crea `orchestrator/nodes/<nombre>.py` con `async def run(graph, state) -> dict`.
3. En `orchestrator/graph.py`: importa el nodo, `add_node(...)`, y define las
   funciones de ruteo `_route_from_<previo>` / `_route_from_<nombre>`.
4. Añade campos a `GraphState` (TypedDict) si produce datos nuevos, y recuerda
   propagarlos en `_map_final_state`, `_run_react_mode` y `step_finalizer`.
5. Tests en `tests/test_<nombre>_agent.py`.

---

## 9.12 Troubleshooting común

| Síntoma | Causa probable | Fix |
|---------|---------------|-----|
| `imagery-mcp` no arranca (RuntimeError al inicio) | `IMAGERY_MCP_KEYS` vacío | Define el JSON de claves; el servicio rehúsa arrancar sin claves. |
| Teselas NDVI/color dan **401/403** | La clave del proxy no coincide, o falta el scope | `IMAGERY_MCP_API_KEY` (en `app`) debe estar entre las `IMAGERY_MCP_KEYS` del MCP con scope `imagery:compute`. |
| Teselas de un MCP dan **404** en `/proxy/mcp/...` | El servidor no está registrado, o la ruta no empieza por un prefijo de `tiles.prefixes` | Revisa el YAML de MCP (`id` y `tiles.prefixes`). |
| Una tool MCP aparece **deshabilitada** | Cambió su descripción/esquema (*pinning*), no está en `tools.allow`, o el servidor no responde | `GET /api/v1/connections` dice por qué; si el cambio es legítimo, `POST /api/v1/connections/{server}/tools/{tool}/approve`. |
| Teselas de imagery dan **429** | Rate limit de la ventana deslizante | Sube `rate_limit_per_min` de la clave; las teselas pesan 0.05 c/u. |
| `app` no ejecuta código Python | `SANDBOX_BACKEND=subprocess` en Windows nativo, o `sandbox` caído | Usa `docker compose up -d` (backend `docker`) o WSL2. Verifica que `sandbox` y `docker-socket-proxy` estén arriba. |
| SQL del LLM lee tablas fuera del dominio | Rol `gis_readonly` no existe (BD vieja) | Aplica `docker/migrations/2026-07-20_gis_readonly.sql` y reinicia `app`. |
| `app` aborta al arrancar en prod | `API_KEY` vacía o `DEBUG=true` fuera de dev | Define `API_KEY` no vacía; pon `DEBUG=false`. |
| Discovery devuelve 0 items para "IGAC" | Owners/sources desactualizados | Verifica `regions.colombia.entities.IGAC` en `discovery_catalog.yaml`. |
| WebSocket no reconecta | El reverse proxy no soporta WS upgrade | Configura `Upgrade`/`Connection: upgrade` en nginx (ya está en `nginx.frontend.conf`). |
| LLM lento / se corta | `LLM_REQUEST_TIMEOUT` corto | Súbelo (hasta 600) para modelos grandes. |
| HITL nunca llega al cliente | La sesión no tiene WS conectado | El frontend debe conectar el WebSocket al iniciar la sesión. |
| PostGIS choca al levantar dev + test a la vez | Ambos en `:5433` | Es correcto: dev usa `5433`, test usa `5434`. |

---

## Fin del recorrido

- Volver al índice: [README](README.md).
- Descubrimiento de datos externos: [07-discovery](07-discovery.md).
- Añadir/modificar un agente: [02-agentes](02-agentes.md).
- Cómo probarlo todo en vivo: [11-como-probar-todo](11-como-probar-todo.md).
