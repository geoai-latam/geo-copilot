"""Teselas de UNA banda (reflectancia en gris, la clasificación SCL, la probabilidad de nubes o
nieve) y el color con estiramiento por canal: lo que pide el explorador al ver una escena
entera y ajustar su contraste.

Va aparte de `tiles_render` (que ya pasa de 350 líneas): misma lectura por carriles y mismos
reintentos, generalizados a N bandas.
"""

from __future__ import annotations

import numpy as np

from imagery_mcp.engine import _GDAL_ENV, _is_auth_error, apply_scaling, scene_scaling
from imagery_mcp.tiles_render import _COMPOSITES, _TILESIZE, _TRANSPARENT_PNG

#: Clasificación de escena de ESA (SCL de Sentinel-2 L2A): valor → (etiqueta, color). Los
#: colores son los de la documentación de ESA, para que se lean igual que en cualquier visor.
CLASES_SCL: dict[int, tuple[str, str]] = {
    1: ("Píxel saturado o defectuoso", "#ff0000"),
    2: ("Zona oscura / sombra topográfica", "#2f2f2f"),
    3: ("Sombra de nube", "#643200"),
    4: ("Vegetación", "#00a000"),
    5: ("Sin vegetación (suelo)", "#ffe65a"),
    6: ("Agua", "#0000ff"),
    7: ("Sin clasificar", "#808080"),
    8: ("Nube, probabilidad media", "#c0c0c0"),
    9: ("Nube, probabilidad alta", "#ffffff"),
    10: ("Cirro fino", "#64c8ff"),
    11: ("Nieve o hielo", "#ff96ff"),
}

#: Bandas que no son reflectancia: su valor ya es lo que se pinta (0–100 %) o una clase.
PROBABILIDADES = ("cloud", "snow")

#: Rango por defecto de cada banda (lo que va de negro a blanco). Reflectancia: 0–0,4 (la
#: ganancia 2,5 habitual); AOT (adimensional) y vapor de agua (g/cm²) en su escala física.
RANGO_DEFECTO: dict[str, tuple[float, float]] = {"aot": (0.0, 1.0), "wvp": (0.0, 5.0)}
RANGO_REFLECTANCIA = (0.0, 0.4)


def rango_de(banda: str) -> tuple[float, float]:
    if banda in PROBABILIDADES:
        return (0.0, 100.0)
    return RANGO_DEFECTO.get(banda, RANGO_REFLECTANCIA)


def _paleta_scl() -> dict[int, tuple[int, int, int, int]]:
    out = {}
    for valor, (_, color) in CLASES_SCL.items():
        h = color.lstrip("#")
        out[valor] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    return out


def _a_uint8(arr: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return (np.clip((np.nan_to_num(arr, nan=lo) - lo) / max(1e-9, hi - lo), 0, 1) * 255).astype("uint8")


class TilesBandasMixin:
    """Se mezcla en `TilePool`: usa sus carriles, readers, cachés y re-firmado."""

    def _leer_teselas(self, scene_id: str, nombres: tuple[str, ...], z: int, x: int, y: int):
        """[(array float32 (H,W)), ...], máscara de validez y escena; None si la tesela cae fuera
        de la escena o la escena no trae alguna de las bandas."""
        import rasterio
        from rio_tiler.errors import TileOutsideBounds

        last_exc = None
        for _attempt in (1, 2):
            entry = self._acquire(scene_id)
            try:
                with self._get_scene_lock(scene_id).take() as lane, rasterio.Env(**_GDAL_ENV):
                    readers = [self._ensure_band(entry, b, lane) for b in nombres]
                    if any(r is None for r in readers):
                        return None
                    teselas = [r.tile(x, y, z, tilesize=_TILESIZE) for r in readers]
                valid = None
                datos = []
                for t in teselas:
                    m = t.mask != 0
                    valid = m if valid is None else (valid & m)
                    datos.append(t.data[0].astype("float32"))
                return datos, valid, entry.scene
            except TileOutsideBounds:
                return None
            except Exception as exc:  # noqa: BLE001 — red transitoria: se reabre y reintenta
                last_exc = exc
                if _is_auth_error(exc) and (inv := getattr(self._provider, "invalidate_token", None)):
                    inv()
                with self._lock:
                    if self._readers.get(scene_id) is entry:
                        del self._readers[scene_id]
                        self._pending_close.append(entry)
            finally:
                self._release(entry)
        raise RuntimeError(f"tesela irrecuperable: {last_exc}")

    def render_band_tile(self, scene_id: str, banda: str, z: int, x: int, y: int,
                         rescale: tuple[float, float] | None = None) -> bytes:
        """PNG de una banda: SCL con la paleta de ESA, probabilidades y reflectancias en gris."""
        from rio_tiler.models import ImageData

        lo, hi = rescale or rango_de(banda)
        key = f"band/{scene_id}/{banda}/{z}/{x}/{y}" + ("" if banda == "scl" else f"/{lo:.4f}_{hi:.4f}")
        if (cached := self._cache_get(key)) is not None:
            return cached
        leido = self._leer_teselas(scene_id, (banda,), z, x, y)
        if leido is None:
            self._cache_put(key, _TRANSPARENT_PNG, rendered=False)
            return _TRANSPARENT_PNG
        (arr,), valid, escena = leido
        if banda == "scl":
            clases = arr.astype("uint8")
            valid = valid & (clases > 0)
            png = ImageData(np.ma.MaskedArray(clases[None], mask=~valid[None])).render(
                img_format="PNG", colormap=_paleta_scl())
        else:
            valores = arr if banda in PROBABILIDADES else apply_scaling(arr, scene_scaling(escena, banda))
            gris = _a_uint8(valores, lo, hi)
            png = ImageData(np.ma.MaskedArray(gris[None], mask=~valid[None])).render(img_format="PNG")
        self._cache_put(key, png)
        return png

    def render_rgb_estirado(self, scene_id: str, combo: str, z: int, x: int, y: int,
                            estiramiento: tuple[tuple[float, float], ...]) -> bytes:
        """Color con un rango de reflectancia por canal (R, G, B), el que eligió quien mira.
        El color real se compone de sus bandas (el TCI ya viene estirado y no se puede ajustar)."""
        from rio_tiler.models import ImageData

        nombres = _COMPOSITES[combo]
        tramo = "_".join(f"{lo:.4f}-{hi:.4f}" for lo, hi in estiramiento)
        key = f"rgb/{scene_id}/{combo}/{z}/{x}/{y}/{tramo}"
        if (cached := self._cache_get(key)) is not None:
            return cached
        leido = self._leer_teselas(scene_id, nombres, z, x, y)
        if leido is None:
            self._cache_put(key, _TRANSPARENT_PNG, rendered=False)
            return _TRANSPARENT_PNG
        datos, valid, escena = leido
        canales = [_a_uint8(apply_scaling(a, scene_scaling(escena, b)), lo, hi)
                   for a, b, (lo, hi) in zip(datos, nombres, estiramiento, strict=True)]
        rgb = np.stack(canales, axis=0)
        png = ImageData(np.ma.MaskedArray(rgb, mask=np.broadcast_to(~valid, rgb.shape))).render(img_format="PNG")
        self._cache_put(key, png)
        return png
