"""ESTADÍSTICAS (hechos): la máscara de nubes, el índice por píxel, sus percentiles y las zonales.

Salió de `engine.py` (F4 del plan de calidad: tenía 1.303 líneas), tal cual.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _eng():
    """`engine` importa este módulo; lo que vive allí (y las pruebas sustituyen allí) se resuelve
    al usarlo."""
    from imagery_mcp import engine

    return engine


# Clases SCL de Sentinel-2 que ENMASCARAN el índice (no son suelo/vegetación):
# 3=sombra de nube, 8=nube prob. media, 9=nube prob. alta, 10=cirro.
_SCL_CLOUD_CLASSES = (3, 8, 9, 10)


# Landsat C2 `qa_pixel`: bits 1 (nube dilatada), 2 (cirro), 3 (nube) y 4 (sombra de nube).
_QA_NUBE_BITS = 0b11110


def mascara_de_nubes(datos: np.ndarray, kind: str) -> np.ndarray:
    """True = nube/sombra, según el TIPO de máscara de la colección (hecho del catálogo)."""
    if kind == "qa_pixel":
        return (datos.astype("uint16") & _QA_NUBE_BITS) != 0
    return np.isin(datos, _SCL_CLOUD_CLASSES)


def compute_ndvi_detallado(red: np.ndarray, nir: np.ndarray) -> tuple[np.ndarray, int]:
    """NDVI + nº de píxeles descartados por reflectancia no positiva.

    GUARDA DE DOMINIO (revisión adversa 2026-09-08, sobre §1.3): la versión
    anterior exigía ``den > 0``, que bastaba mientras la entrada era DN (uint16,
    siempre ≥ 0). Con reflectancia el BOA_ADD_OFFSET produce valores NEGATIVOS
    —para eso existe: codificar reflectancia negativa en un entero sin signo— y
    el cociente se desmadra SIN salirse de lo finito:

        DN(960, 1060) → refl(-0.0040, +0.0060) → NDVI = 5.0000076
        DN(900, 1200) → refl(-0.0100, +0.0200) → NDVI = 3.000003

    Un NDVI de 5,0 pasa ``den > 0``, pasa ``isfinite`` y entra entero en
    ``array_stats``: contamina max/mean/std, el ``p98`` estira la rampa de las
    teselas a [p2, 5.0] y el mapa sale lavado, ``run_zonal`` colorea un polígono
    de agua con media 2,3 y un píxel que va de +5 a -3 se cuenta como pérdida
    fuerte. Y un DN < 1000 no es raro: agua, sombra de nube, sombra de montaña.

    Se exige reflectancia POSITIVA en ambas bandas. Sobre DN crudo la guarda es
    equivalente a la anterior (un DN ≥ 0 solo falla ``> 0`` si vale 0, que ya era
    el nodata del #13), así que no mueve ni un número de los productos sin factor.

    Se DEVUELVE cuántos se descartaron: la política del repo es declarar lo que
    se filtró, y sin esto los píxeles de agua se van a NaN mezclados con los
    descartados por nube y con el nodata del recorte.
    """
    h = min(red.shape[0], nir.shape[0])
    w = min(red.shape[1], nir.shape[1])
    red, nir = red[:h, :w], nir[:h, :w]
    with np.errstate(invalid="ignore", divide="ignore"):
        # Un 0 en CUALQUIER banda es nodata del recorte: si solo una es 0 el NDVI
        # saldría espurio (±1) en agua/sombra. OR, no AND (#13) — la exigencia de
        # positividad lo subsume, porque 0 no es > 0.
        usable = (red > 0) & (nir > 0)
        ndvi = np.where(usable, (nir - red) / (nir + red), np.nan)
        # Descartado por reflectancia no positiva = tenía dato (finito y distinto
        # del 0 de nodata) pero no era positivo. Sobre DN crudo esto es siempre 0.
        con_dato = np.isfinite(red) & np.isfinite(nir) & (red != 0) & (nir != 0)
        no_positivos = int(np.count_nonzero(con_dato & ~usable))
    return ndvi.astype("float32"), no_positivos


def compute_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """NDVI a secas. Para el recuento de descartes, ``compute_ndvi_detallado``."""
    return compute_ndvi_detallado(red, nir)[0]


def array_stats(arr: np.ndarray) -> dict:
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        raise _eng().ImageryError("La ventana no contiene píxeles válidos (¿todo nodata?)")
    return {
        "mean": round(float(valid.mean()), 4),
        "min": round(float(valid.min()), 4),
        "max": round(float(valid.max()), 4),
        "std": round(float(valid.std()), 4),
        "p2": round(float(np.percentile(valid, 2)), 4),
        "p98": round(float(np.percentile(valid, 98)), 4),
        "p25": round(float(np.percentile(valid, 25)), 4),
        "p50": round(float(np.percentile(valid, 50)), 4),
        "p75": round(float(np.percentile(valid, 75)), 4),
        "px_validos": int(valid.size),
        "px_total": int(arr.size),
    }


def _formas(features: list, crs: Any) -> tuple[list, list[dict]]:
    """Cada geometría reproyectada al CRS del ráster con su etiqueta (i+1); las que no, a `skipped`."""
    from rasterio.warp import transform_geom

    shapes = []
    skipped: list[dict] = []
    for i, feat in enumerate(features):
        geom = feat.get("geometry")
        if not geom:
            skipped.append({"feature_index": i, "reason": "sin geometría"})
            continue
        try:
            shapes.append((transform_geom("EPSG:4326", crs, geom), i + 1))
        except Exception as exc:  # noqa: BLE001 — geometría individual inválida
            skipped.append({"feature_index": i, "reason": str(exc)[:80]})
    return shapes, skipped


def _fila_por_sus_pixeles(i: int, geom: dict, index_arr: np.ndarray, transform: Any,
                          skipped: list[dict]) -> dict | None:
    """La fila de una geometría sin píxeles propios en el ráster de etiquetas (o None, a `skipped`)."""
    # V5 F3: el raster de etiquetas da cada píxel a UNA geometría; una más
    # pequeña que el píxel y con vecinas (lotes de ~100 m² con píxeles de
    # 10 m) se quedaba sin ninguno. Se calcula sola sobre los que toca.
    propios = _valores_de_geometria(geom, index_arr, transform)
    if propios.size == 0:
        skipped.append({
            "feature_index": i,
            "reason": "la geometría no toca ningún píxel válido del índice "
                      "(fuera de la escena, o nubes/nodata encima)",
        })
        return None
    return {
        "feature_index": i,
        "mean": round(float(propios.mean()), 4),
        "min": round(float(propios.min()), 4),
        "max": round(float(propios.max()), 4),
        "std": round(float(propios.std()), 4),
        "px": int(propios.size),
        # hecho para el analista: el valor es el de los píxeles que la
        # geometría comparte con sus vecinas (es menor que la resolución)
        "px_compartidos": True,
    }


def _zonas(features_geojson: dict, max_features: int) -> list:
    """Las features de las zonas (ni vacías ni por encima del límite)."""
    features = features_geojson.get("features") or []
    if not features:
        raise _eng().ImageryError("El GeoJSON de zonas no contiene features")
    if len(features) > max_features:
        raise _eng().ImageryError(
            f"{len(features)} features supera el límite v1 de {max_features} "
            "para estadística zonal"
        )
    return features


def zonal_stats(
    features_geojson: dict,
    index_arr: np.ndarray,
    transform: Any,
    crs: Any,
    *,
    max_features: int = 5000,
) -> tuple[list[dict], list[dict]]:
    """Media/min/max/std del índice por feature. Devuelve (rows, skipped)."""
    import rasterio.features

    features = _zonas(features_geojson, max_features)

    shapes, skipped = _formas(features, crs)

    if not shapes:
        raise _eng().ImageryError("Ninguna feature tiene geometría reproyectable")

    labels = rasterio.features.rasterize(
        shapes, out_shape=index_arr.shape, transform=transform,
        fill=0, dtype="int32", all_touched=True,
    )

    finite = np.isfinite(index_arr)
    lab = labels[finite]
    val = index_arr[finite]
    n = len(features) + 1
    counts = np.bincount(lab, minlength=n)
    sums = np.bincount(lab, weights=val, minlength=n)
    sq_sums = np.bincount(lab, weights=val * val, minlength=n)

    geom_de = {idx - 1: g for g, idx in shapes}
    rows: list[dict] = []
    for i in range(len(features)):
        c = int(counts[i + 1])
        if c == 0:
            if i not in geom_de:
                continue  # ya está en skipped (sin geometría / no reproyectable)
            fila = _fila_por_sus_pixeles(i, geom_de[i], index_arr, transform, skipped)
            if fila is not None:
                rows.append(fila)
            continue
        mean = sums[i + 1] / c
        var = max(0.0, sq_sums[i + 1] / c - mean * mean)
        mask = lab == (i + 1)
        rows.append({
            "feature_index": i,
            "mean": round(float(mean), 4),
            "min": round(float(val[mask].min()), 4),
            "max": round(float(val[mask].max()), 4),
            "std": round(float(math.sqrt(var)), 4),
            "px": c,
        })
    return rows, skipped


def _limites(coords: Any) -> tuple[float, float, float, float] | None:
    """bbox de las coordenadas de una geometría GeoJSON (sin shapely: no está en la imagen)."""
    xs: list[float] = []
    ys: list[float] = []

    def recorrer(c: Any) -> None:
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(float(c[0]))
            ys.append(float(c[1]))
        elif isinstance(c, (list, tuple)):
            for hijo in c:
                recorrer(hijo)

    recorrer(coords)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _valores_de_geometria(geom: dict, index_arr: np.ndarray, transform: Any) -> np.ndarray:
    """Valores finitos del índice en los píxeles que TOCA una geometría (ya en el CRS del raster)."""
    import rasterio.features
    import rasterio.windows
    from rasterio.windows import Window

    partes = [g.get("coordinates") for g in geom.get("geometries") or []] or [geom.get("coordinates")]
    limites = _limites(partes)
    if limites is None:
        return np.empty(0, dtype=index_arr.dtype)
    x0, y0, x1, y1 = limites
    esquinas = [~transform * (x, y) for x in (x0, x1) for y in (y0, y1)]  # → (col, fila)
    col0 = max(0, math.floor(min(c for c, _ in esquinas)) - 1)
    fila0 = max(0, math.floor(min(f for _, f in esquinas)) - 1)
    col1 = min(index_arr.shape[1], math.ceil(max(c for c, _ in esquinas)) + 1)
    fila1 = min(index_arr.shape[0], math.ceil(max(f for _, f in esquinas)) + 1)
    if fila1 <= fila0 or col1 <= col0:
        return np.empty(0, dtype=index_arr.dtype)
    trozo = index_arr[fila0:fila1, col0:col1]
    dentro = rasterio.features.geometry_mask(
        [geom], out_shape=trozo.shape, invert=True, all_touched=True,
        transform=rasterio.windows.transform(Window(col0, fila0, col1 - col0, fila1 - fila0), transform),
    )
    valores = trozo[dentro]
    return valores[np.isfinite(valores)]
