"""Rutas HTTP de teselas del servicio (fuera del protocolo MCP): NDVI e índices, cambio entre
fechas, color (con contraste por canal opcional) y bandas sueltas. Heredan el scope de
imagery_ndvi y pesan 0.05 en el límite de peticiones.

Salieron de `server.py` (pasaba de 500 líneas con las tools del explorador).
"""

from __future__ import annotations

import logging

from geo_mcp_kit import ExtraRoute, respond_json, respond_png

from imagery_mcp import resultados
from imagery_mcp.tiles import (
    _SCENE_RE,
    EscenasIncompatiblesError,
    TilePool,
    parse_diff_tile_path,
    parse_rgb_tile_path,
    parse_tile_path,
)
from imagery_mcp.tiles_bandas import rango_de
from imagery_mcp.vista import BANDAS

logger = logging.getLogger("imagery_mcp")

#: Reflectancia admisible en el contraste por canal (la superficie no pasa de ~1,5).
_LIMITES_CANAL = (-0.2, 2.0)


def parse_band_tile_path(path: str) -> tuple[str, str, int, int, int] | None:
    """``/tiles-band/{scene}/{banda}/{z}/{x}/{y}.png`` → (scene, banda, z, x, y)."""
    if not path.startswith("/tiles-band/"):
        return None
    parts = path[len("/tiles-band/"):].split("/")
    if len(parts) != 5 or not parts[4].endswith(".png") or parts[1] not in BANDAS or parts[1] == "visual":
        return None
    try:
        return (parts[0], parts[1], int(parts[2]), int(parts[3]), int(parts[4][:-4]))
    except ValueError:
        return None


def _parse_canales(scope) -> tuple[tuple[float, float], ...] | None:
    """``?r=lo,hi&g=lo,hi&b=lo,hi`` (reflectancia) → los tres rangos, o None si falta o no vale."""
    from urllib.parse import parse_qs

    q = parse_qs(scope.get("query_string", b"").decode())
    out = []
    for c in "rgb":
        try:
            lo, hi = (float(v) for v in (q.get(c) or [""])[0].split(","))
        except ValueError:
            return None
        if not (_LIMITES_CANAL[0] <= lo < hi <= _LIMITES_CANAL[1]):
            return None
        out.append((lo, hi))
    return tuple(out)


def _parse_rescale(scope, default: tuple[float, float] = (-1.0, 1.0),
                   limites: tuple[float, float] = (-1.0, 1.0)):
    """``?rescale=lo,hi`` con lo<hi dentro de `limites` ([-1,1] en los índices); malformado → default."""
    rescale = default
    try:
        from urllib.parse import unquote
        q = scope.get("query_string", b"").decode()
        for part in q.split("&"):
            if part.startswith("rescale="):
                # unquote: la coma URL-encoded (%2C) no debe caer al default en
                # silencio (divergía de la tesela precalentada).
                lo_s, hi_s = unquote(part[len("rescale="):]).split(",")
                lo_f, hi_f = float(lo_s), float(hi_s)
                if limites[0] <= lo_f < hi_f <= limites[1]:
                    rescale = (lo_f, hi_f)
    except (ValueError, IndexError):
        pass
    return rescale


def _parse_indice(scope) -> tuple[str, str | None] | None:
    """``?index=…&collection=…`` de una tesela de índice (T5.5). None si alguno no es válido;
    sin ellos, NDVI de la colección por defecto (las URLs de siempre)."""
    from urllib.parse import parse_qs

    from imagery_mcp.engine import INDICES
    from imagery_mcp.providers import COLECCIONES

    q = parse_qs(scope.get("query_string", b"").decode())
    index = (q.get("index") or ["ndvi"])[0]
    collection = (q.get("collection") or [None])[0]
    if index not in INDICES or (collection is not None and collection not in COLECCIONES):
        return None
    return index, collection


def _ruta_banda(pool: TilePool):
    """Tesela de una banda suelta (reflectancia, SCL o probabilidad)."""
    import anyio

    async def banda(scope, send, bt, _key):
        if not _SCENE_RE.match(bt[0]):
            await respond_json(send, 400, {"error": "scene_id con formato inválido"})
            return
        lo, hi = rango_de(bt[1])
        rescale = _parse_rescale(scope, default=(lo, hi), limites=(-1.0, max(100.0, hi)))
        try:
            png = await anyio.to_thread.run_sync(lambda: pool.render_band_tile(*bt, rescale=rescale))
        except KeyError:
            await respond_json(send, 404, {"error": "escena no resoluble"})
            return
        except Exception as exc:  # noqa: BLE001 — red flaky: error honesto
            logger.warning(f"band tile {bt} falló: {exc}")
            pool.record_tile_failure()
            await respond_json(send, 502, {"error": "tesela de banda no disponible"})
            return
        await respond_png(send, png)

    return banda


def _ruta_ndvi(pool: TilePool):
    """Teselas NDVI / índice de una escena."""
    import anyio

    async def ndvi(scope, send, tile, _key):
        if not _SCENE_RE.match(tile[0]):
            await respond_json(send, 400, {"error": "scene_id con formato inválido"})
            return
        rescale = _parse_rescale(scope)
        pedido = _parse_indice(scope)
        if pedido is None:
            await respond_json(send, 400, {"error": "índice o colección no válidos"})
            return
        index, collection = pedido
        try:
            png = await anyio.to_thread.run_sync(
                lambda: pool.render_tile(*tile, rescale=rescale, index=index, collection=collection))
        except KeyError:
            await respond_json(send, 404, {"error": "escena no registrada; ejecuta antes imagery_ndvi/zonal"})
            return
        except Exception as exc:  # noqa: BLE001 — red flaky: error honesto
            logger.warning(f"tile {tile} falló: {exc}")
            pool.record_tile_failure()
            await respond_json(send, 502, {"error": "tesela no disponible"})
            return
        await respond_png(send, png)

    return ndvi


def _ruta_diff(pool: TilePool):
    """Teselas del cambio de NDVI entre dos escenas."""
    import anyio

    async def diff(scope, send, dtile, _key):
        if not (_SCENE_RE.match(dtile[0]) and _SCENE_RE.match(dtile[1])):
            await respond_json(send, 400, {"error": "scene_id con formato inválido"})
            return
        rescale = _parse_rescale(scope, default=(-0.5, 0.5))
        try:
            png = await anyio.to_thread.run_sync(lambda: pool.render_diff_tile(*dtile, rescale=rescale))
        except KeyError:
            await respond_json(send, 404, {"error": "alguna escena del cambio no está disponible"})
            return
        except EscenasIncompatiblesError as exc:
            # 409: la petición es válida pero el cálculo que pide sería falso.
            await respond_json(send, 409, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 — red flaky: error honesto
            logger.warning(f"diff tile {dtile} falló: {exc}")
            pool.record_tile_failure()
            await respond_json(send, 502, {"error": "tesela de cambio no disponible"})
            return
        await respond_png(send, png)

    return diff


def _ruta_rgb(pool: TilePool):
    """Teselas de color (con contraste por canal opcional)."""
    import anyio

    async def rgb(scope, send, rgbt, _key):
        if not _SCENE_RE.match(rgbt[0]):
            await respond_json(send, 400, {"error": "scene_id con formato inválido"})
            return
        estiramiento = _parse_canales(scope)
        try:
            png = await anyio.to_thread.run_sync(
                lambda: pool.render_rgb_estirado(*rgbt, estiramiento) if estiramiento else pool.render_rgb_tile(*rgbt))
        except KeyError:
            await respond_json(send, 404, {"error": "escena no registrada; ejecuta antes imagery_composite"})
            return
        except Exception as exc:  # noqa: BLE001 — red flaky: error honesto
            logger.warning(f"rgb tile {rgbt} falló: {exc}")
            pool.record_tile_failure()
            await respond_json(send, 502, {"error": "tesela RGB no disponible"})
            return
        await respond_png(send, png)

    return rgb


def rutas_de_teselas(pool: TilePool) -> tuple[ExtraRoute, ...]:
    """Teselas NDVI, de cambio, RGB y de banda, y los resultados grandes: heredan el scope de su
    tool y pesan poco en el límite de peticiones."""
    return (
        ExtraRoute(resultados.parse_ruta, resultados.servir, requires_tool="imagery_catalog_world", weight=0.2),
        ExtraRoute(parse_band_tile_path, _ruta_banda(pool), requires_tool="imagery_ndvi", weight=0.05),
        ExtraRoute(parse_tile_path, _ruta_ndvi(pool), requires_tool="imagery_ndvi", weight=0.05),
        ExtraRoute(parse_diff_tile_path, _ruta_diff(pool), requires_tool="imagery_ndvi", weight=0.05),
        ExtraRoute(parse_rgb_tile_path, _ruta_rgb(pool), requires_tool="imagery_ndvi", weight=0.05),
    )
