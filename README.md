# GEO_COPILOT

Copiloto Geoespacial Inteligente Multiagente con arquitectura Agent-to-Agent (A2A) y razonamiento ReAct.

```
   ██████╗ ███████╗ ██████╗      ██████╗ ██████╗ ██████╗ ██╗██╗      ██████╗ ████████╗
  ██╔════╝ ██╔════╝██╔═══██╗    ██╔════╝██╔═══██╗██╔══██╗██║██║     ██╔═══██╗╚══██╔══╝
  ██║  ███╗█████╗  ██║   ██║    ██║     ██║   ██║██████╔╝██║██║     ██║   ██║   ██║
  ██║   ██║██╔══╝  ██║   ██║    ██║     ██║   ██║██╔═══╝ ██║██║     ██║   ██║   ██║
  ╚██████╔╝███████╗╚██████╔╝    ╚██████╗╚██████╔╝██║     ██║███████╗╚██████╔╝   ██║
   ╚═════╝ ╚══════╝ ╚═════╝      ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝ ╚═════╝    ╚═╝
```

[![Estado](https://img.shields.io/badge/Estado-Funcional%20(ReAct%20hybrid%20%2B%20imagery--mcp)-green)]()
[![Python](https://img.shields.io/badge/Python-3.11%20recomendado-blue)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green)]()
[![React](https://img.shields.io/badge/React-18.2-blue)]()
[![Mapa](https://img.shields.io/badge/Mapa-MapLibre%20GL%205.x-informational)]()
[![Tests](https://img.shields.io/badge/Tests-1795%20BE%20%2B%20464%20FE%20%2B%2033%20E2E-green)]()
[![Agentes](https://img.shields.io/badge/Agentes-6%20%2B%20imagery--mcp-blue)]()
[![Licencia](https://img.shields.io/badge/Licencia-Apache%202.0-blue)](LICENSE)

---

## Tabla de Contenidos

- [Descripción General](#descripción-general)
- [Qué puede hacer](#qué-puede-hacer)
- [Arquitectura del Sistema](#arquitectura-del-sistema)
- [Agentes Especializados](#agentes-especializados)
- [Orquestación: tres caminos](#orquestación-tres-caminos)
- [Servicios MCP enchufables (MCP Hub)](#servicios-mcp-enchufables-mcp-hub)
- [Análisis de imágenes satelitales (imagery-mcp)](#análisis-de-imágenes-satelitales-imagery-mcp)
- [Flujo de Procesamiento](#flujo-de-procesamiento)
- [Human-in-the-Loop (HITL)](#human-in-the-loop-hitl)
- [Stack Tecnológico](#stack-tecnológico)
- [Instalación](#instalación)
- [Uso](#uso)
- [API Reference](#api-reference)
- [Documentación Detallada](#documentación-detallada)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [Contribuir](#contribuir)

---

## Descripción General

GEO_COPILOT es un copiloto geoespacial que **democratiza el análisis espacial**: cualquier persona
—técnica o no— le habla en lenguaje natural y el sistema consulta bases de datos PostGIS, descubre
datasets abiertos, ejecuta análisis espacial y estadístico, procesa imágenes satelitales y dibuja
el resultado en un mapa interactivo.

Por debajo hay un ecosistema de **6 agentes especializados** orquestados con **LangGraph**
(Router + Data + GIS + Python + Symbology + Insights) más un **MCP Hub** que conecta servicios
MCP externos declarados en YAML — hoy, el **microservicio aparte de imágenes** (`imagery-mcp`).
Cada agente delega **toda decisión semántica al LLM** viendo el contexto real
(schema, query del usuario, muestras) y solo valida el resultado contra el esquema para evitar
alucinaciones.

> **Principio agentic (auditoría 2026-05-25, 7 sprints):** se eliminaron **~1350 líneas** de
> heurísticas, plantillas SQL, listas de keywords y fallbacks adivinadores. Si el LLM falla, el
> agente **falla honesto** — nunca sirve un resultado sesgado en silencio. Ver
> [`docs/sistema/02-agentes.md`](docs/sistema/02-agentes.md).

**Lo nuevo respecto a versiones anteriores de este documento:**

- **Razonamiento ReAct híbrido** (default `react_policy="hybrid"`): las consultas **compuestas /
  complejas** se resuelven con un bucle de tool-calling nativo con circuit breaker; las simples
  siguen por el router clásico por intent. La decisión es **por consulta**, no un flag global.
- **Imágenes satelitales de verdad**: NDVI, detección de cambio, estadística zonal e **imagen en
  color (composite RGB)** sobre Sentinel-2 (STAC→COG), con teselas dinámicas. (Antes GEO_COPILOT
  **no** hacía raster; **hoy sí**, vía el servicio `imagery-mcp`).
- **MCP Hub (Fase 3)**: los servicios externos se enchufan como servidores MCP declarados en
  `config/mcp_servers.yaml`; sus tools aparecen en el chat y en el panel de herramientas sin tocar
  código. `imagery-mcp` es el primero.
- **FRT-04 — capa objetivo por nombre**: "colorea **los lotes**" opera sobre la capa **nombrada**
  por el usuario (`target_layer_id`), no sobre la última cargada.
- **Motor de análisis** (intent `analyze`): estadística y ML (`scikit-learn` / `statsmodels`) en
  sandbox, con salida tabla / estadísticas / gráfico.
- **Mapa 100% MapLibre GL** (2D / 2.5D). Cesium fue retirado por completo.

---

## Qué puede hacer

- **Consultas en lenguaje natural** sobre PostGIS: *"Muestra los predios cerca del río"* → SQL
  PostGIS generado por el LLM viendo el schema, ejecutado en transacción **read-only** con rol
  `gis_readonly` y aprobación HITL.
- **Descubrimiento agentic de datos** en ArcGIS Hub Open Data: el LLM arma el plan de búsqueda
  (query + filtros + alternativas + anclaje geográfico). Si los resultados no coinciden con el
  lugar pedido, **refina automáticamente**.
- **Análisis espacial** in-memory (buffer, intersect, union, dissolve, clip…) en sandbox GeoPandas
  con post-validación del GeoDataFrame (CRS, geometrías válidas, `make_valid()`).
- **Análisis estadístico / ML** (intent `analyze`): `scikit-learn` / `statsmodels` con salida
  `table` / `stats` / `chart`.
- **Imágenes satelitales** (Sentinel-2): **NDVI**, **cambio entre dos fechas**, **estadística
  zonal por feature** e **imagen en color** (`true_color` / `false_color` / `agriculture` / `swir`),
  con teselas dinámicas (stretch p2-p98, máscara de nubes SCL, caché en disco).
- **Simbología cartográfica versátil**: el LLM elige entre 6 tipos × 4 métodos de clasificación ×
  paletas por convención, y MapLibre lo renderiza **por feature**.
- **Insights y visualizaciones**: mapas + charts (bar / pie / line / histogram / scatter) + narrativa.
- **Fuentes externas**: ArcGIS REST (Feature / Map / ImageServer, reproyección de cualquier SRID a
  4326), Socrata (con redirección Socrata→ArcGIS), archivos GeoJSON / SHP / GPKG / KML.
- **Human-in-the-Loop**: aprobación humana para toda acción sensible (SQL, código Python, fetch
  externo).
- **Auto-corrección y auto-verificación**: si el LLM genera SQL/Python con error, un corrector LLM
  ve el error y propone fix; un juez LLM verifica que la salida responda la pregunta.

---

## Arquitectura del Sistema

El despliegue (`docker/docker-compose.yml`) levanta estos servicios (red `geo_network`, más una red
interna aislada `docker_proxy`):

| Servicio | Rol |
|---|---|
| **postgis** | BD de dominio PostGIS (imagen `18-master` pinneada por digest). Host `5433`→`5432`. |
| **redis** | Cache + backend opcional de sesiones. |
| **app** | Backend FastAPI + LangGraph (los 6 agentes + orquestador). |
| **frontend** | Build Vite servido por nginx; actúa de **BFF** e inyecta `X-API-Key` server-side. |
| **sandbox** | Contenedor endurecido (`network:none`, `read_only`, `cap_drop: ALL`) donde corre el código del LLM. |
| **docker-socket-proxy** | Único mediador con el daemon Docker (allowlist `CONTAINERS+EXEC+POST`); la app ya **no** monta el socket crudo. |
| **imagery-mcp** | Microservicio MCP aparte (STAC→COG Sentinel-2), único con egress a catálogos externos. La app lo usa como servidor `imagery` del MCP Hub. |
| **hello-geo** | Servidor MCP de **ejemplo** (perfil `examples`, no arranca por defecto). |

### Diagrama de alto nivel

```mermaid
flowchart TB
    subgraph Frontend["Frontend (React + MapLibre GL)"]
        UI["ChatDock / TopBar / LeftPanel / ApprovalPanel"]
        Map["MapLibreMap (capas, teselas, simbología)"]
        Stores["Zustand: session / map / ui / chat"]
    end

    subgraph Nginx["nginx (contenedor frontend, BFF)"]
        BFF["proxy /api y /ws + inyecta X-API-Key"]
    end

    subgraph App["app (FastAPI + LangGraph)"]
        API["api/routes: query, approval, session, metadata, discovery, proxy, tiles, workspace, connections"]
        WS["websocket_endpoint (streaming de progreso)"]
        Graph["GeoAgentGraph (orchestrator/graph.py)"]
        Agents["6 agentes: router, data, gis, python, symbology, insights"]
        Hub["MCP Hub (platform/mcp)"]
    end

    PG[("postgis: BD de dominio PostGIS")]
    RD[("redis: cache / sesiones")]
    SBX["sandbox (docker exec, network:none, read-only)"]
    Proxy["docker-socket-proxy (allowlist EXEC)"]
    IMG["imagery-mcp (STAC to COG Sentinel-2)"]

    UI --> BFF --> API
    Map --> BFF
    API --> Graph
    WS --> Graph
    Graph --> Agents
    Agents --> PG
    Agents --> RD
    Agents -- "docker exec vía proxy" --> Proxy --> SBX
    Graph --> Hub
    Hub -- "MCP streamable HTTP (Bearer)" --> IMG
    IMG -- "teselas PNG vía /proxy/mcp (Bearer server-side)" --> BFF
```

---

## Agentes Especializados

Los 6 agentes viven en `src/geo_copilot/agents/`. Comparten `base.py` (`BaseAgent`,
`AgentResponse`) y un patrón transversal: (a) decisiones semánticas al LLM, la mayoría ya con
**structured outputs** (function-calling forzado, `core/structured_output.py`); (b) métodos
**A2A** deterministas (sin LLM) para consultarse entre sí vía `AgentHub`; (c) todo lo que toca
BD / código / red pasa por `HITLManager` si `hitl_enabled`.

### Colaboración entre agentes

```mermaid
flowchart TD
    U["Usuario: mensaje en lenguaje natural"] --> R["RouterAgent (structured_call: route)"]
    R -->|"intent=query_data"| G["GISAgent (NL a SQL PostGIS)"]
    R -->|"intent=search_external / load_external / select_service"| D["DataAgent (ArcGIS / Socrata / archivos)"]
    R -->|"intent=spatial_operation / analyze"| P["PythonAgent (sandbox GeoPandas / ML)"]
    R -->|"intent=apply_symbology"| S["SymbologyAgent (design_symbology)"]
    R -->|"intent=connected_service"| I["agent_loop -> tool MCP vía MCP Hub (p. ej. imagery-mcp)"]
    R -->|"intent=direct_response / clarify"| F["Responder directo (sin agente downstream)"]

    G --> IN["InsightsAgent (mapa / chart / tabla / narrativa)"]
    P --> IN
    D --> IN

    S -.->|"A2A: evaluate_visualization_fit"| IN
    G -.->|"A2A: lookup_entity / list_available_entities"| D
    P -.->|"A2A: validate_operation"| G
```

### 1. RouterAgent — supervisor de intención
`src/geo_copilot/agents/router_agent/agent.py`

Único punto de decisión de *intent* del camino clásico. Llama al LLM con **structured output
forzado** (nada de "responde solo JSON"). Los **11 intents** válidos: `direct_response`,
`query_data`, `spatial_operation`, `analyze`, `apply_symbology`, `search_external`,
`select_service`, `load_external`, `follow_up`, `clarify`, `connected_service`. Emite además
`is_complex_query`, `additional_operations` y `target_layer_id` (FRT-04). Valida el intent contra
una whitelist y **falla honesto** si no calza; no hay heurística de keywords de respaldo.

### 2. DataAgent — cazador de datos
`src/geo_copilot/agents/data_agent/agent.py`

Descubrimiento e integración de datos: catálogo interno (semantic layer) y externo (ArcGIS REST,
Socrata, archivos GeoJSON / SHP / GPKG / KML). Todo fetch externo pasa por `URLValidator`
(allowlist anti-SSRF) y por HITL. Expone superficies **A2A** deterministas (`list_available_entities`,
`lookup_entity`) que otros agentes consultan sin pasar por el grafo completo.

### 3. GISAgent — analista espacial (NL→SQL PostGIS)
`src/geo_copilot/agents/gis_agent/agent.py`

Traduce lenguaje natural a **SQL PostGIS**. El SQL lo construye `SQLGenerator` (el LLM ve el schema
completo + hints A2A del DataAgent) y lo valida `SQLValidator`. Ejecución endurecida: transacción
**READ ONLY**, `statement_timeout`, denylist, `LIMIT` cap duro y bajada opcional al rol Postgres
`gis_readonly`. Ya **no** hay plantillas SQL (`sql_templates.py` eliminado, Sprint F): el LLM
construye SQL libre viendo el esquema.

### 4. PythonAgent — operador en memoria + motor de análisis
`src/geo_copilot/agents/python_agent/agent.py`

Motor de operaciones espaciales in-memory (buffer, intersect, clip, dissolve…) **y** del intent
`analyze` (estadística / ML). El LLM genera código Python contra un GeoDataFrame `gdf` (y `gdf2`
para cruces cross-source), se aprueba por HITL y se ejecuta en `PythonSandbox`. El scaffolding
post-valida (CRS, `make_valid()`) y separa salida geométrica (`result`) de salida analítica
(`table` / `stats` / `chart`). Incluye **auto-verificación**: un juez LLM comprueba si la salida
responde la pregunta y dispara **un** reintento dirigido si no.

### 5. SymbologyAgent — estilista cartográfico
`src/geo_copilot/agents/symbology_agent/agent.py`

El LLM diseña la simbología completa (`design_symbology` con structured output) viendo schema +
stats + samples + la query. El código hace solo lo determinista (tipo de geometría, estadísticas,
cálculo de breaks, paletas). Consulta vía A2A a `InsightsAgent.evaluate_visualization_fit` para no
aplicar heatmap/cluster que no encajan, y **declara** cualquier degradación (`[A2A]` / `[DEGRADED]`).

**6 tipos** que el LLM puede elegir: `single_symbol`, `unique_values`, `graduated_colors`,
`graduated_symbols`, `heatmap`, `cluster`. **4 métodos** de clasificación numérica:
`natural_breaks` (Jenks), `quantile`, `equal_interval`, `std_deviation`.

### 6. InsightsAgent — visualizador
`src/geo_copilot/agents/insights_agent/agent.py`

Ensambla mapa / gráficos / tabla / narrativa desde resultados de análisis. El LLM diseña qué
visualizaciones tienen sentido viendo la query real. Expone A2A `evaluate_visualization_fit`
(consumido por SymbologyAgent) y `summarize_data_shape`.

---

## Orquestación: tres caminos

`GeoAgentGraph` (`orchestrator/graph.py`) es un `StateGraph` de LangGraph con **tres estrategias**
que coexisten en el mismo grafo compilado. La política `react_policy` (default **`hybrid`**,
`core/config.py`) decide en runtime cuál se usa **por consulta**:

1. **Router clásico por intent** — el LLM clasifica y `_route_from_router` manda directo al agente
   (`data_agent`, `gis_agent`, `python_agent`, `symbology_agent`, `responder`). El intent
   `connected_service` (lo resuelve una tool de un servicio MCP conectado) va siempre al bucle ReAct.
2. **Bucle ReAct `agent_loop`** — para consultas **compuestas / complejas** (o `analyze` sin capa
   activa). El LLM elige **nativamente** entre tools (`query_database`, `spatial_operation`,
   `analyze_layer`, `apply_symbology`, `search_external`, `select_service`,
   `load_external`, `answer` y las tools MCP `<servidor>__<tool>`) hasta cerrar con `answer` o hasta el **circuit breaker**
   (`react_max_tool_calls`, default 8). Reusa los mismos nodos clásicos, así hereda HITL y
   auto-corrección.
3. **Planner multi-paso** — cuando `hybrid` no aplica (p.ej. `hitl_mode=interrupt`). Genera un plan
   fijo que `step_router` ↔ `step_finalizer` ejecutan paso a paso **dentro** del grafo.

```mermaid
flowchart TD
    Q["Query entra al nodo router"] --> RA["RouterAgent.process (el LLM juzga el intent)"]
    RA --> P{"¿intent y flags?"}
    P -->|"direct_response / clarify"| OUT1["responder (respuesta directa)"]
    P -->|"apply_symbology / spatial_operation con capa activa"| DIRECT["agente clásico directo (symbology / python)"]
    P -->|"query_data / select_service / search_external simple"| CLASSIC["data_agent -> gis_agent -> symbology_agent -> insights_agent"]
    P -->|"connected_service"| AGL
    P -->|"additional_operations>=2 O is_complex_query O analyze sin capa"| HY{"¿policy=hybrid y hitl!=interrupt?"}
    HY -->|"sí"| AGL["agent_loop: el LLM elige tools hasta answer o circuit-breaker"]
    HY -->|"no (policy=off)"| PLN["planner: genera execution_plan; step_router / step_finalizer lo ejecutan"]
    CLASSIC --> RESP["responder"]
    DIRECT --> RESP
    AGL --> RESP
    PLN --> RESP
    RESP --> FIN(["fin del turno: _map_final_state (preserva target_layer_id)"])
```

Los tres caminos convergen en `_map_final_state` (o su espejo `_run_react_mode`), que preservan
`target_layer_id` (FRT-04) y el canal analítico (`data` / `visualization`). Detalle completo en
[`docs/sistema/03-orquestador.md`](docs/sistema/03-orquestador.md).

---

## Servicios MCP enchufables (MCP Hub)

Desde la Fase 3 el núcleo **no tiene código propio para cada servicio externo**. El **MCP Hub**
(`src/geo_copilot/platform/mcp/`) lee `config/mcp_servers.yaml` (o `MCP_SERVERS_PATH`) y convierte
cada tool permitida de cada servidor en una herramienta del agente (`<servidor>__<tool>`). Enchufar
un servicio es **editar el YAML, no tocar código** — aparece en el chat y en el panel
"Herramientas · Servicios conectados" del frontend.

- **Config por servidor:** `id`, `url`, `auth` (`bearer` con `secret_ref: env:VAR`, o `none`),
  `conformance` (G0/G1/G2), `trust` (`untrusted` por defecto), `tools{allow,deny}`, `policy`
  (riesgo por defecto, HITL por riesgo, `timeout_s`, `max_result_mb`), `tiles{prefixes}`.
- **Argumentos geo por referencia:** el LLM pasa `activa`, `ds_…`, el id de una capa o `viewport`, y
  el hub inyecta la geometría real.
- **Resultados `GeoResult`:** `feature_collection` → dataset del workspace, `raster_tiles` → capa del
  mapa vía `/api/v1/proxy/mcp/{server}/…`, `stats`/`table` → tabla; los `facts` van al LLM.
- **Seguridad:** descripciones tratadas como texto externo no confiable; *pinning* por hash (si una
  tool cambia, se deshabilita hasta re-aprobarla); HITL por riesgo; tras la salida de un servidor
  `untrusted`, usar otro servidor en el mismo turno pide aprobación humana.
- **Escala:** con más de 25 tools MCP (`MCP_TOOLS_UMBRAL`), el LLM usa `find_tools(query)` para
  encontrar y activar las relevantes.

Hay un servidor de ejemplo (`services/hello_geo`, perfil `examples` del compose) y un kit compartido
(`packages/geo_mcp_kit`). Guía: [`docs/sistema/12-como-enchufar-un-mcp.md`](docs/sistema/12-como-enchufar-un-mcp.md).

## Análisis de imágenes satelitales (imagery-mcp)

`imagery-mcp` es un **microservicio aparte** —para la app, el servidor `id: imagery` del MCP Hub— (`services/imagery_mcp/`) que expone **5 tools** sobre
Sentinel-2 L2A vía STAC→COG (`planetary-computer` / `earth-search`):

| Tool | Scope | Qué hace |
|---|---|---|
| `imagery_search_scenes` | `imagery:read` | Busca escenas que contienen el AOI. |
| `imagery_ndvi` | `imagery:compute` | NDVI + teselas dinámicas (stretch p2-p98). |
| `imagery_change` | `imagery:compute` | Cambio entre dos fechas (rampa divergente). |
| `imagery_zonal_stats` | `imagery:compute` | Estadística zonal / NDVI por feature de la capa. |
| `imagery_composite` | `imagery:compute` | Imagen en color RGB (`true_color` / `false_color` / `agriculture` / `swir`). |

**Seguridad (fail-closed):** autenticación **Bearer** (sha256, comparación en tiempo constante),
scopes `imagery:read` / `imagery:compute`, y un mapa `TOOL_SCOPES`
(`services/imagery_mcp/imagery_mcp/auth.py`) que asigna a cada tool su scope. **Toda tool nueva que
no se agregue a `TOOL_SCOPES` queda denegada con 403.** Rate limit por ventana deslizante (las
teselas pesan 0.05). El servicio **rehúsa arrancar** si `IMAGERY_MCP_KEYS` está vacío.

**Teselas dinámicas:** `TilePool` sirve PNG on-demand desde COG remotos con máscara de nubes SCL
alineada al grid, caché de dos niveles (memoria + disco) y prewarm. El navegador **nunca** habla
directo con el MCP: consume las teselas por el **proxy genérico de la app**
(`/api/v1/proxy/mcp/imagery/tiles/…`, `/tiles-diff/…`, `/tiles-rgb/…`), que inyecta el Bearer
server-side.

```mermaid
sequenceDiagram
    participant U as "Usuario (chat)"
    participant G as "GeoAgentGraph (router -> agent_loop)"
    participant H as "MCP Hub (platform/mcp/hub.py)"
    participant M as "imagery-mcp (AuthMiddleware + FastMCP)"
    participant F as "Frontend MapLibre"
    participant Px as "proxy /api/v1/proxy/mcp (api/routes/proxy.py)"

    U->>G: "NDVI de este lote entre marzo y junio"
    G->>G: "intent=connected_service -> el LLM elige imagery__imagery_ndvi"
    G->>H: "tool call (aoi='activa', fechas)"
    H->>M: "tools/call imagery_ndvi + Bearer (MCP streamable HTTP)"
    M->>M: "Bearer (401) -> rate limit (429) -> TOOL_SCOPES (403)"
    M-->>H: "GeoResult {raster_tiles, stats, facts}"
    H-->>G: "capa external_imagery (XYZ vía /proxy/mcp) + tabla + facts"
    G-->>U: "capa NDVI + métricas"
    F->>Px: "GET /api/v1/proxy/mcp/imagery/tiles/{scene}/{z}/{x}/{y}.png"
    Px->>M: "GET /tiles/... (Bearer inyectado server-side)"
    M-->>Px: "PNG 256px (stretch p2-p98 + máscara SCL)"
    Px-->>F: "PNG -> tesela XYZ en MapLibre"
```

Detalle: [`docs/sistema/01-arquitectura.md`](docs/sistema/01-arquitectura.md) y
[`docs/SPEC_C3_IMAGERY_MCP_2026-07-19.md`](docs/SPEC_C3_IMAGERY_MCP_2026-07-19.md).

---

## Flujo de Procesamiento

Flujo típico de una consulta que combina búsqueda, carga, simbología y FRT-04:

```mermaid
sequenceDiagram
    participant U as Usuario
    participant CD as "ChatDock (frontend)"
    participant API as "POST /api/v1/query"
    participant G as "GeoAgentGraph"
    participant H as "HITLManager"
    participant RT as "pickRestyleTarget (frontend)"
    participant ML as "MapLibreMap"

    U->>CD: "Busca hospitales en Bogotá"
    CD->>API: "POST /query (map_context, session_id)"
    API->>G: "process(...)"
    G->>G: "RouterAgent -> search_external ; DataAgent -> ArcGIS Hub"
    G-->>CD: "Encontré 5 servicios; selecciona uno"

    U->>CD: "carga el 3"
    CD->>API: "POST /query (selección de servicio)"
    API->>G: "DataAgent carga GeoJSON ; SymbologyAgent estiliza"
    G-->>ML: "capa GeoJSON en el mapa"

    U->>CD: "colorea los hospitales de rojo"
    CD->>API: "POST /query (map_context con todas las capas)"
    API->>G: "intent=apply_symbology + target_layer_id (FRT-04)"
    G->>H: "request_approval si toca acción sensible"
    H-->>G: "approved"
    G-->>CD: "symbology + geojson + target_layer_id"
    CD->>RT: "pickRestyleTarget(layers, target_layer_id)"
    RT-->>CD: "capa 'hospitales' (la nombrada, no la activa)"
    CD->>ML: "removeLayer + addLayer (restyle in place)"
```

Diagramas por nodo y detalle de estados: [`docs/sistema/08-flujos.md`](docs/sistema/08-flujos.md).

---

## Human-in-the-Loop (HITL)

Toda acción sensible (SQL del LLM, código Python, fetch externo) requiere **aprobación humana**. El
mecanismo de producción es `blocking` (`security/hitl.py::HITLManager`): la solicitud espera por
WebSocket hasta que el usuario aprueba / rechaza / modifica, o hasta `hitl_timeout`. Existe también
un modo `interrupt` (LangGraph `interrupt()` + checkpointer) documentado como **spike diferido**.

```mermaid
stateDiagram-v2
    [*] --> Pendiente: "HITLManager.request_approval()"
    Pendiente --> Aprobado: "POST /approval/{id} action=approve"
    Pendiente --> Rechazado: "action=reject"
    Pendiente --> Modificado: "action=modify (modified_content)"
    Pendiente --> Expirado: "timeout (hitl_timeout)"
    Aprobado --> [*]: "SET LOCAL ROLE gis_readonly + ejecuta SQL/código"
    Modificado --> [*]: "ejecuta el contenido modificado"
    Rechazado --> [*]: "cancela la acción"
    Expirado --> [*]: "cancela la acción (fail-safe)"
```

Los endpoints de aprobación exigen `session_id` como *ownership check* (IDOR cerrado). Detalle en
[`docs/sistema/04-backend.md`](docs/sistema/04-backend.md).

---

## Stack Tecnológico

### Backend (Python 3.11+)

| Paquete | Uso |
|---------|-----|
| FastAPI | Framework web async |
| LangGraph | Orquestación del grafo de agentes (StateGraph, ReAct, planner) |
| asyncpg | Driver PostgreSQL async |
| Pydantic v2 | Validación de datos / settings |
| openai · anthropic | Clientes LLM (multi-proveedor) |
| geopandas · shapely | Manipulación geoespacial en memoria |
| httpx | Cliente HTTP async (REST externo + JSON-RPC al imagery-mcp) |

### Servicio imagery-mcp (Python)

| Paquete | Uso |
|---------|-----|
| FastMCP | Servidor MCP streamable-HTTP |
| rasterio · rio-tiler | Lectura ventaneada de COG + teselado dinámico |
| pystac-client | Búsqueda STAC (Planetary Computer / Earth Search) |

### Frontend (Node.js 22+)

| Paquete | Uso |
|---------|-----|
| React 18.2 · TypeScript 5.2 · Vite 5 | UI / build |
| **MapLibre GL 5.x** | Mapas vectoriales 2D / 2.5D (Cesium retirado 100%) |
| Zustand 4.4 | Estado global (session / map / ui / chat) |
| Tailwind CSS · Recharts · Lucide | Estilos / gráficos / iconos |

### Infraestructura

| Servicio | Uso |
|----------|-----|
| PostgreSQL 15+ / PostGIS 3.3+ | BD de dominio (deploy usa `postgis:18-master`) |
| Redis 7 | Cache y sesiones opcionales |
| Docker + Compose v2 | Contenedorización (app, sandbox, socket-proxy, imagery-mcp) |

---

## Instalación

### Requisitos previos

- Python 3.11 recomendado (es la versión que usan Docker y CI); `pyproject.toml` declara un mínimo real de **3.10** (`requires-python = ">=3.10"`). En Windows se sugiere **Conda/Miniconda**.
- Node.js 22+ (LTS; `engines` en `frontend/package.json`: una dependencia pide Node >= 22)
- Docker + Docker Compose v2 (para el Quickstart)
- Git

---

### Quickstart con Docker (recomendado)

Levanta el stack completo (PostGIS + Redis + backend + frontend + sandbox + socket-proxy +
imagery-mcp) con un solo comando.

**1. Configurar `.env`**

```bash
cp .env.example .env
# Edita .env y pon al menos:
#   LLM_PROVIDER=openai            (o anthropic / azure)
#   OPENAI_API_KEY=sk-...          (tu clave real)
#   LLM_MODEL=gpt-4o-mini          (o el modelo/deployment que uses)
#   ALLOWED_DOMAINS=["datos.gov.co","geoportal.igac.gov.co"]
#
#
# Y las credenciales del stack, que NO tienen valor por defecto a propósito
# (el compose las declara con ${VAR:?} y sin ellas no arranca — es lo que
# impide que un despliegue quede corriendo con una contraseña publicada aquí):
#   POSTGRES_PASSWORD, GEO_APP_PASSWORD, REDIS_PASSWORD
#   IMAGERY_MCP_APP_KEY e IMAGERY_MCP_KEYS  (genera con: openssl rand -hex 24)
# Los servidores MCP (URL, auth, tools) se declaran en config/mcp_servers.yaml.
```

> **Esto se corre en local.** No hay autenticación delante del BFF ni TLS: nginx inyecta la
> API key en todo `/api/`, y lo único que hoy contiene eso es que los puertos están atados a
> `127.0.0.1`. Un `ssh -L` entrega la API entera. Antes de exponerlo a una red que no controlas
> hace falta poner autenticación delante y terminar TLS. Ver [`SECURITY.md`](SECURITY.md).

> El compose **sobrescribe** `DATABASE_URL` y `REDIS_URL` para apuntar a los servicios internos
> (`postgis:5432`, `redis:6379`). La app habla con `imagery-mcp` a través del MCP Hub (servidor
> `imagery` en `config/mcp_servers.yaml`, credencial `IMAGERY_MCP_API_KEY`). Si el servidor no
> responde, sus tools aparecen como **deshabilitadas** con el motivo (fallo honesto); el resto del
> sistema funciona igual.

**2. (Opcional) Cargar datos en PostGIS**

| Caso | Qué hacer |
|---|---|
| **Tengo un dump de mi BD** | Ponlo en `docker/init-db/`; PostGIS lo ejecuta en el primer arranque (ver [`docker/init-db/README.md`](docker/init-db/README.md)). |
| **Tengo PostGIS local con datos** | Quita el servicio `postgis` del compose y apunta `DATABASE_URL` a tu host. |
| **Quiero empezar vacío** | No hagas nada — PostGIS arranca limpio. |

**3. Levantar el stack**

```bash
docker compose --env-file .env -f docker/docker-compose.yml up --build
```

> **`--env-file .env` no es opcional.** Con `-f docker/docker-compose.yml`, el
> *project directory* de Compose pasa a ser `docker/`, donde no hay `.env`: sin
> el flag, las interpolaciones `${...}` (`POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
> `API_KEY`, `IMAGERY_MCP_KEYS`) **no leen tu `.env` de la raíz**. Antes esto
> fallaba en silencio cayendo a los defaults; hoy el arranque aborta con un
> error explícito. Aplica al resto de comandos `docker compose` de esta sección.

Servicios expuestos (todos atados a **loopback** — ver R0.3 de la auditoría):

| Servicio | URL | Descripción |
|---|---|---|
| Frontend | http://localhost:3000 | UI completa (nginx BFF con proxy a la API) |
| API | http://localhost:8000 | FastAPI directo (Swagger en `/docs`) |
| Health | http://localhost:8000/health | 200 si todo OK, 503 si falta BD/LLM |
| PostGIS | localhost:**5433** | `geo_user` / la de tu `POSTGRES_PASSWORD` / `geo_copilot` |
| Redis | localhost:6379 | requiere `REDIS_PASSWORD` |

**4. Verificar**

```bash
curl http://localhost:8000/health
# {"status":"healthy", ...} si el LLM key es válido y la BD arrancó
```

Abre http://localhost:3000 en el navegador.

**Comandos útiles**

```bash
docker compose --env-file .env -f docker/docker-compose.yml logs -f app     # logs en vivo
docker compose --env-file .env -f docker/docker-compose.yml down            # parar
docker compose --env-file .env -f docker/docker-compose.yml down -v         # parar + borrar la BD (pierdes el volumen)
docker compose --env-file .env -f docker/docker-compose.yml up --build frontend  # rebuild solo el frontend
```

---

### Instalación manual (desarrollo)

Útil para hot-reload y debug. Para el backend recomendamos **Conda** (maneja mejor los binarios
nativos de GDAL/GEOS/PROJ en Windows).

#### A. Entorno Python con Conda

```powershell
conda init powershell            # (Windows, solo una vez) y REABRIR la terminal
conda create -n geocopilot python=3.11 -y
conda activate geocopilot
conda install -c conda-forge geopandas shapely -y   # binarios nativos
pip install -e ".[dev]"
```

> Alternativa con `venv`:
> ```bash
> python -m venv venv
> source venv/bin/activate      # Linux/Mac
> .\venv\Scripts\activate       # Windows
> pip install -e ".[dev]"
> ```

#### B. Variables de entorno

```bash
cp .env.example .env
# LLM_PROVIDER / OPENAI_API_KEY / LLM_MODEL / DATABASE_URL / REDIS_URL (opcional)
```

#### C. Levantar BD + cache en Docker (lo más cómodo)

```powershell
docker compose --env-file .env -f docker/docker-compose.yml up postgis redis
```

> Si corres el backend fuera de Docker contra ese PostGIS, apunta a **localhost:5433**:
> `DATABASE_URL=postgresql://geo_app:$GEO_APP_PASSWORD@localhost:5433/geo_copilot`
>
> **Usa `geo_app`, no `geo_user`** (R0.6 / AUD-04). `geo_user` es SUPERUSUARIO en la
> imagen `postgis/postgis`, y una transacción `READ ONLY` no contiene a un superusuario:
> `pg_read_file('/etc/passwd')` y los hashes de `pg_authid` quedarían al alcance del SQL
> que genera el LLM. `geo_app` es `NOSUPERUSER` y miembro de `gis_readonly`; su contraseña
> la fija `docker/init-db/05_geo_app_password.sh` desde `GEO_APP_PASSWORD`.

#### D. Backend y frontend (terminales separadas)

```powershell
# Terminal 1 — backend
conda activate geocopilot
python -m geo_copilot.api.app        # uvicorn con reload en :8000

# Terminal 2 — frontend
cd frontend
npm install
npm run dev                          # Vite hot-reload en :5173
```

---

### Pruebas (tests)

#### Backend (pytest)

Por defecto **solo corren los tests deterministas** (los markers `integration` y `llm` quedan
excluidos vía `addopts`):

```powershell
pytest                                   # suite determinista (1795 tests) + gate de cobertura
pytest --no-cov                          # más rápido, sin gate
pytest tests/ruta/test_x.py::test_y -v   # un test concreto
```

#### Integración (PostGIS real)

```powershell
docker compose -f docker-compose.test.yml up -d   # BD de test en el puerto 5434
pip install -e ".[dev,integration]"
pytest -m integration
docker compose -f docker-compose.test.yml down -v
```

`pytest -m llm` corre los tests que llaman a un LLM real (hacen `skip` si no hay credenciales).

#### Frontend

```powershell
cd frontend
npm test                     # unit (vitest, jsdom)
npm run test:e2e             # E2E Playwright (mockeado, headless)
npm run test:e2e:real        # E2E de integración contra el stack Docker real
npm run lint                 # eslint
npm run build                # tsc + vite build
```

> **CI** (`.github/workflows/ci.yml`) corre `ruff` + `pytest` (deterministas) y `vitest` + `build`.
> Playwright, `-m integration`, `-m llm` y el benchmark agentic son de ejecución **manual/local**.

---

## Uso

### Ejemplos de consultas

```text
# Base de datos (PostGIS)
"Muestra los predios del barrio Centro"
"¿Cuál es el predio más grande?"
"Predios a menos de 500 metros del río"

# Descubrimiento + carga externa
"Busca estaciones de bomberos en Bogotá"
"carga el 3"
"aplica buffer de 1km color verde"

# Simbología por nombre de capa (FRT-04)
"colorea los lotes de rojo"
"pinta los predios por estrato"

# Análisis estadístico / ML
"analiza la correlación entre área y valor catastral"

# Imágenes satelitales
"NDVI de este lote entre marzo y junio"
"muéstrame la imagen en color real de esta zona"
"detecta el cambio de vegetación entre 2023 y 2024"

# Follow-ups
"de esos, ¿cuáles tienen más de 2 pisos?"
"¿qué SQL ejecutaste?"
```

---

## API Reference

Todas las rutas cuelgan de `/api/v1` y exigen `X-API-Key` (salvo `/health`). El frontend (nginx BFF)
inyecta la clave server-side, así que el bundle del navegador nunca la lleva.

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/v1/query/` | Procesar consulta en lenguaje natural (punto de entrada principal) |
| `GET` | `/api/v1/query/{id}` | **[501 stub]** estado de query asíncrona (no implementado) |
| `POST` | `/api/v1/query/{id}/cancel` | **[501 stub]** cancelar query (no implementado) |
| `GET` | `/api/v1/approval/pending` | Aprobaciones HITL pendientes de la sesión |
| `GET` | `/api/v1/approval/{id}` | Detalle de una solicitud HITL |
| `POST` | `/api/v1/approval/{id}` | Aprobar / rechazar / modificar |
| `GET` | `/api/v1/approval/history/{session_id}` | Historial de aprobaciones |
| `POST` · `GET` · `PATCH` · `DELETE` | `/api/v1/session/...` | Crear / leer / actualizar / eliminar sesión + `/reset` + `/history` |
| `GET` | `/api/v1/metadata/entities · /categories · /search · /tables` | Introspección de esquema PostGIS |
| `POST` | `/api/v1/discovery/search` | Buscar datasets en ArcGIS Hub |
| `POST` | `/api/v1/discovery/load` | Cargar dataset (geojson + simbología, o imagery) |
| `GET` | `/api/v1/discovery/regions · /health` | Regiones del catálogo · smoke test |
| `GET` | `/api/v1/proxy/imagery · /imagery-identify` | Proxy anti-SSRF/CORS de raster ArcGIS |
| `GET` | `/api/v1/proxy/mcp/{server_id}/{path}` | Proxy genérico de teselas de un servidor MCP (solo prefijos declarados, credencial server-side) |
| `GET` | `/api/v1/tiles/{schema}/{table}/{z}/{x}/{y}.pbf` | Teselas vectoriales MVT on-the-fly desde PostGIS |
| `GET` | `/api/v1/connections` | Estado de los servidores MCP y sus tools (incluidas las deshabilitadas y por qué) |
| `GET` | `/api/v1/connections/tools` | Tools MCP habilitadas con su `input_schema` |
| `POST` | `/api/v1/connections/{server}/tools/{tool}/run` | Panel transaccional (misma capacidad que usa el agente; tools `write` → 403) |
| `POST` | `/api/v1/connections/{server}/tools/{tool}/approve` | Re-aprobar una tool deshabilitada por *pinning* |
| `WS` | `/ws/{session_id}` | Streaming de progreso agéntico + HITL |
| `GET` | `/health` | Estado del servicio (raíz, fuera de `/api/v1`) |

### Ejemplo de request

```bash
curl -X POST http://localhost:8000/api/v1/query/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <tu-api-key>" \
  -d '{"query": "Muestra los predios del barrio Centro", "session_id": "abc123"}'
```

> La referencia completa de la API se autogenera en runtime: `/docs` (Swagger UI) y `/redoc`.

---

## Documentación Detallada

La documentación consolidada (con diagramas mermaid y lenguaje accesible) vive en `docs/sistema/`:

| Documento | Descripción |
|-----------|-------------|
| [docs/sistema/01-arquitectura.md](docs/sistema/01-arquitectura.md) | Arquitectura general + topología de servicios + MCP Hub / imagery-mcp |
| [docs/sistema/02-agentes.md](docs/sistema/02-agentes.md) | Los 6 agentes en detalle (patrón agentic, A2A) |
| [docs/sistema/03-orquestador.md](docs/sistema/03-orquestador.md) | LangGraph: router clásico / ReAct / planner |
| [docs/sistema/04-backend.md](docs/sistema/04-backend.md) | API, WebSocket, auth, HITL, semantic layer |
| [docs/sistema/05-frontend.md](docs/sistema/05-frontend.md) | Componentes, stores, MapLibre, simbología |
| [docs/sistema/06-conexion-back-front.md](docs/sistema/06-conexion-back-front.md) | Contrato de datos back ↔ front |
| [docs/sistema/07-discovery.md](docs/sistema/07-discovery.md) | Descubrimiento ArcGIS Hub |
| [docs/sistema/08-flujos.md](docs/sistema/08-flujos.md) | Flujos de una consulta, nodo por nodo |
| [docs/sistema/09-configuracion-y-deploy.md](docs/sistema/09-configuracion-y-deploy.md) | Configuración y despliegue |
| [docs/sistema/10-test-roadmap.md](docs/sistema/10-test-roadmap.md) | Estrategia y roadmap de tests |
| [docs/sistema/11-como-probar-todo.md](docs/sistema/11-como-probar-todo.md) | Guía para probar el sistema end-to-end |
| [docs/sistema/12-como-enchufar-un-mcp.md](docs/sistema/12-como-enchufar-un-mcp.md) | Cómo conectar un servidor MCP nuevo al MCP Hub |

Specs vigentes: [`docs/SPEC_C3_IMAGERY_MCP_2026-07-19.md`](docs/SPEC_C3_IMAGERY_MCP_2026-07-19.md).
Los pendientes se siguen en los issues del repositorio.

---

## Estructura del Proyecto

```
GEO_COPILOT/
│
├── src/geo_copilot/            # Backend Python (FastAPI + LangGraph)
│   ├── agents/                 # 6 agentes especializados
│   │   ├── base.py             # BaseAgent, AgentResponse
│   │   ├── router_agent/       # Enrutamiento por intent (structured output)
│   │   ├── data_agent/         # Descubrimiento + conectores (arcgis, socrata, files)
│   │   ├── gis_agent/          # NL→SQL PostGIS (sql_generator/validator/corrector, sandbox)
│   │   ├── python_agent/       # Sandbox GeoPandas + análisis (analyze) + code_corrector
│   │   ├── symbology_agent/    # Diseño de simbología (styles.py)
│   │   └── insights_agent/     # Mapa / chart / tabla / narrativa
│   │
│   ├── orchestrator/           # LangGraph
│   │   ├── graph.py            # GeoAgentGraph (StateGraph, tres caminos)
│   │   ├── nodes/              # Un módulo por nodo (router, agent_loop, planner,
│   │   │                       #   step_router, step_finalizer, responder, ...)
│   │   ├── react_tools.py      # dispatch de tools del bucle ReAct
│   │   ├── tool_schemas.py     # esquemas de tools nativas
│   │   ├── circuit_breaker.py  # límite del bucle ReAct
│   │   └── sessions/           # backends de sesión (memory / redis)
│   │
│   ├── api/                    # FastAPI + WebSocket
│   │   ├── app.py              # create_app() / lifespan
│   │   ├── websocket.py        # streaming de progreso + HITL
│   │   ├── auth.py             # API key (fail-closed) + guards de arranque
│   │   └── routes/             # query, approval, session, metadata, discovery,
│   │                           #   proxy, tiles, workspace, connections
│   │
│   ├── core/                   # config.py (react_policy), llm_client.py, security/url_validator.py
│   ├── platform/               # capacidades, workspace y mcp/ (MCP Hub: config, connection,
│   │                           #   hub, busqueda)
│   ├── security/               # hitl.py (HITLManager, modo blocking)
│   └── semantic/               # introspector.py + layer.py (auto-descubrimiento PostGIS)
│
├── services/
│   ├── imagery_mcp/            # Microservicio MCP aparte (STAC→COG Sentinel-2)
│   │   └── imagery_mcp/        # server.py, engine.py, tiles.py, auth.py (TOOL_SCOPES),
│   │                           #   georesult.py, providers.py, config.py
│   └── hello_geo/              # Servidor MCP de ejemplo (perfil `examples`)
│
├── packages/geo_mcp_kit/       # Kit compartido para escribir servidores MCP geo
│
├── frontend/                   # React + MapLibre GL
│   └── src/
│       ├── App.tsx
│       ├── components/         # MapLibreMap, ChatDock, ApprovalPanel, LeftDrawer,
│       │                       #   McpToolsPanel, DataDiscoveryPanel, MapLegend, ...
│       ├── stores/             # session / map / ui / chat (Zustand)
│       ├── lib/                # maplibreSymbology, restyleTarget, mapTestState, ...
│       └── utils/              # mapContext (buildMapContext), zoomIntent, ...
│
├── tests/                      # 1795 tests backend (markers integration/llm aparte)
├── docker/                     # docker-compose.yml, Dockerfiles, init-db/, nginx BFF
├── docs/                       # Documentación (docs/sistema/ = canónica)
├── config/                     # YAML (discovery_catalog, mcp_servers, etc.)
└── semantic_layer/             # Overrides opcionales del semantic layer
```

---

## Contribuir

1. Fork del repositorio.
2. Crea una rama (`git checkout -b feature/nueva-funcionalidad`).
3. Ejecuta los tests (`pytest` + `cd frontend && npm test`).
4. Commit (`git commit -m 'feat: mi nueva funcionalidad'`).
5. Push y abre un Pull Request.

**Convenciones de commits:** `feat:` · `fix:` · `docs:` · `refactor:` · `test:`

> **Nota agentic:** si añades una tool nueva al `imagery-mcp`, debes registrarla también en
> `TOOL_SCOPES` (`services/imagery_mcp/imagery_mcp/auth.py`) o quedará denegada con 403 (fail-closed).

---

## Licencia

[Apache License 2.0](LICENSE). Puedes usar, modificar y redistribuir el código, incluso
comercialmente, conservando el aviso de copyright y la licencia. Se eligió sobre MIT por la
concesión explícita de patentes de la §3, que en un sistema que ejecuta código generado por IA
protege a quien contribuye y a quien lo usa.

El repositorio **no distribuye datos**: `data/` está fuera de git y los fixtures de tests son
sintéticos. Cualquier dato catastral real que cargues es tuyo y se rige por los términos de su
fuente, no por esta licencia.

## Contacto

- Issues: [GitHub Issues](https://github.com/geoai-latam/GEO_COPILOT/issues)
- Vulnerabilidades: **no abras un issue** — ver [`SECURITY.md`](SECURITY.md)
- Cómo contribuir: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Documentación: [docs/](docs/) · canónica en [docs/sistema/](docs/sistema/)
