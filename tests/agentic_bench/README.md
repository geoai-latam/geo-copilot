# Benchmark agéntico (A1)

Suite de **32 tareas en lenguaje natural** que corren end-to-end contra el
grafo REAL (LLM real + PostGIS real + sandbox), evalúan **resultado y
trayectoria** con checks deterministas, y emiten un score JSON versionable en
`bench_results/`. Es la red de regresión del comportamiento agéntico: se corre
tras cada cambio grande (política ReAct, structured outputs, prompts) para
detectar regresiones que los tests unitarios no ven.

## Categorías

| Categoría | Tareas | Qué mide |
|---|---|---|
| simple | 5 | consulta directa a BD (SQL correcto, LIMIT respetado) |
| aggregation | 4 | GROUP BY / AVG / MAX reales |
| spatial | 3 | operación geométrica sobre capa cargada (sandbox) |
| analytic | 5 | clustering/correlación/outliers/distribución (sandbox) |
| multistep | 4 | planes compuestos (BD→análisis, BD→simbología) |
| followup | 3 | memoria conversacional; feature seleccionada |
| symbology | 2 | juicio de simbología (incl. negación "sin agrupar") |
| external | 2 | discovery ArcGIS Hub (**flaky: red externa**) |
| ambiguous | 2 | honestidad ante ambigüedad (sin crash; A5 endurecerá a clarify) |
| unresolvable | 2 | rechazo honesto (tabla inexistente, DELETE prohibido) |

`score` excluye las `flaky`; `score_strict` las incluye.

## Cómo correr

**Dentro del contenedor app (recomendado — sandbox POSIX + BD interna):**

```bash
docker cp tests/agentic_bench geo_copilot_app:/app/agentic_bench
# OJO: /app es de solo-escritura restringida para el uid del contenedor —
# escribe los resultados en /tmp y cópialos de vuelta.
docker exec geo_copilot_app python /app/agentic_bench/runner.py --policy off \
    --out /tmp/bench_results
docker cp geo_copilot_app:/tmp/bench_results/. bench_results/
```

**En el host** (Windows: el sandbox no corre — spatial/analytic fallarán honesto):

```bash
python tests/agentic_bench/runner.py --category simple,aggregation,followup
```

Flags útiles: `--only id1,id2` · `--category a,b` · `--policy off|hybrid|always|current`
· `--timeout 300` · `--label fase4-pre`.

## Qué evalúa (evaluator.py)

Checks deterministas por tarea (`expected` en `tasks.yaml`): `success`,
`intent_any`, `trajectory_contains/absent` (agente actuó — engine-agnostic:
mapea herramientas ReAct → agentes), `sql_regex`/`no_sql`, `min/max_results`,
`result_kind` (geojson | table_or_chart | text_only), `has_symbology`,
`symbology_type_not`, `min_services`, `answer_regex/answer_not_regex`.

El HITL queda desactivado (grafo sin `hitl_manager`) — el bench es desatendido.
Las capas sintéticas (`synthetic.py`) son deterministas: mismos puntos, mismos
outliers, mismas categorías en cada corrida.

## Interpretación

- El JSON de cada corrida queda en `bench_results/<fecha>_<policy>.json` con
  score global, por categoría y por tarea (con razones de fallo).
- Comparar `off` vs `hybrid` (A2): correr dos veces cambiando `--policy` y
  comparar `score` y latencias por categoría.
- `bench_results/` no se versiona (contiene las respuestas del modelo de cada corrida): cada
  quien guarda sus corridas en local y compara contra las suyas.

## Verdades (`verdades.yaml`)

30 preguntas con la **cifra verdadera medida en el origen** (PostGIS, DuckDB, Nominatim), una por
fuente y por MCP. Cada una dice cómo se midió (`medida`). Lo que evalúa es la cifra que lee el usuario:
- `answer_numbers`: lo que la respuesta debe decir;
- `answer_numbers_not`: las respuestas falsas ya vistas (p. ej. 43.249, los lotes de todo Bogotá, dados como los de Chapinero).

Necesita el grafo de la APP con sus MCP (`--app`). El LLM no es determinista, así que se mide una
tasa con `--repeticiones`:

```bash
docker cp tests/agentic_bench geo_copilot_app:/tmp/agentic_bench
docker exec geo_copilot_app python /tmp/agentic_bench/runner.py --app \
    --tasks /tmp/agentic_bench/verdades.yaml --repeticiones 3 --out /tmp/bench_results
```

(Desde Git Bash en Windows: `MSYS_NO_PATHCONV=1`, o convierte `/tmp` en una ruta de Windows.)

Se corre **antes y después** de todo cambio de prompts u orquestador, y de cada refactor (plan de
calidad, F3/F4). Para MIRAR un caso con la traza completa: `scripts/banco_agente.py`.

## Costo

Una corrida completa hace ~80–150 llamadas LLM reales (2–6 por tarea) y tarda
10–25 min. Para iterar barato usa `--only` / `--category`.
