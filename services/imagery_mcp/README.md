# imagery-mcp

Servicio **MCP independiente** de análisis de imagery satelital (C3-v1 del
roadmap GEO_COPILOT). Spec completo: `docs/SPEC_C3_IMAGERY_MCP_2026-07-19.md`.

- **Fuente**: STAC → COG. Colección por petición (T5.5): **Sentinel-2 L2A** (10 m, por
  defecto) o **Landsat 8/9 C2 L2** (30 m, archivo desde 2013; solo Planetary Computer: en
  Earth Search es de pago por el solicitante). Proveedor default **Planetary Computer**
  (Azure, ~20× más rápido desde la red del despliegue); `earth-search` como alternativa
  (`IMAGERY_PROVIDER`). La firma SAS de PC va por cuenta/contenedor de almacenamiento.
- **Índices** (T5.5): NDVI (vegetación), NDWI (agua), NDBI (construido) y NDMI (humedad), todos
  `(a − b) / (a + b)` con el MISMO cálculo, factor de reflectancia y máscara de nubes (SCL en
  Sentinel-2; bits 1–4 de `qa_pixel` en Landsat). Si las bandas tienen resolución distinta
  (SWIR 20 m en Sentinel-2) la segunda se reamostrea a la grilla de la primera.
- **Lectura ventaneada**: para "NDVI de estos lotes" solo se transfiere la
  ventana del AOI (KBs–MBs), con retry a nivel de banda (los truncados HTTP
  no los reintenta GDAL).
- **Transporte**: MCP streamable HTTP (stateless) en `/mcp` + `/health` abierto.
- **Auth**: Bearer API keys con scopes (`imagery:read`, `imagery:compute`) y
  rate limit por clave. **No arranca sin claves.**

## Tools

| Tool | Scope | Qué devuelve |
|---|---|---|
| `imagery_search_scenes` | read | escenas con fecha/% nubes/contención del AOI (`collection`) |
| `imagery_ndvi` | compute | índice (`index`: ndvi/ndwi/ndbi/ndmi; `collection`) — stats + bloque `tiles` + escena + qué mide |
| `imagery_change` | compute | Δ NDVI entre 2 fechas + hechos (% píxeles ±0.1) + `tiles` |
| `imagery_zonal_stats` | compute | índice por feature (`<índice>_mean`…; `index`, `collection`) |
| `imagery_composite` | compute | imagen en color RGB (`combo`: true_color / false_color / agriculture / swir) + `tiles` |

## Teselas (contrato de la capa visual)

Las tools que producen una capa visual (`ndvi`, `change`, `composite`) devuelven un
bloque `tiles` con una `url_template` **relativa al host del servicio** (p.ej.
`/tiles/{scene}/{z}/{x}/{y}.png?rescale=lo,hi`). El consumidor la sirve así:

- **Endpoints** (GET, PNG, cacheable): `/tiles/{scene}/{z}/{x}/{y}.png` (índice; `&index=` y
  `&collection=` si no es NDVI de Sentinel-2),
  `/tiles-diff/{a}/{b}/{z}/{x}/{y}.png` (cambio), `/tiles-rgb/{scene}/{combo}/{z}/{x}/{y}.png` (RGB).
- **Auth**: el MISMO Bearer que las tools (scope `imagery:compute`) — no es un endpoint abierto.
- **Transporte**: HTTP plano (no MCP). El cliente prepende el host del servicio y la cabecera `Authorization`.
- Además: `/health` (abierto, liveness) y `/metrics` (Bearer, contadores del pool).

## Configuración (env)

```bash
IMAGERY_PROVIDER=planetary-computer     # | earth-search
IMAGERY_HOST=0.0.0.0
IMAGERY_PORT=9100
IMAGERY_TILE_CACHE_DIR=/tmp/ndvi-tiles  # caché de teselas en disco (LRU acotada)
IMAGERY_MCP_KEYS='[{"name":"geo-copilot-app","key":"<secreto>","scopes":["imagery:read","imagery:compute"],"rate_limit_per_min":60}]'
```

Claves adicionales (p.ej. Claude Desktop, agentes QGIS) = entradas extra en el
JSON con sus scopes — mismo servidor, credenciales separadas.

## Correr

```bash
# local (dev)
pip install -r requirements.txt
IMAGERY_MCP_KEYS='[...]' python -m imagery_mcp.server

# docker (compose lo levanta como servicio `imagery-mcp`)
docker compose -f docker/docker-compose.yml up -d imagery-mcp
```

## Tests

Unit (offline) + auth + middleware viven en `tests/test_imagery_mcp.py` del
repo raíz; la integración real (busca escena de Bogotá y computa NDVI) corre
con `pytest -m integration tests/test_imagery_mcp.py`.
