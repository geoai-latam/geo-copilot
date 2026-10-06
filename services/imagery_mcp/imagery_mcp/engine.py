"""Motor de análisis: lectura ventaneada de COGs + NDVI/cambio/zonal.

Decisiones ancladas en las sondas en vivo (2026-07-19):
- Retry a NIVEL DE BANDA (reabrir+releer): GDAL no reintenta cuerpos HTTP
  truncados con status 200 — se vieron truncados intermitentes reales.
- Selección de escena por CONTENCIÓN del AOI (los tiles MGRS pueden dejar el
  AOI fuera aunque la escena "intersecte"). Sin escena contenedora → se usa la
  de mejor cobertura y se DECLARA (`degraded` + coverage_pct), sin mosaicar (v2).
- Todo aquí son HECHOS (números, máscaras, percentiles). El juicio narrativo
  es del LLM de la app consumidora.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

# F4: la radiometría, el AOI, las estadísticas y las operaciones viven en sus módulos; se reexportan
# porque el servidor, las teselas y las pruebas los importan desde `engine`.
from imagery_mcp.aoi import (  # noqa: F401
    aoi_bbox,
    bbox_area_km2,
    bbox_polygon,
    check_aoi,
    default_date_range,
    rango_fechas,
)
from imagery_mcp.config import Limits
from imagery_mcp.estadisticas import (  # noqa: F401
    _QA_NUBE_BITS,
    _SCL_CLOUD_CLASSES,
    _limites,
    _valores_de_geometria,
    array_stats,
    compute_ndvi,
    compute_ndvi_detallado,
    mascara_de_nubes,
    zonal_stats,
)
from imagery_mcp.operaciones import (  # noqa: F401
    _COMPOSITE_COMBOS,
    _con_coleccion,
    _hecho_coleccion,
    _que_se_busco,
    run_change,
    run_composite,
    run_ndvi,
    run_search,
    run_zonal,
)

# `baseline_value` se reexporta: el umbral tiene una sola definición (providers).
from imagery_mcp.providers import (
    BandScaling,
    Scene,
    StacProvider,
    baseline_value,  # noqa: F401
)
from imagery_mcp.radiometria import (  # noqa: F401
    _BOA_OFFSET_BASELINE,
    _FUENTE_TEXTO,
    _IDENTITY_SCALING,
    _MASCARA_TEXTO,
    INDICE_DEFECTO,
    INDICES,
    _origen,
    apply_scaling,
    check_unidades_coherentes,
    descartes_payload,
    indice,
    mensaje_incompatibles,
    motivo_sin_validos,
    radiometry,
    reflectance_payload,
    scene_scaling,
)

# Entorno GDAL para lecturas remotas (validado en las sondas).
_GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_CONNECTTIMEOUT": "20",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
}


class ImageryError(ValueError):
    """Error tipado y honesto del motor (límites, sin escenas, banda irrecuperable)."""


@dataclass
class BandWindow:
    data: np.ndarray            # float32
    transform: Any              # affine del RECORTE
    crs: Any
    bounds4326: tuple[float, float, float, float]
    # % de píxeles enmascarados por nubes/sombras (SCL); None = sin máscara.
    cloud_pct_masked: float | None = None
    # Píxeles descartados por reflectancia no positiva (agua/sombra tras aplicar
    # el BOA_ADD_OFFSET). Se declara aparte del nodata y de la nube.
    px_no_positivos: int = 0


# ---------------------------------------------------------------------------
# Reflectancia: DN → BOA (auditoría 2026-09-08, §1.3)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Geometría / límites (hechos, aproximaciones declaradas)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Selección de escena
# ---------------------------------------------------------------------------
def select_scene(
    scenes: list[Scene], bbox: tuple[float, float, float, float], *, busqueda: str = "Sentinel-2"
) -> tuple[Scene, bool]:
    """La escena con MENOS nubes que CONTIENE el AOI; si ninguna lo contiene,
    la de mejor cobertura (declarado por el caller como degradación).

    `busqueda`: QUÉ se buscó (colección, fechas, nubes), para que el error lo diga: antes
    decía «Sentinel-2» aunque se hubiera buscado en Landsat (V5 T5.5)."""
    if not scenes:
        raise ImageryError(
            f"No hay escenas {busqueda} que cumplan los filtros (zona/fechas/nubes). "
            "Amplía el rango de fechas o el umbral de nubes."
        )
    containing = [s for s in scenes if s.contains(bbox)]
    if containing:
        return min(containing, key=lambda s: s.cloud_pct), True
    return max(scenes, key=lambda s: s.coverage_pct(bbox)), False


def _filter_requested_scene(provider, scenes: list[Scene], scene_id: str,
                            collection: str | None = None) -> list[Scene]:
    """Restringe `scenes` a la escena pedida por id, sin degradar en silencio.

    Si el id no aparece en el search actual (p.ej. la escena quedó fuera de la
    ventana de fechas o del umbral de nubes), se resuelve por ID contra el STAC;
    si tampoco existe ahí, error tipado. NUNCA se sustituye por otra fecha — el
    `... or scenes` anterior devolvía otra escena y mentía sobre qué se analizó
    (auditoría M1/#35)."""
    matched = [s for s in scenes if s.id == scene_id]
    if matched:
        return matched
    one = provider.get_scene(scene_id, **_con_coleccion(collection)) if hasattr(provider, "get_scene") else None
    if one is None:
        raise ImageryError(
            "la escena solicitada no está disponible con los filtros actuales"
        )
    return [one]


# ---------------------------------------------------------------------------
# Lectura ventaneada con retry por banda
# ---------------------------------------------------------------------------
def _is_auth_error(exc: Exception) -> bool:
    """¿La excepción huele a 401/403 del object store (SAS vencido/robado)?"""
    msg = str(exc)
    return any(t in msg for t in (
        "403", "401", "AuthenticationFailed", "SignatureDoesNotMatch",
        "AccessDenied", "Access Denied", "authentication",
    ))


def ventana_minima(win):
    """Ventana de lectura de al menos UN píxel en cada eje.

    Un punto (o una línea recta horizontal/vertical) tiene bbox de área cero: al
    redondear al píxel la ventana salía 0×0 y se reportaba «fuera de la escena»
    aunque el punto estuviera dentro (V5 F4, «¿qué valor tiene el NDVI aquí?»).
    Se lee el píxel que lo contiene."""

    from rasterio.windows import Window

    col0, row0 = math.floor(win.col_off), math.floor(win.row_off)
    col1 = max(col0 + 1, math.ceil(win.col_off + win.width))
    row1 = max(row0 + 1, math.ceil(win.row_off + win.height))
    return Window(col0, row0, col1 - col0, row1 - row0)


def _intersecta(win, ancho: int, alto: int) -> bool:
    """¿La ventana toca el raster (ancho×alto píxeles)?"""
    return (win.col_off < ancho and win.row_off < alto
            and win.col_off + win.width > 0 and win.row_off + win.height > 0)


def read_band_window(
    href_signed: str,
    bbox4326: tuple[float, float, float, float],
    *,
    retries: int = 3,
    resign=None,
    scaling: BandScaling | None = None,
) -> BandWindow:
    """Lee la ventana del AOI de una banda COG con retry.

    `resign` (opcional): callable sin args que devuelve un href RE-FIRMADO. Ante
    un 401/403 (SAS expirado) se invoca antes del reintento para no reintentar
    con la misma credencial vencida (M4).

    `scaling` (opcional): factor de reflectancia del asset. Se aplica AQUÍ, no
    más arriba, porque este es el único punto por el que pasa la banda antes de
    ``compute_ndvi`` — dejarlo al caller es lo que produjo el sesgo del §1.3."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    from rasterio.windows import transform as window_transform

    href = href_signed
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with rasterio.Env(**_GDAL_ENV), rasterio.open(href) as src:
                wb = transform_bounds("EPSG:4326", src.crs, *bbox4326)
                win = ventana_minima(from_bounds(*wb, src.transform))
                if not _intersecta(win, src.width, src.height):
                    raise ImageryError(
                        "La ventana del AOI queda fuera de la escena (tile MGRS)"
                    )
                data = src.read(1, window=win).astype("float32")
                # DN → reflectancia ANTES de que nadie calcule un índice (§1.3).
                data = apply_scaling(data, scaling)
                return BandWindow(
                    data=data,
                    transform=window_transform(win, src.transform),
                    crs=src.crs,
                    bounds4326=bbox4326,
                )
        except ImageryError:
            raise
        except Exception as exc:  # noqa: BLE001 — truncados/red: reintentar
            last_exc = exc
            # 401/403 → el SAS probablemente expiró: re-firmar antes de reintentar
            # (si no, se reintenta con la misma credencial vencida y da 502).
            if resign is not None and _is_auth_error(exc):
                try:
                    href = resign()
                except Exception:  # noqa: BLE001 — si re-firmar falla, error normal
                    pass
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise ImageryError(
        f"No se pudo leer la banda tras {retries} intentos: {str(last_exc)[:160]}"
    )


def reproject_like(win: BandWindow, like: BandWindow) -> BandWindow:
    """Reamostrea ``win`` a la grilla EXACTA de ``like`` (mismo CRS, transform y
    shape) para poder restar píxel a píxel.

    Sin esto, dos escenas Sentinel-2 del mismo AOI en zonas UTM distintas (o con
    orígenes de recorte diferentes) se restarían con los píxeles desalineados y
    el Δ NDVI saldría numéricamente falso. NDVI es una superficie continua →
    remuestreo bilinear; el nodata (NaN) se preserva.
    """
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    dst = np.full(like.data.shape, np.nan, dtype="float32")
    reproject(
        source=win.data,
        destination=dst,
        src_transform=win.transform,
        src_crs=win.crs,
        dst_transform=like.transform,
        dst_crs=like.crs,
        src_nodata=float("nan"),
        dst_nodata=float("nan"),
        resampling=Resampling.bilinear,
    )
    return BandWindow(
        data=dst,
        transform=like.transform,
        crs=like.crs,
        bounds4326=like.bounds4326,
        cloud_pct_masked=win.cloud_pct_masked,
    )


# ---------------------------------------------------------------------------
# Índices y estadísticas (hechos)
# ---------------------------------------------------------------------------


def read_scl_mask(
    href_signed: str,
    bbox4326: tuple[float, float, float, float],
    out_shape: tuple[int, int],
    *,
    ref_transform: Any = None,
    ref_crs: Any = None,
    retries: int = 3,
    kind: str = "scl",
) -> np.ndarray | None:
    """Máscara booleana (True = nube/sombra) re-muestreada al grid del NDVI.

    IMG-INFO (alineación SCL): la SCL nativa es 20 m y el NDVI (red/nir) 10 m.
    Si cada banda calcula su ventana redondeando el bbox a SU propio grid, la
    extensión geográfica efectiva difiere hasta ~1 píxel → la máscara queda
    DESPLAZADA respecto al NDVI y enmascara nubes en píxeles vecinos. Cuando el
    caller pasa el georreferenciado del NDVI (``ref_transform``/``ref_crs``),
    leemos la SCL sobre EXACTAMENTE la misma extensión que el NDVI (sin
    re-redondear al grid 20 m) y la remuestreamos a ``out_shape`` → alineación
    exacta con el NDVI. Sin esos refs, cae al comportamiento previo (bbox).

    Best-effort: si la SCL no se puede leer, devuelve None y el caller DECLARA
    que las estadísticas no están filtradas por nubes (nunca falla el análisis
    por la máscara).
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import array_bounds
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    for attempt in range(1, retries + 1):
        try:
            with rasterio.Env(**_GDAL_ENV), rasterio.open(href_signed) as src:
                if ref_transform is not None and ref_crs is not None:
                    # Extensión EXACTA del NDVI (en su CRS), proyectada al CRS de
                    # la SCL (misma escena S2 → normalmente el mismo UTM, así que
                    # la proyección es identidad). NO se redondea al grid 20 m:
                    # el resampling nearest a out_shape alinea al grid del NDVI.
                    h, w = out_shape
                    nb = array_bounds(h, w, ref_transform)  # (left, bottom, right, top)
                    wb = nb if ref_crs == src.crs else transform_bounds(ref_crs, src.crs, *nb)
                    win = from_bounds(*wb, src.transform)
                else:
                    wb = transform_bounds("EPSG:4326", src.crs, *bbox4326)
                    win = from_bounds(*wb, src.transform).round_offsets().round_lengths()
                if win.width <= 0 or win.height <= 0:
                    return None
                scl = src.read(1, window=win, out_shape=out_shape,
                               resampling=Resampling.nearest,
                               boundless=True, fill_value=0)
                return mascara_de_nubes(scl, kind)
        except Exception:  # noqa: BLE001 — best-effort
            if attempt < retries:
                time.sleep(1.0 * attempt)
    return None


# ---------------------------------------------------------------------------
# Estadística zonal (rasterize + bincount — un pase, sin loop por feature)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Operaciones de alto nivel (las que expone el server como tools)
# ---------------------------------------------------------------------------
def alternatives_payload(scenes: list[Scene], chosen: Scene, bbox) -> list[dict]:
    """Otras escenas VÁLIDAS (contienen el AOI) para que el agente o el
    usuario final elijan otra fecha — se re-invoca con ``scene_id``."""
    alts = [s for s in scenes if s.id != chosen.id and s.contains(bbox)]
    return [
        {"id": s.id, "date": s.datetime[:10], "cloud_pct": s.cloud_pct}
        for s in alts[:3]
    ]


def scene_payload(s: Scene, bbox, contains: bool) -> dict:
    return {
        "id": s.id, "datetime": s.datetime, "cloud_pct": s.cloud_pct,
        "provider": s.provider, "contains_aoi": contains,
        "coverage_pct": 100.0 if contains else s.coverage_pct(bbox),
    }


def _href_banda(scene: Any, band: str) -> str | None:
    bands = getattr(scene, "bands", None) or {}
    if band in bands:
        return bands[band]
    return {"red": getattr(scene, "red_href", None), "nir": getattr(scene, "nir_href", None)}.get(band)


def _ndvi_window(provider: StacProvider, scene: Scene, bbox, limits: Limits,
                 index: str = INDICE_DEFECTO) -> BandWindow:
    """Ventana del índice (a - b) / (a + b) con máscara de nubes; por defecto el NDVI.

    T5.5: las dos bandas pueden tener resolución distinta (en Sentinel-2 el SWIR es 20 m y el
    NIR 10 m): la segunda se reamostrea a la grilla EXACTA de la primera antes de operar.
    """
    # Bandas EN PARALELO: secuencial duplicaba la latencia (cada banda son
    # varios range-requests a Azure con ~200ms RTT desde Colombia).
    from concurrent.futures import ThreadPoolExecutor

    a, b = indice(index)["bandas"]
    href_a, href_b = _href_banda(scene, a), _href_banda(scene, b)
    if not href_a or not href_b:
        faltan = [n for n, h in ((a, href_a), (b, href_b)) if not h]
        raise ImageryError(f"la escena {scene.id} no trae la(s) banda(s) {faltan} que necesita "
                           f"{indice(index)['nombre']}")
    check_unidades_coherentes(scene, (a, b))   # antes de gastar dos lecturas remotas

    def _resigner(raw_href):
        """Re-firma el href tras invalidar el token (para el retry en 401/403)."""
        def _f():
            inv = getattr(provider, "invalidate_token", None)
            if inv:
                inv()
            return provider.sign(raw_href)
        return _f

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_a = pool.submit(read_band_window, provider.sign(href_a),
                          bbox, retries=limits.band_read_retries,
                          resign=_resigner(href_a), scaling=scene_scaling(scene, a))
        f_b = pool.submit(read_band_window, provider.sign(href_b),
                          bbox, retries=limits.band_read_retries,
                          resign=_resigner(href_b), scaling=scene_scaling(scene, b))
        # (Antes había un f_scl con out_shape=(0,0) que NUNCA se consumía —
        # trabajo de red muerto, #15. La SCL se relee abajo al shape real.)
        wa, wb = f_a.result(), f_b.result()
    if wb.data.shape != wa.data.shape:
        wb = reproject_like(wb, wa)
    # (a - b) / (a + b): el mismo cálculo (y los mismos descartes) que el NDVI de siempre
    ndvi, px_no_positivos = compute_ndvi_detallado(wb.data, wa.data)
    # MÁSCARA DE NUBES (SCL o qa_pixel): sin ella, las nubes SESGAN la media sin que el
    # usuario lo sepa. Se relee al shape real del índice.
    cloud_pct_masked = None
    if scene.scl_href:
        # IMG-INFO: alineamos la máscara al grid EXACTO del índice (transform/crs de la
        # primera banda) en vez de re-derivar la ventana del bbox.
        scl = read_scl_mask(
            provider.sign(scene.scl_href), bbox, ndvi.shape,
            ref_transform=wa.transform, ref_crs=wa.crs,
            kind=getattr(scene, "mask_kind", "scl"),
        )
        if scl is not None:
            scl = scl[: ndvi.shape[0], : ndvi.shape[1]]
            n_cloud = int(scl.sum())
            ndvi[scl] = np.nan
            cloud_pct_masked = round(100.0 * n_cloud / max(1, ndvi.size), 1)
    win = BandWindow(data=ndvi, transform=wa.transform, crs=wa.crs,
                     bounds4326=bbox, px_no_positivos=px_no_positivos)
    win.cloud_pct_masked = cloud_pct_masked
    return win


def _effective_cloud(max_cloud_pct: float | None, limits: Limits) -> float:
    """Umbral de nubes efectivo. Con `or` un 0% explícito ("solo cielo limpio")
    se confundía con "sin dato" y caía al default 20 (#23); distingue None de 0."""
    return limits.default_max_cloud_pct if max_cloud_pct is None else max_cloud_pct


def _window_bounds4326(win, fallback) -> list:
    """Extent REAL (4326) de la ventana recortada al grid de píxeles (#16).

    from_bounds redondea al píxel, así que el extent efectivo difiere del bbox
    pedido; devolverlo hace que el fitBounds del front encuadre lo RENDERIZADO.
    Fallback al bbox si falta transform/crs (p.ej. ventanas fake en tests)."""
    if win is None or getattr(win, "transform", None) is None \
            or getattr(win, "crs", None) is None:
        return list(fallback)
    try:
        from rasterio.transform import array_bounds
        from rasterio.warp import transform_bounds
        h, w = win.data.shape[:2]
        left, bottom, right, top = array_bounds(h, w, win.transform)
        return list(transform_bounds(win.crs, "EPSG:4326", left, bottom, right, top))
    except Exception:  # noqa: BLE001 — bounds es informativo; nunca debe fallar
        return list(fallback)
