# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Numeración: aún sin tag estable; las secciones son por rama de
consolidación.

## [Unreleased] — Fase 6 (refactor profundo)

Continuación del trabajo de consolidación, atacando la deuda arrastrada
de las Fases 2 y 3 una por una. Cada ítem se ejecutó, validó y
commiteó por separado (8 commits, ver `git log`).

### Added

- **`RetryExecutor`** en `orchestrator/retry.py` — abstracción única
  para retry + auto-corrección con callbacks (`attempt`, `correct`,
  `on_retry`). 10 tests dedicados. Reemplaza la copia inline de
  `PlanExecutor._execute_step`; las dos copias restantes en
  `_gis_agent_node` / `_python_agent_node` se migran como parte de la
  segunda pasada de #4/#6 (van junto con la extracción de esos nodos).
- **9 tests de integración del orquestador** en
  `tests/test_orchestrator_integration.py` — ejercitan
  `GeoAgentGraph.process()` end-to-end con LLM mockeado a través del
  wiring real, sin más mocks que el LLM y el pool de BD. Cubren:
  construcción del grafo, integridad de `GraphState`, flujo de
  `direct_response`, fallos del LLM, ruteo a planner en queries
  complejas, y el contrato de la firma pública de retorno.
- **Paquete `orchestrator/nodes/`** con 6 nodos extraídos de
  `graph.py`: `router`, `planner`, `plan_executor`, `symbology`,
  `insights`, `responder`. Cada nodo es un módulo con
  `async def run(graph, state) -> dict`; el método `_*_node` en la
  clase es un thin delegator de 2 líneas.
- **`agents/router_agent/prompts.py`** — `build_router_system_prompt`
  con el template como constante. Patrón ya establecido por
  `PythonAgent`.
- **`agents/insights_agent/html_report.py`** — `HTMLReportMixin` con
  `_render_full_report` y `_markdown_to_html`. `InsightsAgent` hereda
  del mixin.
- **Cancelación real de tasks en `ConnectionManager`** —
  `register_task` / `clear_task` / `cancel_task` por `session_id`.
  El handler de WebSocket envuelve `agent_graph.process()` en una
  `asyncio.create_task()` registrada; `handle_cancel` ejecuta
  `Task.cancel()` de verdad (antes solo enviaba notificación).

### Changed

- `GeoAgentGraph._*_node` ahora son thin delegators de 2 líneas que
  importan y llaman `nodes.<node>.run(self, state)`.
- `_render_full_report` y `_markdown_to_html` viven en `HTMLReportMixin`
  via herencia múltiple — sin tocar las ~30 referencias a `self.*` que
  hacían dentro del método.
- `RouterAgent._build_system_prompt` es ahora un wrapper de 6 líneas
  alrededor de `build_router_system_prompt`.
- `/query` REST: `requires_approval` y `pending_approval_id` se
  propagan desde el grafo. Cuando ambos están presentes, el `status`
  de la respuesta pasa a `WAITING_APPROVAL` (antes se forzaba a
  `COMPLETED`/`FAILED`).
- `CesiumMap.tsx`: el efecto de init usa una bandera `cancelled` para
  evitar el leak de viewer bajo StrictMode (FE-4). Tras cada `await`
  comprueba la bandera y, si fue cancelado, destruye lo creado y sale
  sin tocar `viewerRef`.
- `PlanExecutor._execute_step` reescrito sobre `RetryExecutor`.
  Comportamiento observable idéntico; ~75 → ~60 LOC.

### Removed

- `src/geo_copilot/core/container.py` (260 LOC) — `ServiceContainer`
  ceremonial sin consumidores reales.
- `tests/core/test_container.py` (16 tests) — cobertura falsa del
  contenedor.
- `~220 LOC del bloque HTML inline` en `insights_agent/agent.py`:
  `_render_full_report` (~200) + `_markdown_to_html` (~20). Movidos
  al mixin.
- `_planner_node`, `_plan_executor_node`, `_router_node`,
  `_symbology_agent_node`, `_insights_agent_node`, `_responder_node`
  como cuerpos inline en `graph.py` — quedan como delegators. Total:
  `graph.py` 1973 → 1524 LOC (-23%).

### Fixed

- `handle_cancel` leía `app_state.current_execution_state`, atributo
  que no existía. La task seguía ejecutándose por su cuenta a pesar
  del "cancel" del cliente. Ahora la cancelación llega al
  `Task.cancel()` real.
- Leak del viewer Cesium en `CesiumMap.tsx`: bajo StrictMode el
  `cleanup` síncrono pasaba antes de que el `initViewer` async
  resolviera, dejando un viewer huérfano sin posibilidad de destruirlo.

### Cierre de Fase 6 — segunda vuelta (los 3 pendientes)

- **#4 parte 2 / #6 parte 2** — `_data_agent_node`, `_gis_agent_node`
  y `_python_agent_node` extraídos a `nodes/`. `gis` y `python` usan
  ahora `RetryExecutor` (el inline desaparece). `graph.py` pasó de
  1524 → 644 LOC (-58%); del baseline original 1973 → 644 LOC (-67%).
  Nuevos módulos: `nodes/data_agent.py` (286 LOC), `nodes/gis_agent.py`
  (314 LOC), `nodes/python_agent.py` (261 LOC). Helpers
  `extract_external_url`, `format_search_results` e
  `identify_sql_risks` también migrados. HITL REJECTED/EXPIRED se
  modela con `AttemptOutcome.no_retry=True` para que el `RetryExecutor`
  los respete sin reintentar.

- **#9 (ORC-5) — doble motor resuelto.** `PlanExecutor` ya no llama a
  `self.graph._<x>_node` (métodos privados). Las 17 invocaciones
  ahora pasan por `nodes.<x>.run(graph, state)` — la misma capa
  pública que el grafo compilado. El "doble motor" se reduce a "dos
  consumidores de la misma capa". Helper `_node(module_name)` con
  import perezoso para evitar el ciclo orchestrator → nodes → graph.

- **#11 (ORC-11) — sesiones a Redis.** Abstracción `SessionStore` en
  `orchestrator/sessions/`:
  - `base.py` — contrato abstracto (get/save/delete/exists/list/count/
    cleanup_expired).
  - `memory.py` — `InMemorySessionStore` con la lógica histórica.
  - `redis_store.py` — `RedisSessionStore` con TTL nativo (`EX`),
    SCAN para listar (nunca KEYS), tolerante a datos corruptos
    (limpia + degrada graciosamente), import perezoso del cliente.
  - `ConversationManager` refactorizado para delegar al store.
    API pública intacta.
  - `ConversationContext.from_dict()` nuevo para deserializar.
  - Config: `session_backend`, `session_max_count`,
    `session_timeout_minutes`.
  - Factory `_build_session_store` en `dependencies.py` con
    fallback automático a in-memory si Redis no está disponible o
    el ping falla.
  - `redis>=5.0` añadido a `pyproject.toml`.
  - 17 tests nuevos: InMemoryStore, ConversationManager delegation,
    roundtrip serialización JSON, RedisStore mocked (save/get,
    corrupt payload sweep, SCAN listing), fallback con host
    inexistente.

Validación final tras los 11 ítems: pytest 535 passed, 2 skipped,
0 failed; ruff 0 errores.

---

## [Pre-Fase-6] — consolidación de la base (Fases 0–5)

Trabajo previo al primer release, ejecutado tras el diagnóstico
documentado en `docs/DIAGNOSTICO.md`. Detalle por fase en
`docs/ROADMAP.md`.

### Added

- **Sandbox Python por subproceso aislado** (`agents/gis_agent/sandbox_runner.py`).
  El ejecutor del PythonAgent ya no usa `exec()` en proceso; cada
  invocación corre como subprocess con rlimits (CPU, memoria, fd, fork,
  core) y timeout duro vía `asyncio.wait_for`. Defensa en profundidad
  con AST allowlist en el padre.
- **Autenticación por API key** (`api/auth.py`). Header `X-API-Key`
  para REST y `?token=…` para WebSocket; constant-time compare. Si
  `settings.api_key` es `None`, modo dev (auth desactivada).
- **Validación de Origen + token en WebSocket** antes de aceptar la
  conexión.
- **Scope de aprobaciones HITL por sesión**: `ApprovalRequest` requiere
  `session_id`; `POST /approval/{id}` devuelve 403 si la sesión no es
  dueña; `GET /approval/pending` exige `?session_id=` y filtra.
- **`URLValidator` aplicado en `ArcGISConnector` y `SocrataConnector`**
  (defensa SSRF al nivel de los conectores, no solo de FileConnector).
- **Parametrización de `SQLTemplates`**: helpers `_safe_ident`,
  `_quote_literal`, `_build_where`.
- **Read-only + `statement_timeout`** en `GISAgent._execute_sql`.
- **Escape HTML/JS en reportes de InsightsAgent y TableFormatter** —
  los popups de Leaflet ahora se construyen con `textContent`.
- **`pydantic.SecretStr` para credenciales** (LLM keys, `database_url`).
- **Timeouts** en clientes LLM (`timeout=`, `max_retries=`) y en
  `graph.process()` (via `asyncio.wait_for(total_execution_timeout)`).
- **`asyncio.Lock` en `AppState.initialize`** — elimina la carrera que
  podía crear pools/clientes duplicados.
- **Llamada a `setup_logging` al arranque** + middleware de
  `X-Request-ID` para trazabilidad.
- **`/health` con 503 cuando la BD o el LLM están caídos**.
- **Drain limpio en `AppState.shutdown`**: WebSockets → cliente LLM →
  pool de BD.
- **`ErrorBoundary` global en frontend** (`frontend/src/components/ErrorBoundary.tsx`).
- **CI**: `.github/workflows/ci.yml` con jobs de backend (ruff + pytest)
  y frontend (vitest + tsc + build).
- **Pre-commit**: `.pre-commit-config.yaml` (ruff, ruff-format,
  whitespace, yaml).
- **`docker/Dockerfile.sandbox`** mínimo para que `docker compose
  build` funcione (lo referencia el servicio `sandbox`).
- **`frontend/src/lib/cesiumViewer.ts`** que provee `setViewer` y
  `mapActions` (lo importan `CesiumMap` y `MapControls`).

### Changed

- `PythonAgent` ahora pasa el GeoJSON al sandbox vía `input_data` en
  vez de interpolarlo en el código fuente.
- `GISAgent.execute_approved_code` reescrito (la versión anterior
  llamaba a un método inexistente y olvidaba `await`).
- `DataAgent` usa un `ContextVar` task-local para `session_id` en vez
  de `self._current_session_id` (era estado mutable compartido).
- `HITLManager` usa `settings.hitl_timeout` por defecto, datetimes
  tz-aware (UTC), y limpia `_responses` en el `finally` (fuga).
- `ConnectionManager.disconnect` cierra el WebSocket explícitamente
  con código 1000.
- Pool de BD lee `db_pool_min_size`/`max_size`/`db_query_timeout` del
  config en vez de valores hardcodeados.
- Tests `ApprovalRequest` pasan `session_id`; el test de Socrata
  ahora se enruta correctamente (bug real corregido en
  `connectors/base.py`).
- Modelos Pydantic de WebSocket migrados de `class Config` a
  `ConfigDict` (Pydantic v3 ready).
- Config de Ruff movida a `[tool.ruff.lint.*]` (antes a nivel raíz, se
  ignoraba). `slowapi` añadido a `pyproject.toml`.

### Removed

- **~1.900 LOC de código muerto** (cero usos verificados):
  - `src/geo_copilot/core/execution/` (RetryExecutor, ErrorCorrector,
    ExecutionStrategy).
  - `src/geo_copilot/orchestrator/execution_controller.py`.
  - `src/geo_copilot/orchestrator/nodes/` (BaseNode, NodeContext).
  - `src/geo_copilot/orchestrator/helpers.py`.
  - `tests/core/execution/` (~1.250 LOC de cobertura falsa).
- **102 `print()` decorativos** (graph.py, planner.py, query.py,
  router_agent, python_agent) — la info paralela ya iba al logger.
- Campos de `GraphState` sin uso: `next_agent`, `search_keywords`,
  `partial_results`; lecturas muertas `_corrected_sql`/`_corrected_code`.
- `PythonSandbox.generate_analysis_template` (helper sin consumidores).
- Endpoints obsoletos `/metadata/workflows` y `/metadata/intents` (sus
  tests también).
- 5 documentos de plan/diagnóstico obsoletos movidos a `docs/archive/`.

### Fixed

- `connectors/base.py:detect_source_type` enruta correctamente las URLs
  de Socrata (antes el sufijo `.json` ganaba sobre el patrón Socrata).
- `escape_sql_comment` ahora es idempotente: el replace ingenuo dejaba
  `--` residual en `----` (e.g. `- -- -`).
- `PlanExecutor._merge_step_output` ya no fusiona output de pasos
  fallidos en el estado propagado.
- Tests de SQL-sanitization apuntan al SQL escapado, no al texto del
  prompt (el prompt menciona `--` y `DROP TABLE` como ejemplos).
- 6 `raise … from exc` añadidos en handlers HTTP / connectors para
  preservar la cadena de errores.
- `tsconfig` con `"ignoreDeprecations": "5.0"` para que TS 5.x no falle
  por el deprecation warning del `baseUrl`.
- `.gitignore` con `/lib/` anclado a la raíz para no excluir
  `frontend/src/lib/`.
- App.tsx: `retryInfo` fuera del array de deps del efecto WebSocket
  (antes el socket se destruía y reconectaba en cada mensaje de retry).
  `setTimeout` de auto-hide cancelable en el cleanup.

### Security

- Mitigado el riesgo de **RCE** vía sandbox (subprocess + rlimits).
- Cerrada la **autenticación abierta** (toda la API + WS exigen API key).
- Cerrado el **bypass HITL** (scope por sesión + auth).
- Cerrado **SSRF** en conectores externos (URLValidator).
- Cerrada **SQL injection** en plantillas (parametrización +
  read-only + statement_timeout).
- Cerrado **XSS almacenado** en reportes HTML / popups Leaflet.
- Credenciales en `SecretStr` (no se filtran en logs / `repr`).

### Documentation

- `docs/DIAGNOSTICO.md` (nuevo) — diagnóstico técnico consolidado.
- `docs/ROADMAP.md` (nuevo) — plan por fases con checklists.
- `docs/archive/` — planes obsoletos archivados.
- README badges y métricas actualizados (515 tests / 56% / 6 agentes).
- Este `CHANGELOG.md` (nuevo).

### Tooling

- CI en GitHub Actions (ruff + pytest backend, vitest + tsc + build
  frontend).
- Pre-commit hooks (ruff, formato, basics).
- `pyproject.toml` consolidado como única fuente de dependencias.
