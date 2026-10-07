"""Servidor MCP de imagery — FastMCP (streamable HTTP, stateless) + auth ASGI.

Capas:
1. `/health` — abierto (para healthchecks de compose y del cliente app).
2. `/mcp`   — protegido: Bearer válido (401) → rate limit (429) → si el
   mensaje es `tools/call`, scope de la tool (403, fail-closed). La inspección
   se hace en el middleware leyendo el body JSON-RPC y re-inyectándolo.

Las tools son funciones SÍNCRONAS (el motor es IO bloqueante de GDAL/httpx);
FastMCP las despacha en thread pool. ``stateless_http=True``: sin manejo de
sesión — correcto para clientes API-key (la app y terceros).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from geo_mcp_kit import (
    GeoMcpAuth,
    ToolRunner,
    geo_meta,
    jsonrpc_tool_names,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from imagery_mcp import georesult as gr
from imagery_mcp import vista
from imagery_mcp.aoi import aoi_bbox, bbox_area_km2, rango_fechas
from imagery_mcp.auth import KeyRing, RateLimiter
from imagery_mcp.catalogo import ORDENES, Catalogo, CatalogoError, ConCatalogo
from imagery_mcp.config import Settings
from imagery_mcp.engine import (
    ImageryError,
    run_change,
    run_composite,
    run_ndvi,
    run_search,
    run_zonal,
)
from imagery_mcp.providers import ColeccionNoDisponible, build_provider
from imagery_mcp.rutas import (  # noqa: F401 — los tests los importan de aquí
    _parse_indice,
    _parse_rescale,
    rutas_de_teselas,
)
from imagery_mcp.tiles import TilePool

logger = logging.getLogger("imagery_mcp")

# Tope del cuerpo /mcp: los tools legítimos son JSON pequeños (AOI + fechas);
# 2 MB deja margen para un FeatureCollection zonal y corta abusos (#18).
_MAX_BODY_BYTES = 2 * 1024 * 1024

settings = Settings.from_env()

#: Tipos de geometría que son un ÁREA (menú contextual: el NDVI de una zona, la estadística
#: zonal de unos polígonos; en un punto no tienen sentido, salvo leer el píxel del NDVI).
AREAS = ["Polygon", "MultiPolygon"]
# El proveedor STAC + las escenas del catálogo GeoParquet (ids de Collection 1): una escena
# elegida en el explorador sirve igual a las tools de cálculo y a las teselas.
catalogo = Catalogo()
provider = ConCatalogo(build_provider(settings.provider), catalogo)
keyring = KeyRing(settings.parsed_keys())
limiter = RateLimiter()
# Teselado dinámico (spec §8): pool de Readers calientes + caché de PNGs.
tile_pool = TilePool(provider)

mcp = FastMCP(
    "geo-imagery",
    instructions=(
        "Análisis de imagery satelital Sentinel-2 L2A sobre un AOI GeoJSON: "
        "búsqueda de escenas, NDVI, cambio entre fechas, estadística zonal por "
        "feature e imagen en color (composición RGB: color natural / falso color "
        "/ agricultura / SWIR). Datos: STAC → COG con lectura ventaneada (solo se "
        "transfiere la zona pedida). Las respuestas declaran la escena usada, su "
        "fecha y % de nubes; las capas visuales se sirven como teselas XYZ (ver "
        "el bloque `tiles`: url_template relativa al host del servicio, con el "
        "MISMO Bearer y HTTP plano)."
    ),
    stateless_http=True,
    # Respuestas JSON planas (no SSE) para peticiones single: permite clientes
    # mínimos por httpx (la app) sin SDK; los clientes MCP completos también
    # las aceptan por spec.
    json_response=True,
    # La protección DNS-rebinding de FastMCP solo acepta Host localhost — en
    # compose el Host es `imagery-mcp:9100` y devolvía 421 (lo cazó el E2E
    # containerizado). Aquí el gate real es el Bearer del AuthMiddleware
    # (ningún request llega sin clave), y el servicio no se expone al browser
    # (las teselas van por el proxy de la app), así que se desactiva.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# S0.6: tiempo máximo por tool (ejecución y errores honestos: geo_mcp_kit).
_RUNNER = ToolRunner(
    timeout_s=settings.limits.tool_timeout_s, service="imagery", expected_errors=(ImageryError, ColeccionNoDisponible, CatalogoError),
)


def _wrap(fn, *args, timeout_s: float | None = None, **kwargs) -> dict:
    """Ejecuta una operación del motor con errores tipados y tiempo máximo."""
    limit = settings.limits.tool_timeout_s if timeout_s is None else timeout_s
    return _RUNNER.run(fn, *args, timeout_s=limit, **kwargs)


@mcp.tool(
    meta=geo_meta(inputs={"aoi_geojson": ["geometry", "layer_ref"]}, outputs=["table"], cost="medium"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_search_scenes(
    aoi_geojson: dict,
    date_from: str | None = None,
    date_to: str | None = None,
    max_cloud_pct: float | None = None,
    limit: int = 10,
    collection: Literal["sentinel-2-l2a", "landsat-c2-l2"] = "sentinel-2-l2a",
) -> dict[str, Any]:
    """Busca escenas que cubren el AOI en la colección pedida: Sentinel-2 L2A (10 m, cada
    ~5 días) o Landsat 8/9 C2 L2 (30 m, cada ~8 días, archivo desde 2013). Fechas ISO
    YYYY-MM-DD (default: últimos 45 días, nubes < 20%). Devuelve una tabla con id, fecha,
    % nubes y si la escena contiene el AOI completo; útil para elegir `scene_id` o fechas
    antes de un análisis."""
    return gr.search(_wrap(run_search, provider, aoi_geojson, date_from, date_to,
                           max_cloud_pct, min(int(limit), 20), settings.limits, collection=collection))


@mcp.tool(
    meta=geo_meta(inputs={"aoi_geojson": ["geometry", "layer_ref"]}, geometry_types={"aoi_geojson": [*AREAS, "Point"]}, outputs=["raster_tiles", "stats"], cost="high"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_ndvi(
    aoi_geojson: dict,
    date_from: str | None = None,
    date_to: str | None = None,
    scene_id: str | None = None,
    max_cloud_pct: float | None = None,
    index: Literal["ndvi", "ndwi", "ndbi", "ndmi"] = "ndvi",
    collection: Literal["sentinel-2-l2a", "landsat-c2-l2"] = "sentinel-2-l2a",
) -> dict[str, Any]:
    """Índice espectral de una zona: NDVI (vegetación, por defecto), NDWI (agua), NDBI
    (construido/suelo desnudo) o NDMI (humedad de la vegetación), con imágenes Sentinel-2
    (10 m, por defecto) o Landsat 8/9 (30 m; archivo desde 2013). Devuelve la capa (teselas,
    stretch p2–p98) y sus estadísticas (media/min/max/percentiles). Elige la escena con menos
    nubes que contenga el AOI (o la de mejor cobertura, avisado en `degraded`). Hechos para
    interpretar el número: `index` (qué mide y cómo leerlo), `collection`, `scene` (fecha,
    % nubes), `cloud_mask` (píxeles enmascarados), `reflectance` (factor de escala y de dónde
    salió), `descartes` (píxeles sin índice definido) y `alternatives` (otras fechas)."""
    result = _wrap(run_ndvi, provider, aoi_geojson, date_from, date_to,
                   scene_id, settings.limits, on_scene=tile_pool.register_scene,
                   max_cloud_pct=max_cloud_pct, index=index, collection=collection)
    # Pre-calentamiento (fire-and-forget): teselas del AOI listas al mirar el mapa.
    tiles = result.get("tiles") or {}
    if tiles.get("bounds") and result.get("scene"):
        rs = tiles.get("rescale") or [-1.0, 1.0]
        tile_pool.prewarm(result["scene"]["id"], tiles["bounds"],
                          rescale=(rs[0], rs[1]), index=index, collection=collection)
    return gr.ndvi(result)


@mcp.tool(
    meta=geo_meta(inputs={"aoi_geojson": ["geometry", "layer_ref"]}, geometry_types={"aoi_geojson": AREAS}, outputs=["raster_tiles", "stats"], cost="high"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_change(
    aoi_geojson: dict,
    date_a: str,
    date_b: str,
    window_days: int = 15,
    max_cloud_pct: float | None = None,
) -> dict[str, Any]:
    """Cambio de NDVI entre dos fechas ISO YYYY-MM-DD (busca la mejor escena a
    ±window_days de cada una). Devuelve la capa de cambio (rojo = pérdida,
    verde = ganancia), estadísticas de la diferencia y hechos interpretables:
    % de píxeles con pérdida/ganancia fuerte (|Δ|>0.1), escenas usadas,
    reflectancia y descartes por escena."""
    return gr.change(_wrap(run_change, provider, aoi_geojson, date_a, date_b,
                           int(window_days), settings.limits, max_cloud_pct=max_cloud_pct,
                           on_scene=tile_pool.register_scene))


@mcp.tool(
    meta=geo_meta(inputs={"features_geojson": ["geometry", "layer_ref"]}, geometry_types={"features_geojson": AREAS}, outputs=["feature_collection"], cost="high"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_zonal_stats(
    features_geojson: dict,
    date_from: str | None = None,
    date_to: str | None = None,
    scene_id: str | None = None,
    max_cloud_pct: float | None = None,
    index: Literal["ndvi", "ndwi", "ndbi", "ndmi"] = "ndvi",
    collection: Literal["sentinel-2-l2a", "landsat-c2-l2"] = "sentinel-2-l2a",
) -> dict[str, Any]:
    """Índice espectral POR FEATURE (por lote, polígono, predio, área dibujada…) de una capa:
    NDVI (vegetación, por defecto), NDWI (agua), NDBI (construido/suelo desnudo) o NDMI
    (humedad de la vegetación); con Sentinel-2 (10 m, por defecto) o Landsat 8/9 (30 m). Devuelve
    la misma capa enriquecida con `<índice>_mean`, `_std`, `_min` y `_max` en cada feature, lista
    para colorear; los features sin píxeles válidos quedan en `skipped`."""
    return gr.zonal(_wrap(run_zonal, provider, features_geojson, date_from, date_to,
                          scene_id, settings.limits, on_scene=tile_pool.register_scene,
                          max_cloud_pct=max_cloud_pct, index=index, collection=collection),
                    features_geojson)


@mcp.tool(
    meta=geo_meta(inputs={"aoi_geojson": ["geometry", "layer_ref"]}, geometry_types={"aoi_geojson": AREAS}, outputs=["raster_tiles"], cost="high"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_composite(
    aoi_geojson: dict,
    combo: Literal["true_color", "false_color", "agriculture", "swir"] = "true_color",
    date_from: str | None = None,
    date_to: str | None = None,
    scene_id: str | None = None,
    max_cloud_pct: float | None = None,
) -> dict[str, Any]:
    """VER la imagen satelital en color de una zona (no es un índice). `combo`:
    'true_color' (color natural, como el ojo humano), 'false_color' (infrarrojo;
    vegetación en rojo), 'agriculture' (cultivos/suelo), 'swir' (urbano/SWIR).
    Devuelve la capa de teselas RGB y la escena usada (fecha, % nubes)."""
    return gr.composite(_wrap(run_composite, provider, aoi_geojson, combo, date_from, date_to,
                              scene_id, settings.limits, on_scene=tile_pool.register_scene,
                              max_cloud_pct=max_cloud_pct))


#: Cotas del catálogo: un área del tamaño de un país (Colombia: ~1,9 M km² de bbox) y un año
#: de ventana; más que eso abre muchos archivos y tarda minutos.
_CATALOGO_MAX_KM2 = 5_000_000.0
_CATALOGO_MAX_DIAS = 366


def _ventana_catalogo(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    from datetime import date

    d0, d1 = rango_fechas(date_from, date_to, settings.limits)
    if (date.fromisoformat(d1) - date.fromisoformat(d0)).days > _CATALOGO_MAX_DIAS:
        raise CatalogoError(f"la ventana {d0}…{d1} pasa de {_CATALOGO_MAX_DIAS} días: pide un año o menos")
    return d0, d1


def _bbox_catalogo(aoi_geojson: dict) -> tuple[float, float, float, float]:
    bbox = aoi_bbox(aoi_geojson)
    if bbox_area_km2(bbox) > _CATALOGO_MAX_KM2:
        raise CatalogoError("el área pasa del tamaño de un país; acota la zona")
    return bbox


def _cuadricula(aoi_geojson, date_from, date_to, max_cloud_pct, min_coverage_pct) -> dict:
    d0, d1 = _ventana_catalogo(date_from, date_to)
    filas = catalogo.cuadricula(_bbox_catalogo(aoi_geojson), d0, d1, max_cloud_pct, min_coverage_pct)
    return {"filas": filas, "desde": d0, "hasta": d1}


def _escenas(tile, aoi_geojson, date_from, date_to, max_cloud_pct, min_coverage_pct, order, limit) -> dict:
    d0, d1 = _ventana_catalogo(date_from, date_to)
    bbox = _bbox_catalogo(aoi_geojson) if aoi_geojson else None
    filas = catalogo.escenas(d0, d1, tile=tile, bbox=bbox, max_nubes=max_cloud_pct,
                             min_cobertura=min_coverage_pct, orden=order, limite=limit)
    return {"filas": filas, "desde": d0, "hasta": d1}


@mcp.tool(
    meta=geo_meta(inputs={"aoi_geojson": ["geometry", "layer_ref"]}, geometry_types={"aoi_geojson": AREAS}, outputs=["feature_collection"], cost="medium"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_catalog_grid(
    aoi_geojson: dict,
    date_from: str | None = None,
    date_to: str | None = None,
    max_cloud_pct: float | None = None,
    min_coverage_pct: float | None = None,
) -> dict[str, Any]:
    """Disponibilidad de imágenes Sentinel-2 en un área (hasta el tamaño de un país) y una
    ventana de fechas (hasta un año), por tesela MGRS de 110 km: cuántas escenas hay, la más
    despejada (`mejor_escena`, `mejor_fecha`), nubes mínima y mediana (%) y cobertura máxima
    (%). Devuelve las teselas como capa para colorear. Sirve para decidir DÓNDE y CUÁNDO hay
    imagen útil antes de pedir una; `mejor_escena` se puede pasar como `scene_id` a
    imagery_composite, imagery_ndvi o imagery_zonal_stats."""
    return gr.cuadricula(_wrap(_cuadricula, aoi_geojson, date_from, date_to, max_cloud_pct, min_coverage_pct))


@mcp.tool(
    meta=geo_meta(inputs={"aoi_geojson": ["geometry", "layer_ref"]}, outputs=["table"], cost="low"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_catalog_scenes(
    tile: str | None = None,
    aoi_geojson: dict | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    max_cloud_pct: float | None = None,
    min_coverage_pct: float | None = None,
    order: Literal["menos_nubes", "mas_cobertura", "reciente"] = "menos_nubes",
    limit: int = 50,
) -> dict[str, Any]:
    """Escenas Sentinel-2 de una tesela MGRS (`tile`, p. ej. '18NWL', de imagery_catalog_grid)
    o de un área, con fecha, nubes (%), cobertura de la tesela (%) y miniatura (URL de una
    vista previa JPG), ordenadas por menos nubes, más cobertura o más reciente (hasta 200).
    Cada `id` se puede pasar como `scene_id` a imagery_composite, imagery_ndvi o
    imagery_zonal_stats para verla o medirla."""
    if order not in ORDENES:
        order = "menos_nubes"
    return gr.escenas_catalogo(_wrap(_escenas, tile, aoi_geojson, date_from, date_to,
                                     max_cloud_pct, min_coverage_pct, order, limit))


@mcp.tool(
    meta=geo_meta(inputs={}, outputs=["feature_collection"], cost="medium"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_catalog_world(
    date_from: str | None = None,
    date_to: str | None = None,
    max_cloud_pct: float | None = None,
    min_coverage_pct: float | None = None,
    min_scenes: int | None = None,
) -> dict[str, Any]:
    """Disponibilidad de Sentinel-2 en TODO EL MUNDO por tesela MGRS (~29.000 teselas), por
    meses completos (los que toca la ventana; hasta un año): escenas, nubes mínima, mediana de
    las medianas mensuales y cobertura máxima (%). Para ver dónde hay imagen despejada a escala
    de país o continente; una tesela concreta se detalla con imagery_catalog_scenes."""
    return gr.mundo(_wrap(_mundo, date_from, date_to, max_cloud_pct, min_coverage_pct, min_scenes))


def _mundo(date_from, date_to, max_cloud_pct, min_coverage_pct, min_scenes) -> dict:
    d0, d1 = _ventana_catalogo(date_from, date_to)
    r = catalogo.mundo(d0, d1, max_cloud_pct, min_coverage_pct, min_scenes)
    return {**r, "desde": d0, "hasta": d1}


@mcp.tool(
    meta=geo_meta(inputs={}, outputs=["raster_tiles"], cost="low"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_scene_view(
    scene_id: str,
    product: str = "true_color",
    stretch: list[list[float]] | None = None,
    rescale: list[float] | None = None,
) -> dict[str, Any]:
    """VER una escena Sentinel-2 ENTERA (~110 km) por su `scene_id` (de imagery_catalog_scenes o
    imagery_search_scenes), sin medir nada. `product`: color 'true_color', 'false_color',
    'agriculture', 'swir'; índice 'ndvi', 'ndwi' (agua), 'ndmi', 'ndbi'; una banda suelta
    ('coastal', 'blue', 'green', 'red', 'rededge1'…'rededge3', 'nir', 'nir08', 'nir09',
    'swir16', 'swir22', 'aot', 'wvp'); 'scl' (clasificación de la escena: nube, sombra, agua,
    vegetación…) o 'cloud'/'snow' (probabilidad %). `stretch`: contraste de un color, tres
    pares [mín, máx] de reflectancia (R, G, B); `rescale`: [mín, máx] de un índice o banda.
    Devuelve la capa y los enlaces para DESCARGAR cada banda (COG)."""
    return gr.vista(_wrap(vista.ver_escena, provider, scene_id, product, estiramiento=stretch,
                          rescale=rescale, on_scene=tile_pool.register_scene))


@mcp.tool(
    meta=geo_meta(inputs={}, outputs=["stats"], cost="medium"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_band_histogram(scene_id: str, bands: list[str]) -> dict[str, Any]:
    """Histograma y percentiles 2/98 de hasta 6 bandas de una escena entera (reflectancia; la
    'scl' da el % de cada clase: nubes, sombra, agua, vegetación…). Sirve para elegir el
    contraste (`stretch`/`rescale` de imagery_scene_view) o para saber cuánto de la escena está
    cubierto de nubes."""
    return gr.histograma(_wrap(vista.histograma, provider, scene_id, bands))


@mcp.tool(
    meta=geo_meta(inputs={"point_geojson": ["geometry"]}, geometry_types={"point_geojson": ["Point"]},
                  outputs=["table"], cost="low"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    structured_output=True,
)
def imagery_pixel(scene_id: str, point_geojson: dict) -> dict[str, Any]:
    """El valor de CADA banda de una escena en un punto (GeoJSON Point): reflectancia de B01…B12,
    AOT, vapor de agua, la clase SCL y la probabilidad de nubes/nieve, más los índices que salen
    de ellas (NDVI, NDWI, NDMI, NDBI). Para saber qué hay en un píxel concreto."""
    g = (point_geojson or {}).get("geometry", point_geojson) or {}
    if g.get("type") != "Point":
        return {"error": "point_geojson debe ser un GeoJSON Point"}
    lon, lat = (float(v) for v in g["coordinates"][:2])
    return gr.pixel(_wrap(vista.pixel, provider, scene_id, lon, lat))


# ---------------------------------------------------------------------------
# ASGI: auth del kit + rutas de teselas propias
# ---------------------------------------------------------------------------
_jsonrpc_tool_names = jsonrpc_tool_names  # compat con tests existentes


def AuthMiddleware(app, keyring: KeyRing, limiter: RateLimiter,
                   tile_pool: TilePool | None = None) -> GeoMcpAuth:
    """401 sin clave · 429 sobre el límite · 403 sin scope (fail-closed) · teselas."""
    return GeoMcpAuth(
        app, keyring, limiter, service="imagery-mcp",
        routes=rutas_de_teselas(tile_pool) if tile_pool is not None else (),
        metrics=(lambda: dict(tile_pool.metrics)) if tile_pool is not None else None,
        max_body_bytes=_MAX_BODY_BYTES,
    )


def build_app():
    """ASGI app final: FastMCP streamable HTTP envuelto por la autenticación."""
    if len(keyring) == 0:
        raise RuntimeError(
            "imagery-mcp no arranca SIN claves (IMAGERY_MCP_KEYS vacío) — "
            "un MCP con datos y cómputo no se expone abierto."
        )
    return AuthMiddleware(mcp.streamable_http_app(), keyring, limiter,
                          tile_pool=tile_pool)


def main() -> None:  # pragma: no cover — entrypoint
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    logger.info(
        f"imagery-mcp: provider={settings.provider}, claves={len(keyring)}, "
        f"puerto={settings.port}"
    )
    from datetime import UTC, datetime

    catalogo.precalentar([datetime.now(UTC).year])   # la 1.ª consulta del explorador no paga los pies
    uvicorn.run(build_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
