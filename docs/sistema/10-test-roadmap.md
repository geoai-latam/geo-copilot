# 10. Estructura de tests y roadmap de hardening

> **Qué encontrarás aquí:** el mapa de **cómo están organizados los tests HOY**
> (qué suites existen, qué corre solo, qué es manual) y, al final, el **roadmap
> de hardening** con lo que ya se cerró y lo que todavía es deuda. Escrito para
> que alguien no-técnico entienda la forma general y un integrador sepa qué
> comando lanzar.

> 📝 **Actualización 2026-07-26.** El roadmap original (2026-05-25) tenía **14
> items abiertos** en 5 fases. La mayoría **ya se implementó** (PostGIS real con
> `testcontainers`, cassettes VCR de Discovery, HITL end-to-end por REST+WS,
> handshake SEC-4, secuenciación de WebSocket, retry con corrector real,
> orquestador real, Playwright del mapa…). Este documento pasa de ser "un plan
> de 14 pendientes" a describir la **estructura viva** y dejar el roadmap como
> registro de estado + los pocos gaps que persisten. La guía operativa día-a-día
> para correr todo vive en [11-como-probar-todo](11-como-probar-todo.md).

---

## 10.1 Panorama en números

| Suite | Motor | Dónde vive | Tamaño aprox. | ¿La corre CI? |
|-------|-------|------------|--------------:|:-------------:|
| **Unit backend** | `pytest` | `tests/` (121 archivos `test_*.py`) | **1.795** casos deterministas pasando + 10 skipped (92 quedan deselected por los markers `integration`/`llm`; 1.728 funciones `test_*` en 121 archivos, que los parametrizados expanden) | ✅ sí |
| **Integración backend** | `pytest -m integration` | `tests/` (marcados) | decenas, requieren PostGIS real | ❌ manual |
| **Juicio LLM** | `pytest -m llm` | `tests/` (marcados) | críticos de juicio agéntico, cuestan tokens | ❌ manual |
| **Benchmark agéntico** | `pytest -m agentic_bench` | `tests/agentic_bench/` | 32 tareas, grafo+LLM+BD reales | ❌ manual |
| **Unit frontend** | `vitest` (jsdom) | `frontend/src/**/*.test.ts(x)` (31 archivos activos) | **464** casos | ✅ sí |
| **E2E mockeado** | Playwright | `frontend/e2e/` (15 specs) | **~28** casos, backend+WS mockeados | ❌ manual |
| **E2E integración real** | Playwright | `frontend/e2e-integration/` (3 specs) | **5** casos, stack Docker real | ❌ manual |

**Idea central:** el 99% de lo que corre en cada PR son **tests deterministas**
(no tocan red, ni BD real, ni LLM). Todo lo que necesita infraestructura o
credenciales está **marcado y excluido por defecto**, para que la suite sea
rápida, gratis y reproducible en cualquier máquina — pero sigue siendo
ejecutable a mano cuando hace falta validar de verdad.

---

## 10.2 La pirámide de tests

Cuanto más abajo, más numeroso, rápido y barato; cuanto más arriba, más
realista y caro. La base (unit) es enorme y se ejecuta en cada PR; la punta
(E2E real) es pequeña y se corre a mano contra el stack completo.

```mermaid
flowchart TB
    subgraph PIRAMIDE["Piramide de tests de GEO_COPILOT (base ancha = mas numeroso y rapido)"]
        direction TB
        L5["E2E integracion real (Playwright, stack Docker completo)<br/>frontend/e2e-integration — 3 specs / 5 casos — lento, sin mocks, manual"]
        L4["E2E mockeado (Playwright + page.route, headless)<br/>frontend/e2e — 15 specs / ~28 casos — determinista, oraculo __mapTestState"]
        L3["Juicio agentico con LLM real (marker llm) + agentic_bench<br/>grafo y agentes reales — manual, cuesta tokens"]
        L2["Integracion con PostGIS real (marker integration)<br/>fixture postgis_pool — se salta solo si no hay Docker"]
        L1["Unit deterministas — la base<br/>backend pytest 1.795 casos + frontend vitest 464 casos"]
    end
    L1 --> L2 --> L3 --> L4 --> L5
```

---

## 10.3 Backend: markers de pytest

El comportamiento por defecto está fijado en `pyproject.toml`
(`[tool.pytest.ini_options]`):

```toml
addopts = "-v --cov=geo_copilot --cov-report=term-missing --cov-fail-under=60 -m 'not integration and not llm'"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
```

Es decir: **`pytest` a secas corre solo los deterministas** y falla si la
cobertura baja de 60%. Tres markers declarados separan lo que necesita mundo
real (`markers` en `pyproject.toml`):

- **`integration`** — requiere PostGIS real. La fixture `postgis_pool`
  (`tests/conftest.py`) intenta conectar a `TEST_DATABASE_URL` (por defecto
  `postgresql://gc_test:gc_test_pw@localhost:5433/geocopilot_test`); si el pool
  no conecta **o** el schema `catastro` está vacío, hace `pytest.skip` — **nunca
  falla en una máquina sin Docker**. Correr con `pytest -m integration`.
- **`llm`** — llama a un LLM real. `get_real_llm_or_skip()` (`conftest.py`)
  cachea un `LLMClient.from_settings(...)`, hace un ping y hace `pytest.skip` si
  no responde (sin costo en CI sin credenciales). Correr con `pytest -m llm`.
- **`agentic_bench`** — benchmark end-to-end (grafo real + LLM real + BD real)
  en `tests/agentic_bench/` (`runner.py`, `evaluator.py`, `synthetic.py`,
  `tasks.yaml`). Resultados versionados en `bench_results/*.json` (p.ej.
  `2026-07-19T234918Z_hybrid_fase4-v2.json`, que valida `react_policy=hybrid`
  vs `off` — 93.33% en ambos modos, decisión de hacer `hybrid` el default).
  Excepción: las corridas de la prueba de carga (`scripts/prueba_carga.py`,
  `bench_results/carga*.json`) son locales y no se versionan (`.gitignore`); la
  que vale como evidencia se copia a `docs/validacion/evidencia/fase-7/`.

Subpaquetes dentro de `tests/`: `tests/security/` (path traversal, sanitización
SQL, `URLValidator` anti-SSRF), `tests/core/`, `tests/agentic_bench/`, y
`tests/manual_e2e/` (scripts de sprint versionables, no ejecutados por CI:
`sprint_a_data_agent.py` … `sprint_f_gis.py`). El resto son archivos planos
`test_*.py` en la raíz de `tests/`.

### Base de datos de test dedicada

`docker-compose.test.yml` levanta un PostGIS **aparte** del de desarrollo:

- imagen `postgis/postgis:15-3.3`, contenedor `geocopilot_postgis_test`;
- puerto host **5434** → 5432 (el PostGIS de dev usa 5433; con 5434 no chocan
  al levantar dev+test a la vez);
- seed `tests/fixtures/seed_catastro.sql` montado como init-script (schema
  exacto que esperan los tests de integración).

> ⚠️ **Ojo con el puerto.** La `TEST_DATABASE_URL` por defecto apunta a **5433**;
> el compose de test expone **5434**. Para correr integración contra este
> compose, exporta `TEST_DATABASE_URL=...localhost:5434/...` (o ajusta el
> puerto). Es el desajuste que hace `skip` en vez de conectar si no lo notas.

Flujo típico:

```bash
docker compose -f docker-compose.test.yml up -d
pip install -e ".[dev,integration]"      # integration trae testcontainers[postgres]
TEST_DATABASE_URL=postgresql://gc_test:gc_test_pw@localhost:5434/geocopilot_test \
  pytest -m integration
```

```mermaid
sequenceDiagram
    participant Dev as "Desarrollador"
    participant DC as "docker-compose.test.yml (postgis_test :5434)"
    participant PT as "pytest -m integration"
    participant FX as "fixture postgis_pool (conftest.py)"
    participant DB as "PostGIS + seed_catastro.sql"

    Dev->>DC: docker compose up -d (levanta PostGIS de test)
    DC->>DB: init con tests/fixtures/seed_catastro.sql
    Dev->>PT: pytest -m integration
    PT->>FX: solicita la fixture postgis_pool
    FX->>DB: intenta conectar (TEST_DATABASE_URL)
    alt "conecta y schema catastro poblado"
        DB-->>FX: pool listo
        FX-->>PT: fixture disponible
        PT->>DB: ejecuta SQL real de GISAgent (ST_Buffer, ST_DWithin...)
        DB-->>PT: filas reales -> asserts semanticos
    else "sin Docker o schema vacio"
        FX-->>PT: pytest.skip (nunca falla la suite)
    end
```

---

## 10.4 Frontend: vitest, Playwright mockeado y Playwright real

`frontend/package.json` define tres scripts de test:

| Script | Config | Directorio | Qué hace |
|--------|--------|-----------|----------|
| `npm test` | `vite.config.ts` | `frontend/src/**/*.test.ts(x)` | **Vitest** unit en jsdom (31 archivos activos `*.test.ts(x)`, **464** casos, `src/test/setup.ts`; uno más existe solo como `DataDiscoveryPanel.test.tsx.disabled`, ver 10.6 #11). |
| `npm run test:e2e` | `playwright.config.ts` | `frontend/e2e/` | **Playwright mockeado**: 15 specs / ~28 casos, backend y WS interceptados con `page.route` (`e2e/helpers.ts::mockInit/mockQuery`), 1 worker (WebGL SwiftShader headless no tolera paralelismo), oráculo `window.__mapTestState` / `window.__mlmap`. |
| `npm run test:e2e:real` | `playwright.integration.config.ts` | `frontend/e2e-integration/` | **Playwright real**: 3 specs / 5 casos (`real-flow`, `real-frt04`, `real-imagery`), **sin mocks**, timeouts largos (120s/30s); exige el stack Docker sirviendo `:3000`. |

**Cobertura frontend — deuda declarada.** Los umbrales en `vite.config.ts` están
deliberadamente bajos (`lines: 15, branches: 70, functions: 45, statements: 15`),
comentados como pendiente de subir. La lógica pura (helpers, expresiones de
simbología, `buildMapContext`, `pickRestyleTarget`) sí está bien cubierta; lo
que falta es render de componentes JSX.

Los 15 specs mockeados cubren TODAS las features visibles del frontend:
`agentic-query`, `analysis`, `client-zoom`, `discovery`, `errors-clarify`,
`feature-popup`, `hitl`, `imagery`, `fase-3-mcp`, `layers`, `map`,
`misc-ui`, `session-ui`, `symbology`, `tabs-table`.

---

## 10.5 Mapa de suites y qué ejecuta CI

Este es el punto que más confunde: **CI ejecuta solo la base de la pirámide.**
`.github/workflows/ci.yml` tiene dos jobs y **ninguno** corre integración, LLM,
benchmark ni Playwright — esas suites son de ejecución manual/local.

- **Job `backend`**: `ruff check src/ tests/` + `pytest -q --tb=short` (hereda el
  `addopts` del `pyproject.toml`, así que **excluye `integration` y `llm`**).
- **Job `frontend`**: `npm test -- --run` (vitest) + `npm run build`.

```mermaid
flowchart TB
    subgraph BE["Backend (raiz tests/, pytest)"]
        BEdef["Default: -m 'not integration and not llm'<br/>1.795 casos deterministas (74,8% cobertura) + cov-fail-under=60"]
        BEint["marker integration -> fixture postgis_pool<br/>docker-compose.test.yml (postgis:15-3.3, puerto 5434)"]
        BEllm["marker llm -> get_real_llm_or_skip()<br/>(ping + skip si no hay credenciales)"]
        BEbench["marker agentic_bench -> tests/agentic_bench/<br/>runner + evaluator + tasks.yaml; salida en bench_results/*.json"]
    end
    subgraph FE["Frontend (frontend/, npm)"]
        FEunit["vitest (jsdom): 31 archivos activos / 464 casos<br/>umbrales bajos: lines 15 / functions 45 (deuda)"]
        FEmock["Playwright mockeado: playwright.config.ts<br/>frontend/e2e (page.route, 1 worker, __mapTestState)"]
        FEreal["Playwright real: playwright.integration.config.ts<br/>frontend/e2e-integration (stack Docker en :3000)"]
    end
    subgraph CI["CI (.github/workflows/ci.yml)"]
        CIbe["job backend: ruff check + pytest -q"]
        CIfe["job frontend: vitest --run + npm run build"]
    end
    BEdef --> CIbe
    FEunit --> CIfe
    CIbe -. "NO ejecuta" .-> BEint
    CIbe -. "NO ejecuta" .-> BEllm
    CIbe -. "NO ejecuta" .-> BEbench
    CIfe -. "NO ejecuta" .-> FEmock
    CIfe -. "NO ejecuta" .-> FEreal
```

**Comandos abreviados:**

```bash
# Todo lo determinista (lo mismo que CI)
pytest                              # backend unit (excluye integration/llm)
cd frontend && npm test -- --run    # frontend unit (vitest)

# Suites manuales (requieren infra o credenciales)
pytest -m integration               # PostGIS real (levanta docker-compose.test.yml antes)
pytest -m llm                       # juicio agéntico con LLM real
pytest -m agentic_bench             # benchmark end-to-end
cd frontend && npm run test:e2e     # Playwright mockeado
cd frontend && npm run test:e2e:real  # Playwright contra stack Docker en :3000

# Regrabar cassettes de Discovery (una vez, con internet)
pytest tests/test_discovery_with_cassettes.py --record-mode=new_episodes -v
```

---

## 10.6 Estado del roadmap original (los 14 items)

El plan de 2026-05-25 planteaba 14 items en 5 fases. Estado a 2026-07-26 (la
evidencia es el archivo de test que hoy existe en el repo):

| # | Item original | Fase | Estado | Evidencia en el repo |
|---|---------------|------|:------:|----------------------|
| 1 | Orquestación real (no test trivial de `hasattr`) | F1 | ✅ | `test_orchestrator.py`, `test_orchestrator_integration.py`, `test_orchestrator_nodes.py` |
| 2 | `test_profile_dataset` con contenido real | F1 | ✅ | `test_data_agent.py` (asserts sobre campos/geometría) |
| 3 | WS secuenciado con TestClient (no single-message) | F1 | ✅ | `test_websocket_sequencing.py` |
| 4 | Frontend WS: reconnect (✅) + buffering (❌ no implementado) | F1 | ⚠️ parcial | `frontend/src/services/websocket.test.ts`: `describe('Reconnection')` cubre el reconnect con tests reales, **pero** el propio archivo documenta que **no hay buffering** (`test('drops messages while disconnected (current behavior, no buffer)')` con comentario "GAP DEL PRODUCTO … NO hay buffering ni retry"): al estar desconectado los mensajes se **dropean** en silencio. Ver 10.7 #6 |
| 5 | `--cov-fail-under` en pyproject + CI | F1 | ✅ | `pyproject.toml` (`--cov-fail-under=60`) |
| 6 | Regex SQL que rechaza falsos positivos (comentarios) | F1 | ✅ | `test_gis_agent.py` (strip de comentarios) |
| 7 | Cassettes VCR de Hub para Discovery | F2 | ✅ | `pytest-recording`+`vcrpy` en deps `dev`; `test_discovery_with_cassettes.py`; `tests/fixtures/cassettes/` |
| 8 | Retry E2E con `SQLCorrector` real | F2 | ✅ | `test_retry_executor_e2e.py`, `test_retry_executor.py` |
| 9 | HITL E2E completo por REST + WebSocket | F2 | ✅ | `test_hitl_e2e.py`, `test_hitl_full_flow.py` (+ `_race`, `_timeout`, `_interrupt_mode`, `_cutover`) |
| 10 | GISAgent contra PostGIS real (testcontainer) | F3 | ✅ | `testcontainers[postgres]` (extra `integration`); `test_gis_integration.py`; `docker-compose.test.yml` |
| 11 | `DataDiscoveryPanel` render + interacción | F4 | ⚠️ parcial | `@testing-library/react` en devDeps; `DataDiscoveryPanel.test.tsx` **existe pero `.disabled`** |
| 12 | `MapLibreMap` con Playwright | F4 | ✅ | suite `frontend/e2e/` completa (incl. `map.spec.ts`, `layers.spec.ts`) + `e2e-integration/` |
| 13 | Bug del sandbox de Python en Windows | F5 | ✅ vía Docker | `sandbox_backend` (`config.py`): `docker` endurecido es el camino soportado (`network:none`, `read_only`, `cap_drop:ALL`) |
| 14 | Handshake WS real (Origin, token, SEC-4) | F2 | ✅ | `test_websocket_handshake_sec4.py`, `test_websocket_approval_ownership.py` |

Es decir: **12 de 14 cerrados**, 2 parciales (el reconnect del WS del frontend
está cerrado pero el **buffering nunca se implementó** — los mensajes se dropean
al estar desconectado; y el test de render del panel de Discovery está escrito
pero desactivado). El grueso del roadmap ya rindió.

---

## 10.7 Gaps que persisten (deuda actual)

Lo que sigue abierto — no como "plan de 14 fases" sino como deuda concreta:

1. **Cobertura frontend baja y sin subir.** Umbrales en 15% de líneas. El código
   de render de componentes (más allá de helpers) está poco cubierto. Meta:
   subir progresivamente y **reactivar `DataDiscoveryPanel.test.tsx`** (hoy
   `.disabled`).
2. **CI no ejecuta las suites realistas.** Ni Playwright (mock ni real), ni
   `-m integration`, ni `-m llm`, ni `agentic_bench` corren en el pipeline. Son
   la red de seguridad *manual*. Gap: un job opcional/nocturno que levante
   `docker-compose.test.yml` y corra `-m integration`, y otro que corra
   Playwright mockeado (que **no** necesita credenciales) daría cobertura
   automática sin costo de tokens.
3. **Desajuste de puerto de la BD de test** (5433 default vs 5434 del compose):
   fricción real que hace `skip` silencioso si no se exporta `TEST_DATABASE_URL`.
   Convendría alinear el default o documentarlo en el propio compose.
4. **Sandbox nativo en Windows.** Resuelto *de facto* exigiendo
   `SANDBOX_BACKEND=docker`; el modo `subprocess` nativo sigue siendo el camino
   de dev de menor aislamiento. No hay tests que fuercen el subprocess en
   Windows (se asume Docker).
5. **Tracking asíncrono de queries.** `GET /query/{id}` y `POST /{id}/cancel`
   devuelven **501 explícito** (honestos: no implementados). No hay suite que
   cubra ese flujo porque el flujo no existe todavía.
6. **WebSocket del frontend sin buffering.** El cliente WS reconecta (con tests
   reales en `websocket.test.ts`), pero **no bufferea ni reintenta** los mensajes
   enviados mientras está desconectado: los **dropea en silencio** (documentado
   en el propio test `drops messages while disconnected (current behavior, no
   buffer)`). Gap: encolar y reenviar tras reconectar, o al menos avisar al
   usuario del mensaje perdido.

---

## 10.8 Definition of Done global

El hardening de tests se considera "completo" cuando:

- ✅ Cobertura BE ≥ 75% enforced en CI (hoy gate en 60%, **real 74 %**); FE con
  umbral **real** subido desde 15%.
- ✅ Cero tests "tautológicos" (mock retorna X → assert X) — logrado en los
  agentes tras Sprints A–F.
- ✅ Cada agente con al menos 1 test contra datos/respuestas reales (cassette o
  testcontainer) — logrado (Discovery cassettes, GIS integration).
- ✅ HITL E2E (REST + WS) verificado — logrado.
- ✅ Handshake WS (Origin/token/SEC-4) verificado — logrado.
- ⬜ Al menos una suite realista (Playwright mock o `-m integration`) corriendo
   **automáticamente** en CI o en un job nocturno.
- ⬜ `DataDiscoveryPanel.test.tsx` reactivado y verde.

---

## 10.9 Principio que guía la estructura

Alineado con la memoria del proyecto (*"agentic, no fallbacks"* y *"el LLM juzga,
el código verifica hechos"*): los tests deterministas verifican **hechos y
contratos** (shapes, guardas de seguridad, orden de nodos, expresiones de
simbología, SSRF), y los tests marcados `llm`/`agentic_bench` validan el
**juicio agéntico real** contra un LLM de verdad — y ese juicio se revisa
críticamente, no se da por bueno solo porque el test pasa. La base rápida
protege el contrato; la punta lenta protege el comportamiento.

---

## Navegación

- Volver al [README](README.md) para el índice completo.
- Guía práctica paso a paso: [11-como-probar-todo](11-como-probar-todo.md).
- Configuración/entornos de los servicios: [09-configuracion-y-deploy](09-configuracion-y-deploy.md).
- Si vas a añadir un agente y sus tests: [02-agentes](02-agentes.md).
