"""Las OPERACIONES del servicio (lo que exponen los tools): buscar escenas, índice, cambio entre
fechas, zonales y composición en color.

Salió de `engine.py` (F4 del plan de calidad: tenía 1.303 líneas), tal cual.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np

from imagery_mcp.aoi import aoi_bbox, bbox_polygon, check_aoi, rango_fechas
from imagery_mcp.config import Limits
from imagery_mcp.estadisticas import array_stats, zonal_stats
from imagery_mcp.providers import (
    Scene,
    StacProvider,
)
from imagery_mcp.radiometria import (
    INDICE_DEFECTO,
    check_unidades_coherentes,
    descartes_payload,
    indice,
    mensaje_incompatibles,
    motivo_sin_validos,
    radiometry,
    reflectance_payload,
)


def _eng():
    """`engine` importa este módulo; lo que vive allí (y las pruebas sustituyen allí) se resuelve
    al usarlo."""
    from imagery_mcp import engine

    return engine


def _con_coleccion(collection: str | None) -> dict:
    """kwargs de colección para el proveedor (sin ella, su colección por defecto)."""
    return {"collection": collection} if collection else {}


def _que_se_busco(collection: str | None, d0: str, d1: str, max_cloud_pct: float | None, limits: Limits) -> str:
    from imagery_mcp.providers import COLECCION_DEFECTO, COLECCIONES

    col = COLECCIONES.get(collection or COLECCION_DEFECTO)
    nombre = col.etiqueta if col else str(collection)
    return f"{nombre} entre {d0} y {d1} con nubes < {_eng()._effective_cloud(max_cloud_pct, limits):g} %"


def _hecho_coleccion(scene: Any) -> dict:
    from imagery_mcp.providers import COLECCIONES

    col = COLECCIONES.get(getattr(scene, "collection", "") or "")
    return {"id": col.id, "nombre": col.etiqueta, "resolucion_m": col.resolucion_m} if col else {}


def run_search(provider: StacProvider, aoi_geojson: dict, date_from: str | None,
               date_to: str | None, max_cloud_pct: float | None,
               limit: int, limits: Limits, collection: str | None = None) -> dict:
    bbox = aoi_bbox(aoi_geojson)
    check_aoi(bbox, limits)
    d0, d1 = rango_fechas(date_from, date_to, limits)
    scenes = provider.search(
        bbox_polygon(bbox), d0, d1,
        _eng()._effective_cloud(max_cloud_pct, limits), limit, **_con_coleccion(collection),
    )
    return {
        "date_from": d0, "date_to": d1,
        "scenes": [_eng().scene_payload(s, bbox, s.contains(bbox)) for s in scenes],
    }


def _teselas_indice(scene: Any, win: Any, bbox: Any, stats: dict, index: str) -> dict:
    """Las teselas dinámicas del índice: rampa estirada al rango real del AOI y su extent."""
    # DYNAMIC RANGE STRETCH (patrón EO Browser): la rampa de las teselas se
    # estira al rango REAL del AOI (p2–p98) — con el fijo [-1,1] un AOI
    # urbano (NDVI 0–0.4) se veía plano/lavado.
    _lo, _hi = stats["p2"], stats["p98"]
    if _hi - _lo < 0.05:  # rango degenerado → rampa completa
        _lo, _hi = -1.0, 1.0
    _bounds = _eng()._window_bounds4326(win, bbox)   # extent real del recorte (#16)
    # T5.5: índice y colección viajan en la URL de las teselas (las de NDVI de Sentinel-2
    # quedan como siempre: sin parámetros de más)
    extra = "".join(f"&{k}={v}" for k, v in (("index", index), ("collection", getattr(scene, "collection", "")))
                    if v and v not in (INDICE_DEFECTO, "sentinel-2-l2a"))
    return {
        "url_template": (
            f"/tiles/{scene.id}/{{z}}/{{x}}/{{y}}.png"
            f"?rescale={_lo},{_hi}{extra}"
        ),
        "rescale": [_lo, _hi],
        # bounds = el AOI (no la escena de 110 km): el frontend hace
        # fitBounds a la ZONA analizada, no a todo el tile MGRS.
        "minzoom": 8, "maxzoom": 15, "bounds": _bounds,
    }


def run_ndvi(provider: StacProvider, aoi_geojson: dict, date_from: str | None,
             date_to: str | None, scene_id: str | None, limits: Limits,
             on_scene=None, max_cloud_pct: float | None = None,
             index: str | None = None, collection: str | None = None) -> dict:
    ind = indice(index)
    index = (index or INDICE_DEFECTO).lower()
    bbox = aoi_bbox(aoi_geojson)
    check_aoi(bbox, limits)
    d0, d1 = rango_fechas(date_from, date_to, limits)
    scenes = provider.search(bbox_polygon(bbox), d0, d1,
                             _eng()._effective_cloud(max_cloud_pct, limits), 10, **_con_coleccion(collection))
    if scene_id:
        scenes = _eng()._filter_requested_scene(provider, scenes, scene_id, collection)
    scene, contained = _eng().select_scene(scenes, bbox, busqueda=_que_se_busco(collection, d0, d1, max_cloud_pct, limits))
    win = _eng()._ndvi_window(provider, scene, bbox, limits, index)
    if on_scene is not None:
        on_scene(scene)
    if not np.isfinite(win.data).any():
        raise _eng().ImageryError(motivo_sin_validos(win, scene))
    stats = array_stats(win.data)
    return {
        "index": {"id": index, "nombre": ind["nombre"], "bandas": list(ind["bandas"]),
                  "formula": f"({ind['bandas'][0]} - {ind['bandas'][1]}) / ({ind['bandas'][0]} + {ind['bandas'][1]})",
                  "lectura": ind["lectura"]},
        "collection": _hecho_coleccion(scene) or None,
        "scene": _eng().scene_payload(scene, bbox, contained),
        "alternatives": _eng().alternatives_payload(scenes, scene, bbox),
        # Teselado dinámico (spec §8): capa nítida a cualquier zoom; el
        # preview PNG de abajo es para feedback INSTANTÁNEO mientras calienta.
        "tiles": _teselas_indice(scene, win, bbox, stats, index),
        "stats": stats,
        # §1.3: qué factor DN→reflectancia se aplicó. Auditable por el consumidor:
        # sin él, un NDVI sesgado y uno correcto son indistinguibles.
        "reflectance": reflectance_payload(scene, tuple(ind["bandas"])),
        # Bloqueante 1: lo que el índice descartó por reflectancia no positiva.
        "descartes": descartes_payload(win),
        "cloud_mask": (
            {"applied": True, "pct_px_enmascarados": win.cloud_pct_masked}
            if win.cloud_pct_masked is not None
            else {"applied": False,
                  "nota": "máscara de nubes no disponible; las estadísticas incluyen nubes"}
        ),
        # (png_overlay eliminado, #33: NADIE lo consumía —el mapa usa las
        # teselas dinámicas— y render_png era CPU desperdiciada en cada NDVI.)
        "degraded": None if contained else (
            f"ninguna escena contiene el AOI completo; se usó la de mejor "
            f"cobertura ({scene.coverage_pct(bbox)}%)"
        ),
    }


def _escenas_de_cambio(provider: StacProvider, bbox: Any, date_a: str, date_b: str, window_days: int,
                       limits: Limits, max_cloud_pct: float | None) -> tuple[Scene, bool, Scene, bool]:
    """Las dos escenas del cambio, y las guardas que se deciden sin leer ningún COG."""
    from datetime import date as _date

    def _scene_for(day: str) -> tuple[Scene, bool]:
        try:
            d = _date.fromisoformat(str(day)[:10])
        except ValueError:
            raise _eng().ImageryError(f"«{day}» no es una fecha válida: usa YYYY-MM-DD de un día que exista") from None
        d0, d1 = str(d - timedelta(days=window_days)), str(d + timedelta(days=window_days))
        scenes = provider.search(bbox_polygon(bbox), d0, d1,
                                 _eng()._effective_cloud(max_cloud_pct, limits), 10)
        return _eng().select_scene(scenes, bbox)

    # Las DOS búsquedas en paralelo, y las guardas ANTES de leer ningún COG
    # (revisión adversa 2026-09-08, media 13): las tres guardas se deciden con
    # los metadatos de la escena, así que rechazar después de leer las cuatro
    # bandas eran cuatro lecturas remotas tiradas.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as _pool:
        _fa = _pool.submit(_scene_for, date_a)
        _fb = _pool.submit(_scene_for, date_b)
        scene_a, cont_a = _fa.result()
        scene_b, cont_b = _fb.result()

    # GUARDA 1: si ambas fechas resuelven a la MISMA escena, el "cambio" sería
    # idénticamente 0 (falso "vegetación estable"). Con window_days grande dos
    # fechas cercanas colapsan a la escena de menos nubes. Error honesto.
    if scene_a.id == scene_b.id:
        raise _eng().ImageryError(
            "Las dos fechas resuelven a la misma escena Sentinel-2 "
            f"({scene_a.id}); separa el rango de fechas o reduce window_days "
            "para comparar dos observaciones distintas."
        )

    # GUARDA 3 (auditoría 2026-09-08, §1.3): las dos escenas tienen que estar en
    # la MISMA radiometría. Un "deforestación 2020 vs 2024" cruza el corte del
    # baseline 04.00: si el proveedor no publica el factor, un lado lleva el
    # BOA_ADD_OFFSET incorporado al DN y el otro no, el Δ es un artefacto de
    # ~-0,20 constante y el umbral `diff < -0.1` de abajo marcaría casi el 100 %
    # de los píxeles como pérdida fuerte sobre vegetación intacta. Error tipado:
    # el número, el mapa y la narrativa saldrían coherentes entre sí y falsos.
    if radiometry(scene_a) != radiometry(scene_b):
        raise _eng().ImageryError(mensaje_incompatibles(scene_a, scene_b))

    # GUARDA 4 (media 9): cada escena, por separado, en una sola unidad. Va aquí
    # y no solo dentro de _ndvi_window para que también se decida sin leer COGs.
    check_unidades_coherentes(scene_a)
    check_unidades_coherentes(scene_b)
    return scene_a, cont_a, scene_b, cont_b


def _alineada(win_a: Any, win_b: Any) -> tuple[Any, bool]:
    """`win_b` en la grilla de `win_a` (y si hubo que reamostrar)."""
    # GUARDA 2: las dos ventanas pueden estar en grillas/CRS distintos (tiles
    # MGRS de zonas UTM diferentes cerca de un borde de zona). Restar píxel a
    # píxel sin alinear da basura → reamostrar win_b a la grilla de win_a.
    reprojected = False
    if (
        win_a.crs != win_b.crs
        or win_a.transform != win_b.transform
        or win_a.data.shape != win_b.data.shape
    ):
        win_b = _eng().reproject_like(win_b, like=win_a)
        reprojected = True
    return win_b, reprojected


def _teselas_cambio(scene_a: Any, scene_b: Any, win_a: Any, bbox: Any, dstats: dict) -> dict:
    """Las teselas del cambio: rampa divergente simétrica alrededor de 0, a la magnitud real."""
    # Rescale SIMÉTRICO alrededor de 0 para la rampa divergente de las teselas:
    # así el amarillo (centro) = "sin cambio" y el rango se ajusta a la magnitud
    # real del cambio (p2/p98), acotado a [0.1, 1.0] para no degenerar.
    _m = max(abs(dstats.get("p2", 0.0)), abs(dstats.get("p98", 0.0)))
    _m = max(0.1, min(1.0, _m))
    _bounds = _eng()._window_bounds4326(win_a, bbox)   # grilla de referencia del diff (#16)
    return {
        "url_template": (
            f"/tiles-diff/{scene_a.id}/{scene_b.id}/{{z}}/{{x}}/{{y}}.png"
            f"?rescale={-_m},{_m}"
        ),
        "rescale": [-_m, _m],
        "minzoom": 8, "maxzoom": 15, "bounds": _bounds,
        "kind": "diff",
    }


def _degradado(contenidas: bool, reprojected: bool) -> str | None:
    """Lo que hay que declarar del cambio: cobertura parcial o grillas reamostradas."""
    notes = []
    if not (contenidas):
        notes.append("cobertura parcial del AOI")
    if reprojected:
        notes.append(
            "las escenas estaban en grillas/CRS distintos; se reamostró a una "
            "grilla común antes de restar"
        )
    return "; ".join(notes) or None


def run_change(provider: StacProvider, aoi_geojson: dict, date_a: str,
               date_b: str, window_days: int, limits: Limits,
               max_cloud_pct: float | None = None, on_scene=None) -> dict:
    bbox = aoi_bbox(aoi_geojson)
    check_aoi(bbox, limits)

    scene_a, cont_a, scene_b, cont_b = _escenas_de_cambio(provider, bbox, date_a, date_b, window_days,
                                                          limits, max_cloud_pct)

    # Ya pasadas las guardas: AHORA sí se leen las cuatro bandas. Las dos ventanas
    # en paralelo — en serie, `change` rozaba el read-timeout del cliente (#22);
    # cada _ndvi_window lee además sus dos bandas en paralelo.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as _pool:
        _wa = _pool.submit(_eng()._ndvi_window, provider, scene_a, bbox, limits)
        _wb = _pool.submit(_eng()._ndvi_window, provider, scene_b, bbox, limits)
        win_a, win_b = _wa.result(), _wb.result()

    # Registrar AMBAS escenas para que /tiles-diff pueda servir el cambio (M8).
    if on_scene is not None:
        on_scene(scene_a)
        on_scene(scene_b)

    win_b, reprojected = _alineada(win_a, win_b)

    diff = win_b.data - win_a.data

    degraded = _degradado(cont_a and cont_b, reprojected)

    finite = np.isfinite(diff)
    total = max(1, int(finite.sum()))
    dstats = array_stats(diff)
    return {
        "scene_a": _eng().scene_payload(scene_a, bbox, cont_a),
        "scene_b": _eng().scene_payload(scene_b, bbox, cont_b),
        "degraded": degraded,
        "diff_stats": dstats,
        # §1.3: la radiometría de AMBAS escenas (la guarda de arriba ya garantiza
        # que casan; declararla deja constancia de CON QUÉ se restó).
        "reflectance": {"scene_a": reflectance_payload(scene_a),
                        "scene_b": reflectance_payload(scene_b)},
        "descartes": descartes_payload(win_a, win_b),
        # Teselado dinámico del cambio (M8): NDVI(b)−NDVI(a) alineado en
        # web-mercator, rampa divergente rojo→amarillo→verde con rescale [-m, m].
        "tiles": _teselas_cambio(scene_a, scene_b, win_a, bbox, dstats),
        # Hechos interpretables (el juicio narrativo es del LLM de la app):
        "facts": {
            "pct_px_perdida_fuerte": round(100.0 * float((diff[finite] < -0.1).sum()) / total, 1),
            "pct_px_ganancia_fuerte": round(100.0 * float((diff[finite] > 0.1).sum()) / total, 1),
            "umbral": 0.1,
        },
        # (png_overlay eliminado, #33: el cambio se ve por las teselas /tiles-diff.)
    }


def run_zonal(provider: StacProvider, features_geojson: dict, date_from: str | None,
              date_to: str | None, scene_id: str | None, limits: Limits,
              on_scene=None, max_cloud_pct: float | None = None,
              index: str | None = None, collection: str | None = None) -> dict:
    ind = indice(index)
    index = (index or INDICE_DEFECTO).lower()
    bbox = aoi_bbox(features_geojson)
    check_aoi(bbox, limits)
    d0, d1 = rango_fechas(date_from, date_to, limits)
    scenes = provider.search(bbox_polygon(bbox), d0, d1,
                             _eng()._effective_cloud(max_cloud_pct, limits), 10, **_con_coleccion(collection))
    if scene_id:
        scenes = _eng()._filter_requested_scene(provider, scenes, scene_id, collection)
    scene, contained = _eng().select_scene(scenes, bbox, busqueda=_que_se_busco(collection, d0, d1, max_cloud_pct, limits))
    win = _eng()._ndvi_window(provider, scene, bbox, limits, index)
    if on_scene is not None:
        on_scene(scene)
    rows, skipped = zonal_stats(
        features_geojson, win.data, win.transform, win.crs,
        max_features=limits.zonal_max_features,
    )
    return {
        "index": {"id": index, "nombre": ind["nombre"], "bandas": list(ind["bandas"]), "lectura": ind["lectura"]},
        "collection": _hecho_coleccion(scene) or None,
        "scene": _eng().scene_payload(scene, bbox, contained),
        "alternatives": _eng().alternatives_payload(scenes, scene, bbox),
        "rows": rows,
        "skipped": skipped,
        "reflectance": reflectance_payload(scene, tuple(ind["bandas"])),   # §1.3, igual que run_ndvi
        "descartes": descartes_payload(win),
        "degraded": None if contained else "cobertura parcial del AOI",
    }


# Combinaciones de bandas RGB válidas (en sync con tiles._COMPOSITES).
_COMPOSITE_COMBOS = ("true_color", "false_color", "agriculture", "swir")


def run_composite(provider: StacProvider, aoi_geojson: dict, combo: str,
                  date_from: str | None, date_to: str | None,
                  scene_id: str | None, limits: Limits,
                  on_scene=None, max_cloud_pct: float | None = None) -> dict:
    """Imagen satelital en color (color real u otra combinación de bandas).

    Solo elige la escena y publica la capa de teselas RGB — NO lee ventana (las
    teselas se componen on-demand). `combo` ∈ true_color/false_color/agriculture/
    swir; el estiramiento lo hace el teselado."""
    if combo not in _COMPOSITE_COMBOS:
        raise _eng().ImageryError(
            f"combinación de bandas desconocida: {combo!r} "
            f"(usa una de: {', '.join(_COMPOSITE_COMBOS)})"
        )
    bbox = aoi_bbox(aoi_geojson)
    check_aoi(bbox, limits)
    d0, d1 = rango_fechas(date_from, date_to, limits)
    scenes = provider.search(bbox_polygon(bbox), d0, d1,
                             _eng()._effective_cloud(max_cloud_pct, limits), 10)
    if scene_id:
        scenes = _eng()._filter_requested_scene(provider, scenes, scene_id)
    scene, contained = _eng().select_scene(scenes, bbox)
    if on_scene is not None:
        on_scene(scene)
    return {
        "scene": _eng().scene_payload(scene, bbox, contained),
        "alternatives": _eng().alternatives_payload(scenes, scene, bbox),
        "combo": combo,
        "tiles": {
            "url_template": f"/tiles-rgb/{scene.id}/{combo}/{{z}}/{{x}}/{{y}}.png",
            "minzoom": 8, "maxzoom": 15, "bounds": list(bbox),
            "kind": "rgb", "combo": combo,
        },
        "degraded": None if contained else (
            f"ninguna escena contiene el AOI completo; se usó la de mejor "
            f"cobertura ({scene.coverage_pct(bbox)}%)"
        ),
    }
