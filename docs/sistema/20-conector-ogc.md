# 20 · Conector OGC (WMS, WFS, OGC API) — `ogc-mcp`

La información geográfica oficial de Colombia se publica sobre todo como **servicios OGC**: el IGAC, Parques
Nacionales, el Servicio Geológico Colombiano, el DANE y la UPRA exponen WMS (imágenes) y WFS (elementos).
`ogc-mcp` (`services/ogc_mcp/`, puerto 9800) los pone al alcance del agente con un **catálogo curado** y
sirve también para cualquier otro servicio OGC por su URL.

## Herramientas

| Herramienta | Qué hace | Sale |
|---|---|---|
| `ogc_catalogo(texto?, entidad?)` | Los servicios curados (entidad, temas, descripción, URL por protocolo). Con `texto`, los que contienen alguna palabra, primero los que más. | hechos |
| `ogc_describir(url, tipo?, filtro?)` | GetCapabilities (WMS/WFS) o `/collections` (OGC API): capas con nombre, título, resumen, extensión EPSG:4326, estilos, si el WFS da GeoJSON. | hechos |
| `ogc_ver_mapa(url, capa, estilo?)` | Una capa WMS en el mapa como **teselas** que firma y sirve el propio servidor (`/wms/<ficha>/{z}/{x}/{y}.png`, por el proxy del núcleo). Acepta el nombre o el título exacto y único. | `raster_tiles` |
| `ogc_traer_elementos(url, capa, tipo?, bbox?, area?, max_elementos?)` | Elementos de una capa WFS / OGC API en EPSG:4326 con sus atributos. `area` = una capa de la sesión: solo los que la **intersecan** (no su rectángulo). | `feature_collection` o `feature_ref` |

Los hechos dicen siempre cuántos hay en el servicio (con y sin filtro) y si vino todo. Con filtro, el
«completo» es del recorte (`completa_dentro_del_filtro`), no de la capa.

## Catálogo

`services/ogc_mcp/ogc_mcp/catalogo.yaml`: 33 servicios verificados en vivo (fecha en `verificado`).
`OGC_CATALOGO` apunta a otro archivo (una organización con sus servicios). El servidor no elige: el LLM
decide con los hechos.

## Comportamientos medidos (2026-10-15)

- WFS 2.0: el `bbox` con `urn:ogc:def:crs:EPSG::4326` va en **lat/lon** (al revés devuelve 0); el GeoJSON
  sale en lon/lat con `srsName=EPSG:4326`; CRS84 no lo aceptan ni PNN ni IGAC.
- ArcGIS Server (SGC) no declara EPSG:3857 en el WMS pero lo atiende: se comprueba con una tesela antes
  de aceptarlo (`crs_teselas` lo dice).
- `mapas.igac.gov.co` tarda hasta ~60 s en un GetCapabilities y corta conexiones: caché de 15 min y un
  reintento ante fallo de red.
- Capas pesadas (RUNAP entero ≈ 110 MB): las páginas se piden en mitades si pasan de 40 MB y se para a
  los 80 MB con aviso (`corte_por_peso`); lo que vino se dice incompleto.

Verdades contra consultas directas (`tests/test_ogc_mcp_vivo.py`, `pytest -m integration`): RUNAP en
[-74.5, 4, -73.5, 5] = 169; manzanas IGAC en Luruaco = 151 (Bogotá = 0: catastro propio, no del IGAC);
DIVIPOLA 2025 = 33 departamentos (32 + Bogotá D.C.).

## Seguridad

- Las URLs vienen de fuera: cliente SSRF del kit (`geo_mcp_kit.red`, IP fijada, sin redirecciones a otro
  host, sin IPs internas); `OGC_ALLOWED_DOMAINS` restringe a dominios.
- XML con `defusedxml` (sin entidades externas).
- La ruta de teselas solo pide lo que una herramienta validó: la ficha va firmada con HMAC
  (`OGC_MCP_TILE_SECRET` o, sin ella, derivada de `OGC_MCP_KEYS`).

## Probar

- Unidad: `pytest tests/test_ogc_mcp.py` (servicios simulados).
- En vivo: `pytest -m integration tests/test_ogc_mcp_vivo.py` (red; lento).
- V5: `e2e-integration/ogc-mcp.spec.ts` (stack con ogc-mcp, LLM real).
