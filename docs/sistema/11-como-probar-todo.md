# 11. Cómo probar todo — guía paso a paso

Esta guía explica, de principio a fin, cómo **levantar el sistema** y **correr
todas las suites de prueba** de GEO_COPILOT. Está pensada para que alguien que
recién clona el repo pueda:

1. Montar el stack completo con Docker (`postgis`, `redis`, `app`, `frontend`,
   `sandbox`, `docker-socket-proxy`, `imagery-mcp`).
2. Correr los tests del **backend** (`pytest`, deterministas por defecto).
3. Correr los tests del **frontend** (`vitest`).
4. Correr los **E2E de navegador**: mockeados (deterministas, Playwright) y
   reales (contra el stack completo, `test:e2e:real`).

> **Convención de rutas:** los ejemplos usan `$REPO_ROOT` para la raíz del
> repositorio. Defínela una vez y el resto se copia tal cual:
>
> ```bash
> # bash / zsh (Linux, macOS, WSL2, Git Bash)
> export REPO_ROOT=$(git rev-parse --show-toplevel)
> ```
>
> ```powershell
> # PowerShell
> $env:REPO_ROOT = (git rev-parse --show-toplevel)
> ```
>
> Si todavía no has clonado, pon la ruta a mano. Los comandos `bash`/`sh`
> funcionan igual en WSL2, macOS y Linux.

> ⚠️ **Windows nativo:** el sandbox de Python **no** funciona como
> `subprocess` en Windows nativo (usa APIs POSIX). Para desarrollo local en
> Windows, corre el stack con Docker (`SANDBOX_BACKEND=docker`) o usa WSL2. Ver
> [§9.7 sobre plataformas soportadas](09-configuracion-y-deploy.md#97-stack-de-desarrollo-local-sin-docker).

---

## 0. Mapa mental: qué se prueba y dónde

El sistema tiene **dos cortes** de pruebas que conviene no mezclar:

- **Deterministas** (no tocan la red ni el LLM): corren siempre, son las que
  ejecuta CI. Aquí viven la gran mayoría de los 1.795 tests de backend y los
  31 archivos de tests de frontend (464 casos).
- **Con dependencias externas** (PostGIS real, LLM real, stack completo): se
  activan **a propósito** con *markers* de pytest o scripts dedicados. CI **no**
  las corre; son de ejecución manual/local.

```mermaid
flowchart TD
    subgraph Deter["Deterministas (corre CI y tu maquina)"]
        BE["Backend: pytest (excluye integration y llm)"]
        FE["Frontend: vitest (jsdom)"]
        E2EM["E2E mockeado: Playwright (page.route, sin backend)"]
    end

    subgraph Ext["Con dependencias externas (manual, NO en CI)"]
        INT["pytest -m integration (PostGIS real)"]
        LLM["pytest -m llm (LLM real, cuesta tokens)"]
        BENCH["pytest -m agentic_bench (grafo + LLM + BD reales)"]
        E2ER["E2E real: test:e2e:real (stack Docker completo)"]
    end

    Dev["Desarrollador"] --> Deter
    Dev --> Ext
    CI["GitHub Actions (.github/workflows/ci.yml)"] --> Deter
    CI -. "no ejecuta" .-> Ext
```

### Tabla de tipos de test

| Tipo | Ubicación | Cómo se corre | Requiere |
|------|-----------|---------------|----------|
| **Unit backend** | `tests/test_*.py` (sin marker) | `pytest` (default) | nada extra |
| **Unit frontend** | `frontend/src/**/*.test.ts(x)` | `npm test -- --run` | `npm install` |
| **Discovery / arcgis-mcp** | `tests/test_arcgis_mcp.py` (servidor), `test_hub_search.py` (normalización y parámetros en el servidor, ranking en el núcleo), `test_data_agent_via_mcp.py`, `test_route_discovery.py`, `test_discovery_agent.py`, `test_discovery_ssrf.py` | `pytest` (default) | nada extra (HTTP de ArcGIS y servidor MCP simulados) |
| **HITL full flow** | `tests/test_hitl_full_flow.py` | `pytest` (default) | nada extra |
| **WS handshake** | `tests/test_websocket_handshake_sec4.py` | `pytest` (default) | nada extra |
| **Integration PostGIS** | tests con `@pytest.mark.integration` | `pytest -m integration` | `docker-compose.test.yml` arriba |
| **LLM real** | tests con `@pytest.mark.llm` | `pytest -m llm` | API key de LLM válida |
| **Benchmark agéntico** | `tests/agentic_bench/` | `pytest -m agentic_bench` | grafo + LLM + BD reales |
| **imagery-mcp** | `tests/test_imagery_mcp.py` | `pytest` (unit) / `-m integration` (STAC real) | red a STAC solo para integration |
| **MCP Hub** | `tests/test_mcp_*.py` (`test_mcp_hub.py` cubre `llamar_directo` y `tools.agent`), `test_imagery_via_hub.py`, `test_geo_mcp_kit.py` (+ `test_llm_mcp_escala.py` con `-m llm`) | `pytest` (default) | nada extra (servidores MCP simulados) |
| **E2E mockeado** | `frontend/e2e/*.spec.ts` (~17 specs) | `npm run test:e2e` | Chromium de Playwright |
| **E2E real** | `frontend/e2e-integration/*.spec.ts` (`real-*`, `fase-*`, `regresion-nucleo`) | `npm run test:e2e:real` | stack Docker sirviendo `:3000` |

> **Regla de oro del backend:** `pytest` a secas **ya excluye** `integration` y
> `llm` (está en `addopts` de `pyproject.toml`:
> `-m 'not integration and not llm'`). No tienes que pasar el filtro a mano
> para el uso diario.

---

## 1. Setup inicial (una vez)

### Paso 1 — Dependencias de Python

```bash
cd "$REPO_ROOT"

# (opcional) venv
# python -m venv .venv && .venv\Scripts\activate

pip install -e ".[dev,integration]"
```

**Qué instala:**
- `dev`: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `black`,
  `pytest-recording>=0.13.0` + `vcrpy>=6.0.0` (siguen en el extra, aunque desde
  T5.2 ningún test de Discovery usa cassettes).
- `integration`: `testcontainers[postgres]>=4.0` (fixture alternativa al
  `docker-compose.test.yml` para levantar PostGIS desde el propio test).

Verifica que entró:

```bash
python -c "import vcr, pytest_recording; print('VCR OK')"
python -c "import asyncpg; print('asyncpg OK')"
```

### Paso 2 — Dependencias del frontend

```bash
cd frontend
npm install
```

Trae Vite + React + `vitest` + `@playwright/test` + `@testing-library/*` (ya
declaradas en `package.json`).

Para los E2E de navegador hay que descargar el binario de Chromium (una vez):

```bash
npx playwright install chromium
```

### Paso 3 — (Opcional) Activar el test de DataDiscoveryPanel

El archivo `frontend/src/components/DataDiscoveryPanel.test.tsx.disabled` viene
desactivado para no exigir `@testing-library/*` en instalaciones mínimas. Si ya
hiciste `npm install`, puedes activarlo con un rename:

```bash
cd frontend/src/components
mv DataDiscoveryPanel.test.tsx.disabled DataDiscoveryPanel.test.tsx
```

(Luego commitea el rename junto al `package-lock.json`.)

### Paso 4 — (Opcional) PostGIS de tests para `-m integration`

```bash
cd "$REPO_ROOT"
docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.test.yml ps   # espera "healthy"
```

Este compose levanta un `postgis/postgis:15-3.3` **dedicado** en el puerto host
**5434** (para no chocar con el `:5433` del PostGIS de dev). El seed
`tests/fixtures/seed_catastro.sql` se carga en el primer arranque (~20
construcciones + ~5 lotes en el schema `catastro`, mismo formato que el dump
real). La fixture `postgis_pool` (`tests/conftest.py`) apunta por defecto a
`postgresql://gc_test:gc_test_pw@localhost:5433/geocopilot_test`; ajusta
`TEST_DATABASE_URL` si usas el `:5434` de este compose.

### Paso 5 — Tests de Discovery (sin cassettes desde T5.2)

Antes los tests de Discovery reproducían respuestas del Hub grabadas con VCR
(`test_discovery_with_cassettes.py`). Ese test se borró en T5.2 junto con los
conectores del núcleo. Hoy Discovery va por el servidor MCP `arcgis` y sus
tests corren offline sin grabar nada:

```bash
cd "$REPO_ROOT"
pytest tests/test_arcgis_mcp.py tests/test_hub_search.py tests/test_data_agent_via_mcp.py \
       tests/test_route_discovery.py tests/test_mcp_hub.py -v
```

- `test_arcgis_mcp.py`: el servidor (búsqueda, descripción, consulta paginada por OID, guarda SSRF).
- `test_hub_search.py`: normalización y parámetros del Hub (en el servidor) y ranking (en el núcleo).
- `test_data_agent_via_mcp.py`: búsqueda y carga por chat a través del servidor.
- `test_route_discovery.py`: contrato de `/discovery/*` (incluido el 503/502 si el servidor falla).
- `test_mcp_hub.py`: `llamar_directo` y `tools.agent`.

---

## 2. Levantar el stack completo (Docker)

Para los E2E reales y para probar el producto a mano, levanta el stack de
`docker/docker-compose.yml`. Son **ocho servicios** por defecto en dos redes
(los de los perfiles `examples` y `connectors` no arrancan si no los pides):

```mermaid
flowchart LR
    subgraph geo["red geo_network"]
        FE["frontend (nginx BFF, :3000)<br/>inyecta X-API-Key en /api y /ws"]
        APP["app (FastAPI + LangGraph, :8000)"]
        PG[("postgis (:5433 host)<br/>rol gis_readonly")]
        RD[("redis (:6379)")]
        IMG["imagery-mcp (:9100 interno)<br/>auth Bearer + TOOL_SCOPES"]
        ARC["arcgis-mcp (:9400, host solo 127.0.0.1)<br/>Discovery de ArcGIS"]
    end
    subgraph proxynet["red docker_proxy (internal)"]
        DSP["docker-socket-proxy<br/>(allowlist EXEC)"]
    end
    SBX["sandbox<br/>(network:none, read_only, cap_drop ALL)"]

    Nav["Navegador"] --> FE
    FE --> APP
    APP --> PG
    APP --> RD
    APP -- "Bearer + scopes" --> IMG
    APP -- "Bearer (llamar_directo)" --> ARC
    APP -- "DOCKER_HOST=tcp://..." --> DSP
    DSP -- "docker exec (solo)" --> SBX
```

### Arrancar todo

```bash
cd "$REPO_ROOT"

# 1) Configura .env (LLM API key, API_KEY del backend, etc.)
#    Ver docs/sistema/09-configuracion-y-deploy.md §9.2

# 2) Levanta el stack
docker compose -f docker/docker-compose.yml up -d

# 3) Verifica que 'app' quede healthy y el frontend sirva en :3000
docker compose -f docker/docker-compose.yml ps
```

- **UI:** http://localhost:3000 (nginx proxea `/api` y `/ws` al backend e
  **inyecta la `X-API-Key` server-side** — la clave nunca llega al navegador).
- **Backend:** http://localhost:8000 (`/health` para liveness).
- **PostGIS:** host `:5433` (interno `postgis:5432`).

### imagery-mcp (servicio aparte)

`imagery-mcp` es un **microservicio MCP independiente** (STAC→COG Sentinel-2)
que expone 5 tools (`imagery_search_scenes`, `imagery_ndvi`, `imagery_change`,
`imagery_zonal_stats`, `imagery_composite`) y las teselas dinámicas
(`/tiles*`). El compose ya lo levanta como parte del stack, pero puedes
arrancarlo/rebuildar solo:

```bash
docker compose -f docker/docker-compose.yml up -d --build imagery-mcp
```

Puntos clave para probarlo:
- **No arranca sin claves, ni en dev.** Necesita `IMAGERY_MCP_KEYS` (JSON con
  `key` + `scopes` + `rate_limit_per_min`). El compose las declara **`:?`**
  (`${IMAGERY_MCP_APP_KEY:?}`, `${IMAGERY_MCP_KEYS:?}`): si no las defines, el
  stack **aborta**. Ya no hay default inyectado — lo hubo, y era una clave
  escrita en el repositorio. Genera la tuya con `openssl rand -hex 24` y usa
  **la misma** en las dos variables.
- La app lo alcanza por la red interna a través del **MCP Hub**: es el servidor
  `id: imagery` de `config/mcp_servers.yaml` (`url: http://imagery-mcp:9100/mcp`,
  `secret_ref: env:IMAGERY_MCP_API_KEY`), con `Authorization: Bearer <IMAGERY_MCP_API_KEY>`.
  Ya no existe `IMAGERY_MCP_URL`. Comprueba que el hub lo ve con
  `curl -H "X-API-Key: $API_KEY" http://localhost:8000/api/v1/connections`.
- **Auth fail-closed:** cada tool exige un scope declarado en
  `TOOL_SCOPES` (`services/imagery_mcp/imagery_mcp/auth.py`). **Cualquier tool
  nueva que no se añada ahí queda inalcanzable con 403** — es el chequeo a
  recordar al extender el servicio.
- El navegador **nunca** habla directo con el MCP: consume las teselas vía el
  **proxy genérico de la app** (`/api/v1/proxy/mcp/imagery/tiles/...`,
  `/tiles-diff/...`, `/tiles-rgb/...`), que reinyecta el Bearer server-side.
- Desde la UI: drawer **Herramientas · Servicios conectados** (`McpToolsPanel`),
  que arma el formulario de cada tool desde su `input_schema`. Para probar el
  enchufe de otro servidor, levanta el ejemplo `hello-geo`
  (`docker compose -f docker/docker-compose.yml --profile examples up -d hello-geo`)
  y regístralo en el YAML; ver [12-como-enchufar-un-mcp](12-como-enchufar-un-mcp.md).

Prueba rápida de humo del MCP (liveness, sin auth):

```bash
curl -s http://localhost:9100/health
```

### arcgis-mcp (Discovery de ArcGIS)

Desde T5.2 el panel "Datos" y la búsqueda/carga por chat van por el servidor
MCP `arcgis` (`services/arcgis_mcp/`): el núcleo ya no tiene conectores de
ArcGIS. Sin él, `/discovery/search` responde **503** y `/discovery/load` **502**.

- **No arranca sin claves.** Define en `.env` `ARCGIS_MCP_APP_KEY` y
  `ARCGIS_MCP_KEYS` (JSON; el `key` debe ser **el mismo** valor que
  `ARCGIS_MCP_APP_KEY`, scope `arcgis:read`). Ver `.env.example`.
- Es el servidor `id: arcgis` de `config/mcp_servers.yaml` con
  `tools: { agent: [] }`: el LLM no ve sus tools; las usa el núcleo con
  `McpHub.llamar_directo`. Aparece igual en `GET /api/v1/connections`.
- `ARCGIS_ALLOWED_DOMAINS` (opcional, separado por comas) restringe a qué
  dominios puede ir; siempre bloquea IPs internas y fija la IP resuelta.
- Rebuild solo: `docker compose -f docker/docker-compose.yml up -d --build arcgis-mcp`.
- Prueba en la UI: tab **Datos** → buscar (p. ej. "municipios Cundinamarca") →
  **Cargar al mapa**; o en el chat, "busca equipamientos de Cundinamarca" y
  elegir una tarjeta.

Sus tests unitarios (offline) + auth/middleware viven en
`tests/test_imagery_mcp.py`; la integración real (busca escena y computa NDVI)
corre con `pytest -m integration tests/test_imagery_mcp.py`.

---

## 3. Correr el backend (pytest)

### Suite del día a día (deterministas)

```bash
cd "$REPO_ROOT"
pytest
```

Equivale a `pytest -m "not integration and not llm"` (ya está en `addopts`).
**Resultado esperado (medido el 2026-09-08):** **1.795 passed, 10 skipped,
92 deselected** — los deselected son los marcados `integration`/`llm`; los
skipped son casos como el sandbox `subprocess` en Windows nativo, que se salta
limpio. El gate `--cov-fail-under=60` debe pasar: la **cobertura real de
sentencias es 74 %**.

Si quieres ser explícito o filtrar por archivo:

```bash
pytest -m "not integration and not llm"      # idéntico al default
pytest tests/test_router_agent.py -q         # un archivo
pytest -k "hitl" -q                          # por patrón de nombre
```

### Integration (PostGIS real)

Con el `docker-compose.test.yml` arriba (Paso 4):

```bash
pytest -m integration -v
```

**Si Docker no está arriba:** los tests marcados `integration` **skipean
limpio** (la fixture no conecta o el schema `catastro` está vacío → `pytest.skip`),
nunca fallan en una máquina sin Docker.

### LLM real (validación de juicio agéntico)

```bash
pytest -m llm -v
```

Llama a un LLM real (cuesta tokens). `get_real_llm_or_skip()` cachea el cliente,
hace un ping y **skipea** si no hay credenciales/respuesta. Úsalo para validar
que el LLM sigue tomando buenas decisiones tras un cambio de prompts o de
proveedor.

### Benchmark agéntico

```bash
pytest -m agentic_bench -v
```

End-to-end real (grafo + LLM + BD). Idealmente dentro del contenedor `app`
(sandbox POSIX). Documentado en `tests/agentic_bench/README.md`; los resultados
se versionan en `bench_results/*.json` (p.ej.
`2026-07-19T234918Z_hybrid_fase4-v2.json`, que validó `react_policy=hybrid` vs
`off`).

### Prueba de carga (F7)

```bash
CARGA_TOKENS=<jwt1>,<jwt2>,… python scripts/prueba_carga.py --usuarios 5 --minutos 15 --base https://localhost:3443
```

Contra el stack desplegado, por la puerta pública (nginx). El límite de peticiones
va por identidad (el principal, no la IP), así que cada usuario simulado necesita
la suya: un JWT de OIDC por usuario en `CARGA_TOKENS`, que dure la corrida. Con la
API key todos son el mismo principal (`servicio:api-key`) y comparten un solo cupo:
el script lo avisa y espacia el sondeo de aprobaciones (ver su encabezado y el
runbook, [19](19-despliegue-produccion.md)). La salida (`bench_results/carga*.json`)
es **local y no se versiona**: la corrida que vale como evidencia se copia a
`docs/validacion/evidencia/fase-7/`.

### Cobertura

```bash
pytest --cov=geo_copilot --cov-report=html
# Abre htmlcov/index.html
```

---

## 4. Correr el frontend (vitest)

```bash
cd frontend

npm test              # modo watch (interactivo)
npm test -- --run     # un solo pase (lo que corre CI)
npm run test:coverage # con reporte de cobertura
```

**Resultado esperado:** los 31 archivos `*.test.ts(x)` (jsdom) pasan — **464 casos**. Los
gates de cobertura del frontend son deliberadamente bajos (`vite.config.ts`:
`lines 15`, `branches 70`, `functions 45`, `statements 15`) — deuda declarada,
pendiente de subir cuando se aborden tests de componentes React grandes.

> **Ojo con el comando:** el script `test:run` **ya no existe**. Para un pase
> único usa `npm test -- --run` (o `npm run test:coverage`).

---

## 5. E2E de navegador (Playwright)

Hay **dos** suites Playwright, con propósitos distintos:

```mermaid
flowchart TD
    subgraph Mock["E2E mockeado (frontend/e2e/)"]
        M1["playwright.config.ts"]
        M2["backend mockeado con page.route<br/>(mockInit / mockQuery en helpers.ts)"]
        M3["determinista, sin LLM ni BD"]
        M4["oraculo window.__mapTestState / __mlmap"]
    end

    subgraph Real["E2E real (frontend/e2e-integration/)"]
        R1["playwright.integration.config.ts"]
        R2["SIN mocks: stack Docker completo en :3000"]
        R3["timeouts largos (120s / 30s), aserciones flojas<br/>(toleran el no-determinismo del LLM)"]
        R4["specs: real-flow, real-frt04, real-imagery, fase-3-mcp, ..."]
    end

    CI["CI"] -. "NO corre Playwright" .-> Mock
    CI -. "NO corre Playwright" .-> Real
    Dev["Desarrollador (manual)"] --> Mock
    Dev --> Real
```

### 5.1 E2E mockeado (determinista)

No necesita backend: cada request se intercepta con `page.route`. Ideal para
validar UI, simbología, HITL, capas, imagery, etc. de forma reproducible.

```bash
cd frontend
npm run test:e2e
```

Detalles:
- Config `playwright.config.ts` → `frontend/e2e/` (**17 specs**:
  `agentic-query`, `analysis`, `client-zoom`, `discovery`, `errors-clarify`,
  `feature-popup`, `hitl`, `imagery`, `fase-3-mcp` (panel genérico de
  herramientas MCP), `layers`, `map`, `misc-ui`, `session-ui`, `symbology`,
  `tabs-table`, además de `fase-0-suelo-firme` y `fase-2-workspace`).
- **1 solo worker** (`workers: 1`): MapLibre usa WebGL (SwiftShader headless) y
  varios contextos WebGL en paralelo contienden y vuelven flaky el oráculo del
  render.
- El `webServer` arranca `npm run dev` (Vite en `:3000`) y reusa uno existente
  si ya está corriendo.
- Los tests afirman contra el oráculo `window.__mapTestState` (incluye
  `layers[]` por-capa) — permite verificar **qué capa concreta** fue re-estilada
  (clave para FRT-04) sin depender de nombres generados por el LLM.

### 5.2 E2E real (contra el stack completo)

No mockea nada: ejercita backend + PostGIS + LLM + HITL + imagery reales.

**Requisito:** el stack Docker levantado sirviendo en `:3000` (ver §2).

```bash
# 1) Stack arriba
docker compose -f docker/docker-compose.yml up -d

# 2) Corre los E2E reales
cd frontend
npm run test:e2e:real
```

Detalles:
- Config `playwright.integration.config.ts` → `frontend/e2e-integration/`
  (`real-flow.spec.ts`, `real-frt04.spec.ts`, `real-imagery.spec.ts`,
  `fase-3-mcp.spec.ts`, entre otros).
- Timeouts largos (test 120 s, aserciones 30 s) porque LLM + SQL + HITL tardan
  segundos.
- Aserciones **flojas pero reales**: toleran el no-determinismo del LLM
  ("apareció un número", "se renderizó una capa"), no "dice exactamente X".
- `reuseExistingServer: true`: si `:3000` ya responde (nginx del stack Docker),
  lo reutiliza; si nada responde, la suite falla con un mensaje claro pidiendo
  levantar el stack.

---

## 6. Qué corre CI (y qué NO)

`.github/workflows/ci.yml` tiene **dos jobs**, ambos deterministas:

| Job | Pasos |
|-----|-------|
| **backend** | `ruff check src/ tests/` → `pytest -q --tb=short` (hereda `addopts` → excluye `integration` y `llm`) |
| **frontend** | `npm ci` → `npm test -- --run` (vitest) → `npm run build` |

**CI no ejecuta:** Playwright (ni mockeado ni real), `-m integration`,
`-m llm`, ni `agentic_bench`. Esas suites son de ejecución manual/local. Antes
de abrir un PR, corre al menos `pytest` y `npm test -- --run` para no romper
esos dos gates.

---

## 7. Tests E2E manuales de agentes (sprints de auditoría)

Además de pytest, hay validaciones agénticas por agente en
`tests/manual_e2e/sprint_<x>_<agent>.py` (data_agent, router, python_agent,
symbology, insights, gis). Requieren **LLM real** y **no** son 100%
deterministas, por eso no están integradas a `pytest`. Corren dentro del
contenedor `app` (que tiene el sandbox POSIX):

```bash
docker cp tests/manual_e2e/sprint_b_router.py geo_copilot_app:/tmp/t.py
docker exec geo_copilot_app sh -c 'python /tmp/t.py'
```

Útiles para cazar regresiones tras tocar prompts o lógica de un agente.

---

## 7.b Validación manual en vivo (LLM + PostGIS + navegador reales)

Lo que ni pytest ni Playwright pueden juzgar: si la respuesta es **correcta y
honesta**. Esta es la pasada que se hace a mano, escribiendo en el chat contra
el stack completo. Marca ✅/❌ y anota el texto literal del agente cuando falle.

> **El criterio no es «salió algo».** Es: ¿el área está en las unidades
> correctas? ¿el 0 es un 0 real o un bug de filtro? ¿la simbología responde a lo
> que se pidió? ¿la negativa es honesta o es una invención plausible? Un
> resultado que se ve bien y está mal es el fallo que esta pasada existe para
> encontrar.

### A. Bucle ReAct

`REACT_POLICY` viene en `hybrid` de fábrica, así que las consultas complejas ya
pasan por el bucle sin tocar nada. La palanca de retroceso es `REACT_POLICY=off`.

| # | Acción | Esperado | Variantes |
|---|---|---|---|
| A1 | «trae los lotes del barrio centro y hazles un buffer de 500 metros» | `query_database` → `spatial_operation(buffer)` → `answer`. La segunda opera sobre la capa de la primera | buffer de 1 km; «centroide de cada lote»; «área de cada lote en m²» |
| A2 | Algo imposible: «dame el clima de mañana» | Llama a `answer` **honestamente** («no tengo esa capacidad»). No inventa | «tráfico en tiempo real»; «la ruta más corta en carro» |
| A3 | Varias cosas de un tirón: «cuenta los lotes, sácales el centroide y ponlos azules» | Encadena tres tools en orden lógico y responde | reordenar el pedido; pedir algo de 4-5 pasos |
| A4 | Reflexión (`REACT_MAX_REFLECTIONS=1`): «muéstrame escuelas y hospitales» cuando la BD solo tiene uno de los dos | Si la primera respuesta no cubre ambos, **sigue trabajando** en vez de cerrar en evasiva; si de verdad no se puede, lo dice | ponerlo en `0` y comparar |
| A5 | Circuit-breaker: un pedido confuso que el modelo no resuelva | Corta a las `REACT_MAX_TOOL_CALLS` (default **8**) y responde honesto («alcancé el límite…»). **No** se cuelga | bajar `REACT_MAX_TOOL_CALLS` para verlo antes |
| A6 | Métricas por sesión: varias consultas seguidas en la misma sesión | Desde el segundo turno el prompt lleva un `HISTÓRICO DE SESIÓN:`, y una tool que falla repetido se marca como poco fiable | con `AGENT_STATE_DIR` puesto, reiniciar el backend y ver si persiste |

### B. SQL y PostGIS — donde más caro sale mentir

| # | Acción | Esperado | Variantes |
|---|---|---|---|
| B1 | «área en m² de cada lote» | El área sale en **metros cuadrados correctos**, reproyectando a la zona UTM **real** de los datos. Contrasta con un valor que conozcas | distancias («a 200 m de…»); buffers métricos. ⚠️ Si tus datos no son de Colombia, confirma que **no** esté usando UTM 18N por defecto |
| B2 | Cero legítimo: «lotes de área > 999999999» | Reporta **0 como respuesta real**, sin fabricar datos ni reintentar a ciegas | cualquier conteo que dé 0 de verdad |
| B3 | Cero por bug: filtra con mayúsculas mal, `tipo = 'ESCUELA'` cuando el dato es `'Escuela'` | Debería **sospechar del filtro** y corregir (ILIKE) en vez de decir «no hay» | acentos; una columna casi bien escrita |
| B4 | Provoca SQL con error (columna inexistente por nombre ambiguo) | El corrector arregla y reintenta. Si el fallo es de infraestructura o de permisos, **no** reintenta en vano | forzar un timeout con una consulta pesada |
| B5 | Pregunta de seguimiento sobre lo ya devuelto: «¿cuál es el más grande?» | `follow_up` usa el contexto previo; no vuelve a consultar | pedir un atributo que **no** esté en los resultados → ahí sí debe re-consultar |

### C. Contexto de mapa y de región

| # | Acción | Esperado |
|---|---|---|
| C1 | Con una capa cargada: «ponla en rojo» | Resuelve «la» como la capa activa, sin re-consultar |
| C2 | Con el mapa centrado en una zona: «tráeme los predios en esta zona» | Filtra por el **viewport** real — el bbox de la cámara entra al prompt como `ZONA VISIBLE ACTUAL` |
| C3 | Mandar `session_region="peru"` (no está en el catálogo) | Búsqueda **neutral/global**, no anclada a Colombia. Sin `session_region` cae al default configurado (`colombia`) |

### D. Planes multi-paso (DAG)

| # | Acción | Esperado |
|---|---|---|
| D1 | «carga bomberos en rojo Y hospitales en azul» | Dos ramas **independientes**: si una falla, la otra sobrevive |
| D2 | Un plan donde falla un paso intermedio | Los pasos que **dependen** del fallido se saltan honestamente; los independientes siguen; el responder reporta el desglose (éxito / fallo / saltado) |
| D3 | Un plan que requiere elegir servicio (search → select) | El plan **pausa** pidiendo el número; al elegir, **retoma** el resto |

### E. Honestidad transversal

| # | Acción | Esperado |
|---|---|---|
| E1 | Cualquier cosa que el sistema no pueda hacer | Lo dice. No inventa — es el principio «agentic, no fallbacks» |
| E2 | Simular que no hay LLM disponible | Falla honesto («sin LLM no puedo diseñar la visualización»), no devuelve un mapa plausible inventado |
| E3 | Caracteres especiales y emojis en la consulta, en Windows | No revienta por encoding cp1252 |
| E4 | Consulta larguísima, o un plan que exceda los pasos | Mensaje claro («divide la consulta»), no un `GraphRecursionError` opaco |

> ⚠️ **`HITL_MODE=interrupt` es EXPERIMENTAL — no lo pongas en producción.** El
> sitio de SQL está migrado y es idempotente, pero **el round-trip por API no
> está cableado**: no existe endpoint de resume en `api/routes/approval.py`, y
> faltan los otros sitios más un checkpointer durable. Si quieres probarlo,
> hazlo al nivel de `graph.process` / `resume_hitl`, nunca por la API. El default
> es `blocking` y así debe quedarse.

---

## 8. Troubleshooting

### "ModuleNotFoundError: No module named 'vcr'"

```bash
pip install -e ".[dev]"
```

### "PostGIS de tests no disponible en postgresql://..."

```bash
docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.test.yml ps   # espera "healthy"
```

### `-m integration` skipea todo

Es esperado si no hay PostGIS de tests arriba. Levanta el
`docker-compose.test.yml` (Paso 4) o revisa `TEST_DATABASE_URL`.

### "Cannot find module '@testing-library/react'" en frontend

```bash
cd frontend
npm install
```

Y no renombres `DataDiscoveryPanel.test.tsx.disabled` hasta que `npm install`
termine.

### El sandbox de Python no funciona en Windows

Esperado en Windows nativo (usa APIs POSIX). Levanta el stack con Docker
(`SANDBOX_BACKEND=docker`) o usa WSL2. Ver
[09-configuracion-y-deploy.md §9.7](09-configuracion-y-deploy.md#97-stack-de-desarrollo-local-sin-docker).

### `npm run test:e2e` no encuentra Chromium

```bash
cd frontend
npx playwright install chromium
```

### `npm run test:e2e:real` falla al conectar a `:3000`

El stack real no está arriba. Levanta `docker/docker-compose.yml` (§2) y
verifica que `frontend` sirva en `:3000` y `app` esté `healthy`.

### imagery-mcp responde 403 en una tool

La tool no está en `TOOL_SCOPES`
(`services/imagery_mcp/imagery_mcp/auth.py`) o la API key no tiene el scope
(`imagery:read` / `imagery:compute`). Es fail-closed a propósito.

### imagery-mcp no arranca

Falta `IMAGERY_MCP_KEYS` (el servicio **rehúsa arrancar** sin claves). Revisa
la variable en el compose/entorno.

### Una tool MCP no aparece o sale deshabilitada

`GET /api/v1/connections` dice el estado de cada servidor y el motivo de cada
tool deshabilitada: no está en `tools.allow`, el servidor no responde, falta la
variable de su `secret_ref`, o cambió su descripción/esquema (*pinning*). En
ese último caso, si el cambio es legítimo, re-apruébala con
`POST /api/v1/connections/{server}/tools/{tool}/approve`.

---

## 9. Validación completa de un golpe (smoke test)

Después del setup, este bloque valida los cuatro frentes deterministas:

```bash
# 1) Backend deterministas
cd "$REPO_ROOT"
pytest -q --tb=short
# Esperado: 1.795 passed, 10 skipped, 92 deselected, cobertura 74,8%, exit 0

# 2) Frontend unit
cd frontend
npm test -- --run
# Esperado: 31 archivos / 464 casos verdes

# 3) E2E mockeado (Playwright)
npm run test:e2e
# Esperado: 28 tests verdes (1 worker, WebGL headless)
cd ..
```

Y los que dependen de servicios externos (opcionales, a demanda):

```bash
# Integration PostGIS
docker compose -f docker-compose.test.yml up -d && \
  pytest -m integration -q && \
  docker compose -f docker-compose.test.yml down

# E2E real (stack completo arriba)
docker compose -f docker/docker-compose.yml up -d
cd frontend && npm run test:e2e:real
```

Si los deterministas pasan → la suite está sana y CI debería quedar verde.

---

## 10. Métricas de referencia

> **Medido el 2026-09-08.** Esta tabla es la referencia de cifras de tests del
> repo: si otro documento dice otra cosa, está desactualizado. Los conteos
> crecen con cada feature — vuelve a medirlos antes de citarlos en otro sitio.

| Métrica | Valor |
|---------|------:|
| Funciones `test_*` en `tests/` | **1.616** (en 120 archivos) |
| Backend default (excluye integration + llm) | **1.795 passed, 10 skipped, 92 deselected** |
| Gate cobertura backend | `--cov-fail-under=60` (**real 74 %** de sentencias) |
| Unit frontend (vitest) | **464** casos en 31 archivos |
| Gate cobertura frontend | lines 15 / branches 70 / functions 45 / statements 15 |
| E2E mockeado (Playwright) | **15 specs, 28 tests** |
| E2E real (Playwright integración) | **3 specs, 5 tests** (total E2E: 18 specs / 33 tests) |
| Resultados de benchmark versionados | `bench_results/*.json` (hybrid vs off); las corridas de carga `bench_results/carga*.json` son locales (la válida, en `docs/validacion/evidencia/fase-7/`) |

---

## 11. Comandos abreviados (para integradores)

```bash
# .bashrc / .zshrc / Profile.ps1
# Requiere REPO_ROOT exportado (ver «Convención de rutas» al inicio).
alias gc-be='cd "$REPO_ROOT" && pytest -q'
alias gc-fe='cd "$REPO_ROOT/frontend" && npm test -- --run'
alias gc-e2e='cd "$REPO_ROOT/frontend" && npm run test:e2e'
alias gc-e2e-real='docker compose -f "$REPO_ROOT/docker/docker-compose.yml" up -d && cd "$REPO_ROOT/frontend" && npm run test:e2e:real'
alias gc-integration='docker compose -f "$REPO_ROOT/docker-compose.test.yml" up -d && cd "$REPO_ROOT" && pytest -m integration && docker compose -f docker-compose.test.yml down'
alias gc-discovery='cd "$REPO_ROOT" && pytest tests/test_arcgis_mcp.py tests/test_hub_search.py tests/test_data_agent_via_mcp.py tests/test_route_discovery.py tests/test_mcp_hub.py -v'
```

---

**Enlaces relacionados:**
[Configuración y deploy](09-configuracion-y-deploy.md) ·
[Roadmap de tests](10-test-roadmap.md) ·
[Arquitectura](01-arquitectura.md) ·
[Índice del recorrido](README.md)
