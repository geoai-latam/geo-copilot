# Contribuir a GEO_COPILOT

GEO_COPILOT es un copiloto geoespacial multiagente: le hablas en español, consulta PostGIS,
descubre datos abiertos, procesa imágenes de Sentinel-2 y te dibuja el resultado en un mapa. Lo
mantiene GeoAI LATAM.

Este documento tiene todo lo que necesitas para tu primer *pull request* sin preguntarle nada a
nadie. Si algo de acá no funciona tal como está escrito, eso ya es un aporte: ábrele un issue.

---

## Antes de escribir código

- **`docs/sistema/` es la única fuente de verdad documental.** Si un comentario del código cita
  un documento que no está en el repositorio (auditorías, validaciones o planes internos), lo que
  vale es `docs/sistema/` y el propio código.
- **Antes de "arreglar" algo, mira si ya está diagnosticado** en los issues abiertos.
- **Si lo tuyo es un fallo de seguridad, no abras un issue.** Lee
  [`SECURITY.md`](SECURITY.md): hay un canal privado. El sistema ejecuta SQL y Python escritos por
  un modelo, así que ese canal importa de verdad.

---

## Levantar el entorno

### Con Docker (lo recomendado)

```bash
cp .env.example .env
```

Edita `.env` y define, como mínimo:

- `LLM_PROVIDER` (`openai` | `anthropic` | `azure`), la clave del proveedor y `LLM_MODEL`.
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD` y `GEO_APP_PASSWORD`. Vienen **comentadas** a propósito:
  el compose las declara con `${VAR:?}` y sin ellas el arranque aborta con un error explícito. Es
  intencional, para que nadie termine corriendo con una contraseña publicada.
- `ALLOWED_DOMAINS`, por ejemplo `["datos.gov.co","geoportal.igac.gov.co"]`.

Y levanta el stack:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up --build
```

> **`--env-file .env` no es opcional.** Con `-f docker/docker-compose.yml`, el *project directory*
> de Compose pasa a ser `docker/`, donde no hay `.env`. Sin el flag, las interpolaciones `${...}`
> no leen tu `.env` de la raíz. Aplica a **todos** los comandos `docker compose` de este archivo.

| Servicio | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API (Swagger en `/docs`) | http://localhost:8000 |
| Health | http://localhost:8000/health |
| PostGIS | `localhost:5433` |

Todos los puertos quedan atados a `127.0.0.1`. Es deliberado (remediación R0.3); no los publiques
a `0.0.0.0` para "probar desde el celular".

Comandos que vas a usar a diario:

```bash
docker compose --env-file .env -f docker/docker-compose.yml logs -f app
docker compose --env-file .env -f docker/docker-compose.yml down
docker compose --env-file .env -f docker/docker-compose.yml up --build frontend
```

### Manual, con recarga en caliente

Para el backend recomendamos Conda: maneja mejor los binarios nativos de GDAL, GEOS y PROJ,
sobre todo en Windows.

```bash
conda create -n geocopilot python=3.11 -y
conda activate geocopilot
conda install -c conda-forge geopandas shapely -y
pip install -e ".[dev]"

# La base y el cache, en Docker
docker compose --env-file .env -f docker/docker-compose.yml up postgis redis

# Terminal 1 — backend en :8000
python -m geo_copilot.api.app

# Terminal 2 — frontend en :5173
cd frontend && npm install && npm run dev
```

> Si corres el backend fuera de Docker, apunta `DATABASE_URL` a **`geo_app`**, no a `geo_user`:
> `postgresql://geo_app:$GEO_APP_PASSWORD@localhost:5433/geo_copilot`. `geo_user` es superusuario
> en la imagen `postgis/postgis`, y una transacción `READ ONLY` no contiene a un superusuario. Es
> la remediación R0.6, y desandarla deja el SQL del modelo corriendo con todos los permisos.

---

## Correr los tests

### Backend

```bash
python -m pytest -q                          # la suite determinista (~1.500 tests)
python -m pytest -q --no-cov                 # más rápido, sin el gate de cobertura
python -m pytest tests/ruta/test_x.py::test_y -v
```

Los `addopts` de `pyproject.toml` excluyen por defecto dos marcadores y aplican un gate de
cobertura del 60 %.

**Los tests que tocan red o servicios reales van marcados `@pytest.mark.integration`, y el CI los
excluye.** Corren aparte, contra una base de test en el puerto 5434:

```bash
docker compose -f docker-compose.test.yml up -d
pip install -e ".[dev,integration]"
python -m pytest -m integration
docker compose -f docker-compose.test.yml down -v
```

`python -m pytest -m llm` corre los tests que llaman a un LLM de verdad. Cuestan tokens reales y
hacen `skip` si no hay credenciales.

### Frontend

```bash
cd frontend
npm test -- --run     # vitest una vez; `npm test` a secas se queda en modo watch
npm run lint
npm run build         # tsc + vite build
npm run test:e2e      # Playwright mockeado, headless
```

`npm run test:e2e:real` levanta los E2E contra el stack Docker completo. No corre en CI.

---

## Linter y formato

`ruff` es el linter del backend, con `line-length = 100` y `target-version = py311`:

```bash
ruff check src/ tests/ services/     # exactamente lo que corre el CI
ruff format src/ tests/ services/
```

Y engancha los hooks una sola vez después de clonar, para no descubrir en CI lo que se veía en
local:

```bash
pre-commit install
```

---

## Lo que el CI exige para dejar pasar el PR

`.github/workflows/ci.yml` corre cuatro cosas. Si una falla, el PR no entra.

| Job | Comando |
|---|---|
| Backend lint | `ruff check src/ tests/ services/` |
| Backend tests | `pytest -q --tb=short` |
| Frontend tests | `npm test -- --run` |
| Frontend build | `npm run build` |

Playwright, `-m integration`, `-m llm` y el benchmark agentic son de ejecución manual. Si tu
cambio los toca, córrelos en local y dilo en el PR.

---

## Convenciones

**Ramas** desde `main`: `feature/lo-que-hace`, `fix/lo-que-arregla`.

**Commits**, con estos prefijos: `feat:` · `fix:` · `docs:` · `refactor:` · `test:`. El historial
usa además `sec:` para trabajo de seguridad, y ámbito entre paréntesis cuando ayuda:

```
feat(orchestrator): selección de capa objetivo por NOMBRE
fix(hitl): los tres gates fallan CERRADOS — un EXPIRED ya no ejecuta
```

**Idioma:** español. El código, los comentarios, los commits y la documentación. Los nombres de
símbolos siguen en inglés, como el resto del repositorio.

---

## Las cinco reglas que muerden si no las conoces

1. **Toda tool nueva del `imagery-mcp` va también en `TOOL_SCOPES`**
   (`services/imagery_mcp/imagery_mcp/auth.py`). Si no la registras, queda denegada con 403. El
   servicio es *fail-closed* a propósito, así que el síntoma no te va a decir qué falta.
2. **Nada de heurísticas.** Se borraron ~1.350 líneas de plantillas SQL, listas de palabras clave,
   umbrales mágicos y mapeos fijos. Toda decisión semántica la toma el LLM viendo el contexto real
   —schema, consulta, muestras— y después se valida contra el esquema. Si vas a añadir un `if`
   sobre el texto del usuario, esa es la señal de que el camino es otro.
3. **Fallar honesto.** Si el modelo falla, el agente falla y lo dice. No hay fallbacks que
   adivinen: nada de asumir 4326 porque el shapefile no traía `.prj`, nada de operar sobre la
   última capa cargada porque no se entendió cuál pidió el usuario.
4. **Los controles fallan cerrados, y son allowlists.** Si tocas un gate de aprobación, de scopes
   o de dominios, escríbelo de modo que un estado nuevo que aparezca mañana también bloquee. Esa
   forma no es cosmética: dos hallazgos de la auditoría de julio fueron controles que fallaban
   abiertos.
5. **Si cambias comportamiento, actualiza `docs/sistema/` en el mismo PR.** La deriva documental
   tiene sección propia en la auditoría del 8 de septiembre. Una documentación que promete lo que
   el código ya no hace es peor que no tenerla.

---

## Datos de prueba

`data/` está en `.gitignore` y así se queda. El repositorio **no distribuye datasets**: los tests
de integración se apoyan en `tests/fixtures/seed_catastro.sql`, 33 filas sintéticas con
coordenadas de Bogotá y Mosquera, con el mismo esquema que el dump real.

Si tu aporte necesita datos, aporta un fixture sintético. Nada de dumps de catastro, ortofotos ni
GeoTIFF en un commit: lo que entra al historial de git no sale.

---

## Qué se agradece especialmente

- El cutover de `SQL_AST_VALIDATION` a `enforce` con allowlist de tablas.
- Tests de integración contra PostGIS real: es la parte más flaca de la suite.
- Conectores a fuentes abiertas de LATAM que hoy no estén: catastros municipales, IDE nacionales,
  portales de datos abiertos.
- Correcciones de documentación. Si algo de este archivo te falló, arréglalo acá mismo.

---

## Abrir el PR

1. Rama desde `main`, cambio pequeño y con un solo propósito.
2. `ruff check src/ tests/ services/` y `python -m pytest -q` en verde.
3. `npm test -- --run` y `npm run build` en verde si tocaste `frontend/`.
4. Llena la plantilla del PR: las casillas de tests y de documentación están para contestarlas,
   no para marcarlas.
5. Describe **cómo lo probaste**. Un "funciona en mi máquina" con el comando exacto vale más que
   un párrafo de intenciones.

## ¿Dudas?

Abre un issue con la plantilla de pregunta, o escribe a `sebastian.forero.77@gmail.com`.
Para vulnerabilidades, el canal es el de [`SECURITY.md`](SECURITY.md).
