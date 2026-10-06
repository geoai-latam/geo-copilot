# SPEC C3-v1 — Imagery como servicio MCP aparte (con autenticación)

> ⚠️ **SUPERADO en F3 (2026-09-25).** El núcleo ya no tiene código propio de
> imagery: `ImageryClient`, el nodo `imagery_agent`, la capacidad
> `imagery_analysis`, `routes/imagery.py` y los proxies `/proxy/ndvi-tiles` (y
> diff/rgb) se borraron. imagery-mcp es un servidor más del MCP Hub
> (`config/mcp_servers.yaml`), devuelve `GeoResult` y sus teselas pasan por el
> proxy genérico `/api/v1/proxy/mcp/imagery/…`. Ver
> `docs/sistema/12-como-enchufar-un-mcp.md` y el acta `docs/validacion/FASE_3_2026-09-25.md`.
> Lo que sigue vale como registro del diseño del SERVICIO (motor, límites, auth).

> 📐 **Registro de diseño, no documentación operativa.** Este spec explica **por
> qué** `imagery-mcp` es un servicio aparte y con qué criterio se diseñó. El
> servicio **existe y está desplegado** desde julio de 2026, así que el spec
> ya no describe una intención sino una decisión tomada. Se conserva en
> `docs/` (y no en `docs/archive/`) porque cuatro archivos de código lo citan
> como la forma del contrato: `services/imagery_mcp/imagery_mcp/__init__.py:3`,
> `src/geo_copilot/core/config.py:285`, `src/geo_copilot/core/imagery_client.py:11`
> y `docker/docker-compose.yml:301`.
>
> **Para operar el servicio, no leas esto**: lee
> [`sistema/04-backend.md`](sistema/04-backend.md) (cliente y proxy) y
> [`sistema/09-configuracion-y-deploy.md`](sistema/09-configuracion-y-deploy.md)
> (variables, claves y scopes). Si este spec y `docs/sistema/` se contradicen,
> manda `docs/sistema/`.


**Fecha:** 2026-07-19 · **Decisión del revisor:** C3 arranca la Fase 5; MCP-ready
desde el día 1; el MCP es un servicio APARTE con autenticación de acceso a datos
y herramientas. **Estado:** diseño validado con sondas en vivo (ver §Evidencia).

---

## 1. Por qué así (contexto de la decisión)

Los servicios ArcGIS que consume Discovery hoy son mayoritariamente **productos
visuales RGB** (MapServer tiles; ImageServer RGB-only): sin banda NIR y sin
valores de reflectancia no hay NDVI ni análisis serio. La fuente analítica
correcta es **imagery multiespectral abierta via STAC → COG** (Sentinel-2 L2A:
rojo B04 + NIR B08 a 10 m, revisita ~5 días sobre Colombia, gratis). La lectura
es **ventaneada**: para "el NDVI de estos lotes" se descargan KBs del bbox, no
la escena de ~1 GB.

Además, la capacidad se construye como **servicio MCP independiente**: el mismo
servidor sirve a la app GEO_COPILOT **y** a terceros (Claude Desktop, agentes
QGIS, otros copilotos) con sus propias credenciales — la jugada estratégica del
análisis de mercado (MCP como estándar de interop; nicho self-hosted LATAM).
Esto adelanta la mitad de C6 (federación STAC) y prepara T33 (MCP server
general) — las tools de imagery se exponen 1:1 sin refactor.

## 2. Evidencia (sondas en vivo 2026-07-19, red del despliegue)

| Prueba | Resultado |
|---|---|
| STAC Earth Search (Element84/AWS us-west-2) búsqueda | 0.4 s, escenas correctas |
| Lectura COG ventaneada desde Element84 | **marginal**: 131 s/banda y truncados intermitentes (3/3 fallos en una banda) |
| STAC Planetary Computer (Azure) búsqueda | 2.9 s |
| Firma SAS anónima PC | OK (`/api/sas/v1/token/{collection}`) |
| Lectura COG ventaneada desde PC | **2–12 s/banda** (~20× mejor); truncados transitorios ocasionales |
| NDVI ventana 443×444 px sobre Bogotá | computado OK (media≈0.3) |
| Escena por tile MGRS | un AOI puede caer FUERA del tile de una escena → hay que seleccionar por **contención del AOI** |

**Conclusiones de diseño derivadas:**
1. **Proveedor default: Planetary Computer**; Earth Search como fallback — ambos
   detrás de la misma interfaz `StacProvider`.
2. **Retry a nivel de banda es OBLIGATORIO** (reabrir+releer ante
   `RasterioIOError`): GDAL no reintenta cuerpos truncados con HTTP 200.
3. **Selección de escena por contención del AOI**; si ninguna escena contiene el
   AOI completo → v1 responde honesto con la mejor cobertura parcial declarada
   (`coverage_pct`), sin mosaicar (mosaico = v2).
4. En Windows dev: fijar `PROJ_DATA` al bundle de rasterio (conflicto con el
   proj.db de PostgreSQL). En el contenedor no aplica.

## 3. Arquitectura

```
┌────────────────────────┐      MCP (streamable HTTP + Bearer)      ┌─────────────────────────┐
│ GEO_COPILOT app        │ ───────────────────────────────────────► │ imagery-mcp (aparte)    │
│  - ImageryMCPClient    │                                          │  - auth.py (keys+scopes)│
│  - ReAct tool imagery_*│                                          │  - server.py (FastMCP)  │
│  - nodo imagery (wired)│      otros clientes (Claude, QGIS…)      │  - engine.py (NDVI/…)   │
│  sandbox: network NONE │ ────────────────► con SUS keys           │  - providers.py (STAC)  │
└────────────────────────┘                                          │  egress: PC/AWS         │
                                                                    └─────────────────────────┘
```

- **Aislamiento**: el sandbox de la app sigue `network: none`. TODO el fetch y
  cómputo raster ocurre en el servicio imagery (su contenedor tiene egress a
  planetarycomputer.microsoft.com / *.blob.core.windows.net /
  sentinel-cogs.s3.*.amazonaws.com y NADA más — allowlist de egress).
- **Ubicación**: `services/imagery_mcp/` con Dockerfile y requirements propios
  (rasterio NO entra al contenedor de la app).
- **Compose**: servicio `imagery-mcp` en `docker/docker-compose.yml`, puerto
  interno 9100; la app lo alcanza por red interna (`http://imagery-mcp:9100`).

## 4. Autenticación (v1) — "MCP aparte con autenticación"

- Transporte: **MCP streamable HTTP** + `Authorization: Bearer <api-key>`.
- Claves API estáticas definidas por config del SERVICIO (env/YAML), cada una:
  `{name, key_hash (sha256), scopes[], rate_limit_per_min}`.
- **Scopes**: `imagery:read` (search_scenes) · `imagery:compute`
  (ndvi/change/zonal). 401 sin clave; 403 sin scope; 429 sobre el rate limit.
- La app tiene SU clave (read+compute) en settings. Terceros reciben claves
  propias con los scopes que se les concedan → mismo servidor, dos audiencias.
- Upgrade path documentado a OAuth 2.1 resource-server (spec de auth MCP) sin
  romper claves existentes. Middleware ASGI sobre `streamable_http_app()`.

## 5. Tools MCP (contratos v1)

Todas reciben/retornan JSON tipado; límites duros en el servicio.

1. `imagery_search_scenes(aoi_geojson, date_from, date_to, max_cloud_pct=20, limit=10)`
   → `{scenes: [{id, datetime, cloud_pct, provider, contains_aoi, coverage_pct}]}`
   · scope `imagery:read`.
2. `imagery_ndvi(aoi_geojson, date_from?, date_to?, scene_id?)`
   → `{scene, cloud_pct, stats {mean,min,max,p25,p50,p75,std,px}, png_overlay
   {base64, bounds}, degraded?}` · scope `imagery:compute`. Sin `scene_id`
   elige la de MENOS nubes que contenga el AOI en el rango (default: últimos
   45 días).
3. `imagery_change(aoi_geojson, date_a, date_b, index="ndvi", window_days=15)`
   → `{scene_a, scene_b, diff_stats, png_overlay, interpretation_facts}` —
   HECHOS (pct píxeles con |Δ|>0.1 etc.), el juicio narrativo es del LLM app.
4. `imagery_zonal_stats(features_geojson, index="ndvi", date_from?, date_to?, scene_id?)`
   → `{scene, rows: [{feature_index, mean, min, max, std, px}], skipped: [...]}`
   — la app re-une por índice de feature para la coropleta.

**Límites v1**: AOI ≤ 2 500 km²; features zonal ≤ 5 000; timeout por tool 180 s;
respuesta PNG ≤ 1 MPx. Violación → error tipado honesto, jamás truncado en
silencio.

## 6. Cómo la app lo CONSUME

1. **Config** (`core/config.py`): `imagery_mcp_url` (default
   `http://imagery-mcp:9100/mcp`), `imagery_mcp_api_key` (SecretStr). Sin URL o
   sin health → la capacidad se declara NO disponible (fallo honesto del
   router: "no tengo servicio de imágenes conectado", análogo a F1.1
   sandbox_available). Entra al panel de Configuración de conexiones.
2. **Cliente** (`core/imagery_client.py`): `ImageryMCPClient` — initialize →
   tools/call sobre streamable HTTP con Bearer; timeouts; traduce errores MCP a
   excepciones tipadas. Los agentes NO conocen el protocolo.
3. **ReAct** (camino default hybrid): nueva tool `imagery_analysis(request,
   operation: ndvi|change|zonal|search)` en tool_schemas → dispatch en
   react_tools llama al cliente con el AOI = capa activa (o bbox del viewport
   si no hay capa, declarándolo). El resultado entra al canal analítico
   (`data`/`visualization`) + capa overlay.
4. **Camino cableado**: intent `imagery` en el router (reglas: NDVI, "índice de
   vegetación", "cambio entre fechas con satélite", "qué tan verde") → nodo
   `imagery_agent` liviano que llama al cliente. Mismo shape de salida.
5. **Salida al frontend**: overlay PNG como MapLibre `image` source con bounds
   (nuevo tipo de capa `imagery_overlay`) + tabla de stats por el canal
   analítico + coropleta para zonal (reusa symbology graduated sobre el campo
   unido `ndvi_mean`).
6. **HITL**: no bloquea v1 (datos públicos de solo lectura); el responder
   declara SIEMPRE escena, fecha y % nubes usados (transparencia).

## 7. Plan de implementación (tareas)

| # | Entregable | DoD |
|---|---|---|
| C3.1 | `services/imagery_mcp/` engine (providers PC+ES, retry por banda, selección por contención, NDVI/change/zonal, PNG) | unit tests con arrays sintéticos + providers mockeados; integración real marcada `-m integration` (escena Bogotá) |
| C3.2 | auth.py (keys+scopes+rate limit) + server.py (FastMCP + middleware) | tests 401/403/429/200; tools/list solo muestra tools permitidas por scope |
| C3.3 | Dockerfile + compose (`imagery-mcp`, egress allowlist) + README | contenedor build + health + tool call real desde la app network |
| C3.4 | App: config + `ImageryMCPClient` + capacidad declarada | client tests contra servidor real local; degradación honesta sin servicio |
| C3.5 | Agéntica: tool ReAct + intent imagery + nodo + responder/frontend overlay+coropleta | tests LLM real de routing; bench: categoría `imagery` (3 tareas); navegador: "NDVI de estos lotes" end-to-end |

## 8. Render en el mapa: TESELADO DINÁMICO (decisión 2026-07-19, validada)

Pregunta del revisor: ¿PNG overlay es lo correcto, o hay algo mejor (teselas)?
**Respuesta validada: teselado dinámico estilo TiTiler con `rio-tiler`**, con
el PNG pequeño como preview instantáneo.

Sonda en vivo (rio-tiler 9.4 sobre PC): tesela z13 256px NDVI = **114 s fría**
(cabeceras + pirámide del COG en esta red) pero **0.1 s la vecina** (caché VSI
de GDAL caliente). Diseño derivado:

- `imagery-mcp` expone además un endpoint HTTP plano
  `GET /tiles/{scene_id}/{z}/{x}/{y}.png?expression=ndvi&rescale=-1,1&colormap=rdylgn`
  (NO MCP — cacheable por CDN/navegador) renderizado con rio-tiler desde los
  COGs originales (band-math por tesela; los zooms bajos usan overviews).
- **Caché de lectores CALIENTES por escena** en el servicio (pool LRU de
  `Reader`s abiertos + caché de teselas ya renderizadas): la primera tesela
  paga el frío UNA vez; el resto ~0.1 s.
- Las tools `imagery_ndvi`/`imagery_change` devuelven AMBOS: `png_overlay`
  (preview inmediato con bounds) y `tiles {url_template, minzoom, maxzoom}`.
  El frontend pinta el overlay al instante y monta la capa de teselas
  (MapLibre raster source) para el zoom fino.
- Auth de teselas: el navegador NO recibe la API key — el nginx del frontend
  proxeaa `/imagery/tiles/*` → `imagery-mcp:9100` inyectando el header
  server-side (mismo patrón que /api).

**Después (v2, fuera de alcance v1):** mosaico multi-escena, máscara de nubes
SCL, más índices (NDWI/NBR/SAVI), ImageServer multiespectral oportunista
(renderingRule server-side), OAuth 2.1, publicación del catálogo de tools para
terceros (T33 la absorbe).
