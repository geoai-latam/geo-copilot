"""El RENDER de teselas: NDVI de una escena, la diferencia entre dos fechas y el color (RGB),
con su lectura de bandas.

Salió de `TilePool` (F4 del plan de calidad: tiles.py tenía 689 líneas), tal cual.
"""

from __future__ import annotations

import numpy as np

from imagery_mcp.engine import (
    _GDAL_ENV,
    INDICES,
    _is_auth_error,
    apply_scaling,
    compute_ndvi,
    mascara_de_nubes,
    mensaje_incompatibles,
    radiometry,
    scene_scaling,
)


class EscenasIncompatiblesError(ValueError):
    """Dos escenas que no se pueden restar (radiometría distinta) → 409, no PNG.

    Tipo propio porque el middleware ya mapea KeyError a 404 y el resto a 502, y
    esto no es "no está" ni "falló la red": es "esto no se debe calcular"
    (revisión adversa 2026-09-08, media 6)."""


_TILESIZE = 256


# PNG 1x1 transparente (para teselas fuera del footprint de la escena).
_TRANSPARENT_PNG = bytes.fromhex(  # 1x1 RGBA alpha=0, verificado con PIL
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360606060000000050001a5f645400000000049454e44"
    "ae426082"
)


# Combinaciones de bandas RGB (R-G-B) por nombre canónico. `true_color` usa el
# asset `visual` (TCI ya renderizado) si existe; el resto compone 3 bandas en reflectancia
# con un estiramiento fijo (ver `_estirar_banda`).
_COMPOSITES = {
    "true_color": ("red", "green", "blue"),     # color natural
    "false_color": ("nir", "red", "green"),     # vegetación en rojo
    "agriculture": ("swir16", "nir", "blue"),   # cultivos/suelo
    "swir": ("swir22", "swir16", "red"),        # urbano/SWIR
}


def _band_href(scene, band: str):
    """href SIN firmar de una banda por nombre canónico; None si la escena no la
    trae. Cae a red_href/nir_href para escenas sin `bands` (tests / compat)."""
    if band == "scl":
        return getattr(scene, "scl_href", None)
    bands = getattr(scene, "bands", None) or {}
    if band in bands:
        return bands[band]
    if band == "red":
        return getattr(scene, "red_href", None)
    if band == "nir":
        return getattr(scene, "nir_href", None)
    return None


def _stretch_uint8(arr: np.ndarray, valid) -> np.ndarray:
    """Estira una banda a 8-bit por percentiles p2–p98 de sus píxeles válidos.

    Adaptativo: absorbe la escala/offset del L2A sin conocerlos (v1; el precio es
    una posible costura suave entre teselas — mejorable con rescale por AOI).

    Por eso los composites RGB NO pasan por ``apply_scaling`` (§1.3): un stretch
    por percentiles es invariante a una transformación afín, así que aplicar el
    factor aquí no cambiaría un solo píxel — y sobre el asset `visual` (TCI, ya
    8-bit y sin factor) sería directamente incorrecto."""
    vals = arr[valid] if (valid is not None and valid.any()) else arr.ravel()
    if vals.size == 0:
        return np.zeros(arr.shape, dtype="uint8")
    lo, hi = np.percentile(vals, (2, 98))
    if hi - lo < 1e-6:
        hi = lo + 1.0
    return (np.clip((arr - lo) / (hi - lo), 0, 1) * 255).astype("uint8")


#: Reflectancia que va a 255 en los composites de bandas (ganancia 2,5, la habitual en los
#: visores de Sentinel-2). El MISMO rango en todas las teselas: no hay costuras.
_REFLECTANCIA_MAX = 0.4


def _estirar_banda(arr: np.ndarray, valid, scaling) -> np.ndarray:
    """Una banda cruda a 8-bit. Con su factor publicado (o derivado del baseline) pasa a
    reflectancia y se estira a [0, ``_REFLECTANCIA_MAX``], igual en toda la escena; sin factor
    no hay escala común honesta y se cae al estiramiento por percentiles de la tesela."""
    if scaling is None or scaling.is_identity:
        return _stretch_uint8(arr, valid)
    refl = np.nan_to_num(apply_scaling(arr, scaling), nan=0.0)
    return (np.clip(refl / _REFLECTANCIA_MAX, 0, 1) * 255).astype("uint8")


class TilesRenderMixin:
    """El RENDER de teselas: NDVI de una escena, la diferencia entre dos fechas y el color (RGB),"""

    def _read_scene_ndvi(self, scene_id: str, z: int, x: int, y: int,
                         index: str = "ndvi", collection: str | None = None):
        """NDVI de la tesela z/x/y en el grid web-mercator 256, con máscara de
        validez (footprint + nodata + finito + nubes SCL).

        Devuelve ``(ndvi, valid, scene)`` o ``None`` si la tesela cae 100% fuera
        del footprint. La escena va en el retorno para que ``render_diff_tile``
        pueda comprobar la radiometría sin resolverla por segunda vez.
        `KeyError` si la escena no resuelve; `RuntimeError` si es
        irrecuperable tras el retry. Es el punto ÚNICO de lectura por escena:
        comparte el pool con refcount (H2), el re-firmado en 401/403 (M4) y la
        máscara SCL (M2) entre render_tile (NDVI) y render_diff_tile (cambio)."""
        import rasterio
        from rio_tiler.errors import TileOutsideBounds

        last_exc = None
        for _attempt in (1, 2):
            # _acquire reserva la escena (refs++) para que una evicción
            # concurrente no cierre sus readers a mitad de .tile() (H2). El
            # KeyError de escena irresoluble propaga AQUÍ, antes de crear el
            # scene_lock — así un id falso no deja un lock huérfano (M3).
            entry = self._acquire(scene_id, collection)
            scene_lock = self._get_scene_lock(scene_id)
            try:
                # rasterio.Env: aplica GDAL_HTTP_TIMEOUT a la apertura y lectura
                # del COG — sin él un tile lento fija el scene_lock y agota el
                # thread-pool de anyio (B4 re-auditoría 2026-07-20).
                with scene_lock.take() as lane, rasterio.Env(**_GDAL_ENV):
                    # T5.5: las dos bandas del índice (NDVI: nir, red; NDWI: green, nir…);
                    # rio-tiler lleva cada una al MISMO grid de la tesela, así que bandas de
                    # resolución distinta (SWIR 20 m) quedan alineadas sin más.
                    b_a, b_b = INDICES[index]["bandas"]
                    red_r = self._ensure_band(entry, b_b, lane)
                    nir_r = self._ensure_band(entry, b_a, lane)
                    # SCL best-effort también al ABRIR: un fallo transitorio de la
                    # SCL NO debe envenenar la entry ni tumbar la tesela NDVI (B1).
                    try:
                        scl_r = self._ensure_band(entry, "scl", lane)
                    except Exception:  # noqa: BLE001 — sin SCL no se enmascara
                        scl_r = None
                    if red_r is None or nir_r is None:
                        raise RuntimeError(f"la escena no trae las bandas {b_a}/{b_b}")
                    t_red = red_r.tile(x, y, z, tilesize=_TILESIZE)
                    t_nir = nir_r.tile(x, y, z, tilesize=_TILESIZE)
                    # Máscara de nubes SCL de LA MISMA tesela (nearest, mismo
                    # z/x/y) → alineada con red/nir sin bounds independientes
                    # (#14). Best-effort: un fallo de SCL no tumba la tesela.
                    t_scl = None
                    if scl_r is not None:
                        try:
                            t_scl = scl_r.tile(x, y, z, tilesize=_TILESIZE,
                                               resampling_method="nearest")
                        except Exception:  # noqa: BLE001 — SCL opcional
                            t_scl = None
                # MISMA ruta escalada que el motor (auditoría 2026-09-08, §1.3):
                # aquí había una segunda cuenta sobre el DN crudo, así que el
                # mapa que el usuario ve podía discrepar del NDVI de las stats
                # —o coincidir con él estando ambos sesgados—. `apply_scaling` es
                # el mismo de engine.py: una sola definición de "reflectancia".
                red = apply_scaling(t_red.data[0].astype("float32"),
                                    scene_scaling(entry.scene, b_b))
                nir = apply_scaling(t_nir.data[0].astype("float32"),
                                    scene_scaling(entry.scene, b_a))
                # Y el MISMO `compute_ndvi` del motor, no una copia: esta línea
                # tenía su propio `den > 0`, que con reflectancia deja pasar
                # NDVI de 5,0 sobre agua (bloqueante 1 de la revisión adversa).
                # Duplicar la fórmula es exactamente cómo se coló la primera vez.
                ndvi = compute_ndvi(red, nir)
                # VALIDEZ (True = píxel usable): dentro del footprint (mask≠0) y
                # con NDVI definido. `compute_ndvi` ya manda a NaN el nodata (0 en
                # DN crudo, NaN tras el factor) y la reflectancia no positiva, así
                # que `isfinite` cubre los tres casos con una sola definición.
                valid = (t_red.mask != 0) & (t_nir.mask != 0) & np.isfinite(ndvi)
                if t_scl is not None:
                    valid = valid & ~mascara_de_nubes(t_scl.data[0], getattr(entry.scene, "mask_kind", "scl"))
                return ndvi, valid, entry.scene
            except TileOutsideBounds:
                return None            # 100% fuera del footprint → transparente
            except Exception as exc:  # noqa: BLE001 — red transitoria
                last_exc = exc
                # 401/403 → SAS vencido: invalidar el token para que el reabrir
                # del retry re-firme (si no, se reabre con la credencial vieja).
                if _is_auth_error(exc):
                    inv = getattr(self._provider, "invalidate_token", None)
                    if inv:
                        inv()
                # Readers posiblemente corruptos: sácalos del pool para reabrir
                # en el retry. NO se cierran aquí (otro hilo podría tenerlos
                # reservados); el cierre se difiere hasta refs 0 (H2).
                with self._lock:
                    if self._readers.get(scene_id) is entry:
                        del self._readers[scene_id]
                        self._pending_close.append(entry)
            finally:
                self._release(entry)
        raise RuntimeError(f"tesela irrecuperable: {last_exc}")

    def render_tile(self, scene_id: str, z: int, x: int, y: int,
                    rescale: tuple[float, float] = (-1.0, 1.0),
                    index: str = "ndvi", collection: str | None = None) -> bytes:
        """PNG 256px de NDVI para la tesela pedida (con ambos cachés)."""
        from rio_tiler.colormap import cmap
        from rio_tiler.models import ImageData

        lo, hi = rescale
        # el índice va en la clave (el NDVI conserva la de siempre: el caché en disco sigue valiendo)
        key = f"{scene_id}/{z}/{x}/{y}/{lo:.4f}_{hi:.4f}" + ("" if index == "ndvi" else f"/{index}")
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        result = self._read_scene_ndvi(scene_id, z, x, y, index, collection)
        if result is None:
            self._cache_put(key, _TRANSPARENT_PNG, rendered=False)
            return _TRANSPARENT_PNG
        ndvi, valid, _scene = result

        # Stretch dinámico p2–p98 del AOI (rescale) → contraste real. rio-tiler
        # 9.x toma el alpha del .mask del MaskedArray (True = transparente).
        rng = max(1e-6, hi - lo)
        scaled = (np.clip((np.nan_to_num(ndvi) - lo) / rng, 0, 1) * 255).astype("uint8")
        arr = np.ma.MaskedArray(np.expand_dims(scaled, 0),
                                mask=np.expand_dims(~valid, 0))
        png = ImageData(arr).render(img_format="PNG", colormap=cmap.get(INDICES[index]["colormap"]))
        self._cache_put(key, png)
        return png

    def render_diff_tile(self, scene_a: str, scene_b: str, z: int, x: int, y: int,
                         rescale: tuple[float, float] = (-0.5, 0.5)) -> bytes:
        """PNG del cambio de NDVI (scene_b − scene_a) para la tesela z/x/y (M8).

        rio-tiler rasteriza AMBAS escenas al MISMO grid web-mercator 256 al pedir
        la tesela, así que la resta queda alineada sin reproyectar (a diferencia
        del run_change por ventana, que sí necesita reproject_like). Rampa
        divergente rdylgn con rescale simétrico: pérdida=rojo, sin cambio=amarillo,
        ganancia=verde."""
        from rio_tiler.colormap import cmap
        from rio_tiler.models import ImageData

        lo, hi = rescale
        key = f"diff/{scene_a}__{scene_b}/{z}/{x}/{y}/{lo:.4f}_{hi:.4f}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        ra = self._read_scene_ndvi(scene_a, z, x, y)
        rb = self._read_scene_ndvi(scene_b, z, x, y)
        if ra is None or rb is None:   # alguna tesela fuera de su footprint
            self._cache_put(key, _TRANSPARENT_PNG, rendered=False)
            return _TRANSPARENT_PNG
        ndvi_a, valid_a, esc_a = ra
        ndvi_b, valid_b, esc_b = rb

        # MISMA GUARDA QUE run_change (revisión adversa 2026-09-08, media 6):
        # `_read_scene_ndvi` resuelve CUALQUIER id contra el STAC, así que un
        # GET /tiles-diff/{escena_2020}/{escena_2024}/… a mano —o una plantilla
        # de URL de un run_change viejo servida del caché en disco tras un
        # redeploy— pintaba el mapa de cambio sin pasar por ninguna comprobación.
        # El endpoint tiene que rechazar con el mismo criterio que la tool.
        if radiometry(esc_a) != radiometry(esc_b):
            raise EscenasIncompatiblesError(mensaje_incompatibles(esc_a, esc_b))

        diff = ndvi_b - ndvi_a
        valid = valid_a & valid_b & np.isfinite(diff)

        rng = max(1e-6, hi - lo)
        scaled = (np.clip((np.nan_to_num(diff) - lo) / rng, 0, 1) * 255).astype("uint8")
        arr = np.ma.MaskedArray(np.expand_dims(scaled, 0),
                                mask=np.expand_dims(~valid, 0))
        png = ImageData(arr).render(img_format="PNG", colormap=cmap.get("rdylgn"))
        self._cache_put(key, png)
        return png

    def _read_rgb(self, scene_id: str, combo: str, z: int, x: int, y: int):  # noqa: PLR0912
        """RGB (3,H,W) uint8 + máscara de validez para el composite `combo`, o None
        si la tesela cae fuera. `true_color` usa el TCI `visual` (ya 8-bit); el
        resto compone 3 bandas en reflectancia con un estiramiento fijo (sin costuras). Comparte el pool
        con refcount (H2) y el re-firmado en 401/403 (M4)."""
        import rasterio
        from rio_tiler.errors import TileOutsideBounds

        last_exc = None
        for _attempt in (1, 2):
            entry = self._acquire(scene_id)   # KeyError irresoluble propaga
            scene_lock = self._get_scene_lock(scene_id)
            visual_tile = composite_tiles = None
            try:
                with scene_lock.take() as lane, rasterio.Env(**_GDAL_ENV):   # timeout GDAL (B4)
                    if combo == "true_color" and _band_href(entry.scene, "visual"):
                        vis = self._ensure_band(entry, "visual", lane)
                        visual_tile = vis.tile(x, y, z, tilesize=_TILESIZE)
                    else:
                        names = _COMPOSITES.get(combo)
                        if names is None:
                            raise RuntimeError(f"combinación desconocida: {combo}")
                        readers = [self._ensure_band(entry, b, lane) for b in names]
                        if any(r is None for r in readers):
                            return None   # la escena no trae esas bandas
                        composite_tiles = [r.tile(x, y, z, tilesize=_TILESIZE)
                                           for r in readers]
                if visual_tile is not None:
                    # TCI: 3 bandas uint8 ya estiradas → directo (footprint→alpha).
                    return visual_tile.data[:3].astype("uint8"), (visual_tile.mask != 0)
                valid = None
                chans = []
                for t in composite_tiles:
                    a = t.data[0].astype("float32")
                    m = (t.mask != 0)
                    valid = m if valid is None else (valid & m)
                    chans.append(a)
                rgb = np.stack([_estirar_banda(a, valid, scene_scaling(entry.scene, b))
                                for a, b in zip(chans, names, strict=True)], axis=0)
                return rgb, valid
            except TileOutsideBounds:
                return None
            except Exception as exc:  # noqa: BLE001 — red transitoria
                last_exc = exc
                if _is_auth_error(exc):
                    inv = getattr(self._provider, "invalidate_token", None)
                    if inv:
                        inv()
                with self._lock:
                    if self._readers.get(scene_id) is entry:
                        del self._readers[scene_id]
                        self._pending_close.append(entry)
            finally:
                self._release(entry)
        raise RuntimeError(f"tesela RGB irrecuperable: {last_exc}")

    def render_rgb_tile(self, scene_id: str, combo: str, z: int, x: int, y: int) -> bytes:
        """PNG de una composición RGB (color real u otra combinación de bandas)."""
        from rio_tiler.models import ImageData

        key = f"rgb/{scene_id}/{combo}/{z}/{x}/{y}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        result = self._read_rgb(scene_id, combo, z, x, y)
        if result is None:
            self._cache_put(key, _TRANSPARENT_PNG, rendered=False)
            return _TRANSPARENT_PNG
        rgb, valid = result   # (3,H,W) uint8, (H,W) bool
        arr = np.ma.MaskedArray(rgb, mask=np.broadcast_to(~valid, rgb.shape))
        png = ImageData(arr).render(img_format="PNG")
        self._cache_put(key, png)
        return png
