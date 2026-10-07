"""El ESCALADO radiométrico de las bandas: de dónde sale el factor (asset del item, colección,
baseline de ESA o identidad), el offset del baseline 04.00 y su lectura desde la colección STAC.

Salió de `providers.py` (F4 del plan de calidad: providers.py tenía 570 líneas), tal cual.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("imagery_mcp")


_HTTP_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Factor de reflectancia (auditoría 2026-09-08, §1.3)
#
# ORIGEN, por orden de confianza. Los dos primeros son un dato PUBLICADO por el
# catálogo; el tercero es una inferencia nuestra a partir de la especificación de
# ESA, y por eso se declara aparte en el payload: no valen lo mismo.
_SRC_ASSET = "stac-asset"          # `raster:bands` del asset del item


_SRC_COLECCION = "stac-coleccion"  # `raster:bands` de `item_assets` de la colección


_SRC_BASELINE = "baseline-esa"     # derivado de `s2:processing_baseline`


_SRC_IDENTIDAD = "identidad"       # no hay factor y no hay de dónde derivarlo


# Especificación de ESA (SentiWiki de Copernicus, s2-products / s2-processing, y
# la nota de Sentinel Online sobre el cambio de baseline; consultado 2026-09-08):
#
#     L2A_SR = (L2A_DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE
#
# QUANTIFICATION_VALUE = 10000 siempre. BOA_ADD_OFFSET = -1000 en TODAS las
# bandas desde el baseline 04.00 (desplegado el 25 de enero de 2022), y 0 antes.
# ⇒ post-04.00: scale = 1/10000 = 0.0001, offset = -1000/10000 = -0.1
# ⇒ pre-04.00 : scale = 0.0001,           offset = 0.0
#
# COMPROBACIÓN CRUZADA: ese par derivado es EXACTAMENTE el que Earth Search
# publica en `raster:bands` para los mismos items (scale 0.0001, offset -0.1 con
# baseline 05.12). Que la inferencia y el dato publicado coincidan es lo que
# permite usarla cuando el proveedor calla — Planetary Computer, que es el
# DEFAULT del compose, no publica `raster:bands` ni en el item ni en la colección.
_QUANTIFICATION_VALUE = 10000.0


_BOA_ADD_OFFSET = -1000.0


BOA_OFFSET_BASELINE = 4.0          # '04.00'


BOA_OFFSET_FECHA = "2022-01-25"    # despliegue del baseline 04.00


@dataclass(frozen=True)
class BandScaling:
    """Factor de reflectancia de una banda: ``reflectancia = DN * scale + offset``.

    ``source`` dice DE DÓNDE salió (asset, colección o especificación de ESA),
    porque un dato publicado y una inferencia nuestra no merecen la misma
    confianza aunque den el mismo número. No entra en la comparación de
    compatibilidad entre escenas: lo que importa ahí es si el array quedó en
    reflectancia, no por qué camino (auditoría 2026-09-08, §1.3).

    La identidad (1.0, 0.0) significa "no hay factor y no hay de dónde
    derivarlo": el DN se usa tal cual y el payload lo advierte.
    """

    scale: float = 1.0
    offset: float = 0.0
    source: str = _SRC_IDENTIDAD

    @property
    def is_identity(self) -> bool:
        # El origen NO cuenta: lo que define la identidad es que no transforme.
        return self.scale == 1.0 and self.offset == 0.0

    def as_dict(self) -> dict:
        return {"scale": self.scale, "offset": self.offset}


def baseline_value(raw: str | None) -> float | None:
    """'04.00' → 4.0. None si falta o no es numérico.

    Comparación NUMÉRICA, no de texto: como cadenas, '05.12' < '4' y '10.00' <
    '04.00'. Y un baseline ilegible es DESCONOCIDO, nunca pre-04.00: fallar del
    lado que avisa, no del que calla (§1.3)."""
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def scaling_from_baseline(raw: str | None) -> BandScaling | None:
    """Factor DERIVADO de `s2:processing_baseline` según la especificación de ESA.

    Tercer escalón, el que salva el camino por defecto: Planetary Computer no
    publica `raster:bands`, así que sin esto todo NDVI de PC posterior a enero de
    2022 sale ~0,20 bajo aunque el item declare `s2:processing_baseline: 05.12`.
    No es inventarse un factor: es aplicar la fórmula publicada de ESA al dato
    que el propio item trae.

    None si el baseline no es legible — ahí sí no hay nada que derivar."""
    b = baseline_value(raw)
    if b is None:
        return None
    offset = (_BOA_ADD_OFFSET / _QUANTIFICATION_VALUE) if b >= BOA_OFFSET_BASELINE else 0.0
    return BandScaling(scale=1.0 / _QUANTIFICATION_VALUE, offset=offset,
                       source=_SRC_BASELINE)


_IDENTITY_SCALING = BandScaling()


def _asset_scaling(asset: dict, *, source: str = _SRC_ASSET) -> BandScaling | None:
    """`scale`/`offset` PUBLICADOS por un asset STAC, o None si no publica ninguno.

    Earth Search los trae como lista ``raster:bands`` (extensión raster de STAC
    1.0); STAC 1.1 los pone como claves planas ``raster:scale``/``raster:offset``
    o dentro de ``bands``. Se aceptan las tres formas.

    None cuando no hay nada: quien decide qué hacer con ese hueco es
    ``_resolve_scaling``, que baja al siguiente escalón (§1.3).
    """
    if not isinstance(asset, dict):
        return None
    scale = asset.get("raster:scale")
    offset = asset.get("raster:offset")
    bands = asset.get("raster:bands") or asset.get("bands")
    if isinstance(bands, list) and bands and isinstance(bands[0], dict):
        b0 = bands[0]
        if scale is None:
            scale = b0.get("scale", b0.get("raster:scale"))
        if offset is None:
            offset = b0.get("offset", b0.get("raster:offset"))
    if scale is None and offset is None:
        return None
    try:
        return BandScaling(
            scale=1.0 if scale is None else float(scale),
            offset=0.0 if offset is None else float(offset),
            source=source,
        )
    except (TypeError, ValueError):
        # Metadato corrupto: se cae al siguiente escalón, no se adivina aquí.
        return None


def _scaling_from_assets(assets: dict, mapping: dict, *,
                         source: str = _SRC_ASSET) -> dict:
    """{nombre_canónico: BandScaling} de las bandas que SÍ publican factor."""
    out = {}
    for canon, key in mapping.items():
        sc = _asset_scaling(assets.get(key) or {}, source=source)
        if sc is not None:
            out[canon] = sc
    return out


#: Assets que no son reflectancia: no se les deriva factor del baseline.
_NO_REFLECTANCIA = frozenset({"visual", "scl", "cloud", "snow"})


def _resolve_scaling(assets: dict, mapping: dict, baseline: str | None,
                     coll_lookup=None, *, derivar_baseline: bool = True) -> dict:
    """Factor por banda, resolviendo los TRES escalones por orden de confianza
    (auditoría 2026-09-08, §1.3):

    1. ``raster:bands`` del asset del item      — dato publicado (Earth Search)
    2. ``raster:bands`` de ``item_assets``      — dato publicado, a nivel colección
    3. derivado de ``s2:processing_baseline``   — inferencia, especificación ESA

    ``coll_lookup`` es un callable sin argumentos que devuelve el escalón 2. Se
    invoca PEREZOSAMENTE y solo si algún asset se queda sin factor, para que
    ``_parse`` siga siendo puro (sin red) cuando se le llama directo: search y
    get_scene, que ya hacen red, son los únicos que lo pasan.
    """
    # El baseline de ESA solo describe Sentinel-2: en otra colección no se deriva nada de él.
    derived = scaling_from_baseline(baseline) if derivar_baseline else None
    coll: dict | None = None
    out = {}
    for canon, key in mapping.items():
        if not (assets.get(key) or {}).get("href"):
            continue           # banda ausente: no se le guarda factor
        sc = _asset_scaling(assets.get(key) or {})
        if sc is None and coll_lookup is not None:
            if coll is None:
                coll = coll_lookup() or {}
            sc = coll.get(canon)
        if sc is None and canon not in _NO_REFLECTANCIA:
            # `visual` es el TCI: 8-bit ya renderizado, NO reflectancia. El
            # BOA_ADD_OFFSET de la especificación no le aplica y derivárselo
            # sería un factor sencillamente falso. Igual las capas de calidad: la
            # clasificación SCL y las probabilidades de nube y nieve (0–100).
            sc = derived
        if sc is not None:
            out[canon] = sc
    return out


def _fetch_collection_scaling(url: str, mapping: dict) -> tuple[dict, bool]:
    """Escalón 2: `raster:bands` de `item_assets` de la colección.

    Devuelve ``(factores, concluyente)``. Best-effort — nunca tumba una búsqueda,
    la resolución baja al escalón 3 — pero DISTINGUE los dos casos (revisión
    adversa 2026-09-08, media 10):

    - ``concluyente=True``  → se consultó y esto es lo que hay. PC devuelve {}:
      su colección no declara el factor, comprobado el 2026-09-08. Cacheable.
    - ``concluyente=False`` → no se pudo consultar (red, 503, timeout). NO se
      cachea: si no, un 503 transitorio en la primera búsqueda degradaba el
      escalón 2 al 3 para toda la vida del proceso, y en silencio.
    """
    try:
        resp = httpx.get(url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        item_assets = resp.json().get("item_assets") or {}
    except Exception as exc:  # noqa: BLE001 — escalón opcional, pero se registra
        logger.warning(
            "no se pudo consultar el factor de reflectancia en la colección STAC "
            "(%s): %s: %s. Se deriva del baseline y se reintenta en la próxima "
            "búsqueda.", url, type(exc).__name__, exc,
        )
        return {}, False
    return _scaling_from_assets(item_assets, mapping, source=_SRC_COLECCION), True
