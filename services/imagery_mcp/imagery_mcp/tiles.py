"""Teselado dinámico NDVI/cambio — endpoint HTTP plano (no MCP), cacheable.

Decisión validada (sonda 2026-07-19): tesela fría 114 s en esta red, vecina
0.1 s con el caché VSI caliente ⇒ POOL de Readers abiertos por escena (LRU)
+ caché LRU de teselas PNG ya renderizadas. El navegador consume
``/tiles/{scene_id}/{z}/{x}/{y}.png`` vía el proxy nginx del frontend (que
inyecta la credencial — la key nunca llega al browser).
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager

# F4: el render (NDVI, diferencia, RGB) y sus ayudantes viven en tiles_render (mixin); estos se
# reexportan porque server, operaciones y las pruebas los importan de aquí.
from imagery_mcp.tiles_render import (  # noqa: F401
    _COMPOSITES,
    _TILESIZE,
    _TRANSPARENT_PNG,
    EscenasIncompatiblesError,
    TilesRenderMixin,
    _band_href,
    _stretch_uint8,
)

# Patrón de id de escena: Sentinel-2 (PC: S2A_MSIL2A_…; ES: S2A_18NXL_…) o Landsat 8/9
# (LC08_L2SP_…, LC09_…; T5.5). Alfanumérico/underscore: rechaza junk, espacios y
# traversal (../, /, \) ANTES de tocar el STAC o el nombre de archivo del caché
# — evita amplificación de peticiones y escrituras fuera del directorio (M3).
_SCENE_RE = re.compile(r"^(S2[A-Z]|LC0[89])_[A-Za-z0-9_]{4,120}$")

_READER_POOL_MAX = 4      # escenas calientes simultáneas (2 bandas c/u)
_LANES_PER_SCENE = 6      # teselas de UNA escena que se leen a la vez (_SceneLanes)
_TILE_CACHE_MAX = 512     # teselas PNG renderizadas (~25 KB c/u → ~12 MB)
_SCENE_CACHE_MAX = 256    # escenas registradas + locks por escena (cota anti-leak)
_NEGATIVE_CACHE_MAX = 512  # ids irresolubles recordados (no re-POSTear al STAC)
_DISK_CACHE_MAX = 5000    # teselas PNG en DISCO (~25 KB c/u → ~125 MB); poda LRU
# Caché de teselas en DISCO: sobrevive restarts del proceso (el de memoria no).
import os as _os

_DISK_CACHE_DIR = _os.environ.get("IMAGERY_TILE_CACHE_DIR", "/tmp/ndvi-tiles")


def _close_readers(readers) -> None:
    for r in readers:
        if r is None:   # el reader SCL es opcional (best-effort)
            continue
        try:
            r.close()
        except Exception:  # noqa: BLE001 — cerrar no debe propagar
            pass


class _ReaderEntry:
    """Readers rio-tiler de una escena (por BANDA, abiertos on-demand) + refcount.

    GDAL no es thread-safe: un dataset NO puede leerse mientras otro hilo lo
    cierra. El refcount permite que la evicción LRU DIFIERA el ``close()`` de
    una escena que otro hilo está usando en ``.tile()`` — se cierra recién
    cuando ``refs`` vuelve a 0 (auditoría 2026-07-20, hallazgo H2).

    Invariante: ``refs`` se incrementa antes de tomar un carril de la escena y
    se decrementa al soltarlo; por eso ``refs == 0`` implica que ningún hilo
    está en ningún carril de esa escena ⇒ es seguro cerrar sus readers. Las
    bandas se abren perezosamente con el carril tomado (NDVI usa red/nir/scl;
    los composites RGB, otras) y se cachean en ``readers[carril]``.
    """

    __slots__ = ("readers", "refs", "scene")

    def __init__(self, scene) -> None:
        # Un dict por carril (_SceneLanes): nombre de banda → Reader (perezoso).
        # Cada carril tiene SUS datasets GDAL, así que dos hilos nunca leen el mismo.
        self.readers: list[dict] = [{} for _ in range(_LANES_PER_SCENE)]
        self.refs = 0
        self.scene = scene

    def close_all(self) -> None:
        _close_readers([r for lane in self.readers for r in lane.values()])


class _SceneLanes:
    """Carriles de lectura de una escena: cada uno con SU lock y SUS readers.

    GDAL no es thread-safe por dataset, no por proceso: varios hilos pueden leer
    la misma escena a la vez si cada uno usa sus propios datasets. Con un solo lock
    por escena sus teselas se dibujaban una por una (16 teselas z12 en frío:
    13,8 s con Planetary Computer y 20 s con Earth Search, medido 2026-10-06).
    """

    __slots__ = ("_next", "locks")

    def __init__(self, n: int = _LANES_PER_SCENE) -> None:
        self.locks = [threading.Lock() for _ in range(n)]
        self._next = 0

    def locked(self) -> bool:
        """¿Algún carril en uso? (la poda LRU no evicta una escena activa)."""
        return any(lk.locked() for lk in self.locks)

    @contextmanager
    def take(self) -> Iterator[int]:
        """Toma un carril libre (o espera uno por turnos) y devuelve su índice."""
        for i, lk in enumerate(self.locks):
            if lk.acquire(blocking=False):
                break
        else:
            # Todos ocupados: esperar en uno por turnos. La carrera sobre _next solo
            # reparte peor la espera; cada carril sigue siendo exclusivo por su lock.
            i = self._next % len(self.locks)
            self._next += 1
            lk = self.locks[i]
            lk.acquire()
        try:
            yield i
        finally:
            lk.release()


class TilePool(TilesRenderMixin):
    """Readers rio-tiler calientes por escena + caché de PNGs renderizados."""

    def __init__(self, provider) -> None:
        self._provider = provider
        self._lock = threading.Lock()
        self._readers: OrderedDict[str, _ReaderEntry] = OrderedDict()
        # Escenas expulsadas del pool cuyo close() se difirió porque estaban
        # en uso (refs>0); se cierran al drenar cuando refs vuelve a 0 (H2).
        self._pending_close: list[_ReaderEntry] = []
        # GDAL datasets NO son thread-safe: cada escena tiene carriles con su
        # lock y sus readers (_SceneLanes), así sus teselas se leen en paralelo
        # sin compartir dataset; el caché VSI compartido abarata a las vecinas.
        # OrderedDict → cota LRU.
        self._scene_locks: OrderedDict[str, _SceneLanes] = OrderedDict()
        self._png_cache: OrderedDict[str, bytes] = OrderedDict()
        self._scenes: OrderedDict[str, object] = OrderedDict()  # scene_id → Scene
        # Ids que el STAC NO resuelve: recordarlos evita re-POSTear en cada
        # tesela (amplificación) — un id falso nunca se vuelve real (M3).
        self._negative: OrderedDict[str, None] = OrderedDict()
        # Métricas simples (expuestas en /health) — sin ellas se optimiza a ciegas.
        self.metrics = {"tiles_rendered": 0, "cache_hits_mem": 0,
                        "cache_hits_disk": 0, "tiles_failed": 0}
        self._disk_puts = 0   # contador para el barrido LRU amortizado del disco

    def _disk_path(self, key: str) -> str:
        return _os.path.join(_DISK_CACHE_DIR, key.replace("/", "_") + ".png")

    def _disk_get(self, key: str) -> bytes | None:
        try:
            with open(self._disk_path(key), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def _disk_put(self, key: str, png: bytes) -> None:
        try:
            _os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
            with open(self._disk_path(key), "wb") as fh:
                fh.write(png)
        except OSError:  # disco lleno/permiso: el caché en memoria basta
            return
        # Poda LRU del caché en disco cada N escrituras (barato, amortizado): sin
        # cota, las teselas (x4 combos RGB, z/x/y libre) crecen sin límite → DoS de
        # disco del host (B3 re-auditoría 2026-07-20).
        self._disk_puts += 1
        if self._disk_puts % 256 == 0:
            self._sweep_disk_cache()

    def _sweep_disk_cache(self) -> None:
        """Borra las teselas en disco más viejas (por mtime) sobre _DISK_CACHE_MAX."""
        try:
            with _os.scandir(_DISK_CACHE_DIR) as it:
                files = [(e.stat().st_mtime, e.path) for e in it if e.is_file()]
        except OSError:
            return
        if len(files) <= _DISK_CACHE_MAX:
            return
        files.sort()   # más viejos primero
        for _mt, path in files[: len(files) - _DISK_CACHE_MAX]:
            try:
                _os.remove(path)
            except OSError:  # ya borrado por otro hilo / permiso
                pass

    def register_scene(self, scene) -> None:
        """Las tools registran la escena usada para que /tiles pueda servirla."""
        with self._lock:
            self._scenes[scene.id] = scene
            self._scenes.move_to_end(scene.id)
            self._negative.pop(scene.id, None)   # deja de ser irresoluble
            while len(self._scenes) > _SCENE_CACHE_MAX:
                self._scenes.popitem(last=False)

    def _ensure_band(self, entry: _ReaderEntry, band: str, lane: int = 0):
        """Reader rio-tiler de la banda en el carril, abierto perezoso y cacheado.

        Se llama con el carril ``lane`` tomado (``_SceneLanes.take``), de modo que
        nadie más toca ``entry.readers[lane]`` mientras tanto. El SCL/composite que
        la escena no trae devuelve None (best-effort)."""
        readers = entry.readers[lane]
        r = readers.get(band)
        if r is not None:
            return r
        href = _band_href(entry.scene, band)
        if not href:
            return None
        from rio_tiler.io import Reader
        reader = Reader(self._provider.sign(href) if self._provider else href)
        readers[band] = reader
        return reader

    def _drain_pending_locked(self) -> None:
        """Cierra las escenas diferidas que ya quedaron libres. Con self._lock."""
        if not self._pending_close:
            return
        still = []
        for e in self._pending_close:
            if e.refs <= 0:
                e.close_all()
            else:
                still.append(e)
        self._pending_close = still

    def _evict_locked(self) -> None:
        """Poda el pool al tope LRU sin cerrar readers en uso. Con self._lock."""
        while len(self._readers) > _READER_POOL_MAX:
            _sid, vic = self._readers.popitem(last=False)
            if vic.refs > 0:
                self._pending_close.append(vic)  # en uso: diferir el close (H2)
            else:
                vic.close_all()
        self._drain_pending_locked()

    def _acquire(self, scene_id: str, collection: str | None = None) -> _ReaderEntry:
        """Reserva (refcount++) los readers de la escena, abriéndolos si faltan.

        El caller DEBE llamar ``_release(entry)`` en un ``finally``. Devuelve la
        entrada con ``refs`` ya incrementado, de modo que una evicción LRU
        concurrente no pueda cerrar sus readers mientras se usan.
        """
        with self._lock:
            entry = self._readers.get(scene_id)
            if entry is not None:
                entry.refs += 1
                self._readers.move_to_end(scene_id)
                return entry
            scene = self._scenes.get(scene_id)
            if scene is not None:
                self._scenes.move_to_end(scene_id)
            elif scene_id in self._negative:
                # Ya se comprobó que el STAC no lo resuelve: no re-POSTear (M3).
                raise KeyError(f"escena no registrada ni resoluble: {scene_id}")
        # RESOLUCIÓN PEREZOSA: el registro vive en memoria y un restart lo vacía
        # — 327 teselas devolvieron 404 en vivo por esto. Si el id no está, se
        # resuelve contra el STAC por ID y se registra al vuelo. Fuera del lock.
        if scene is None:
            kw = {"collection": collection} if collection else {}
            scene = self._provider.get_scene(scene_id, **kw) if self._provider else None
            if scene is None:
                with self._lock:
                    self._negative[scene_id] = None
                    self._negative.move_to_end(scene_id)
                    while len(self._negative) > _NEGATIVE_CACHE_MAX:
                        self._negative.popitem(last=False)
                raise KeyError(f"escena no registrada ni resoluble: {scene_id}")
            self.register_scene(scene)
        with self._lock:
            entry = self._readers.get(scene_id)
            if entry is not None:
                entry.refs += 1
                self._readers.move_to_end(scene_id)
            else:
                # Las bandas se abren perezosas bajo el scene_lock (_ensure_band).
                entry = _ReaderEntry(scene)
                entry.refs = 1
                self._readers[scene_id] = entry
            self._evict_locked()
        return entry

    def _release(self, entry: _ReaderEntry) -> None:
        with self._lock:
            entry.refs -= 1
            self._drain_pending_locked()

    def _get_scene_lock(self, scene_id: str) -> _SceneLanes:
        """Carriles de lectura de la escena (_SceneLanes), con cota LRU anti-leak.

        Se pide DESPUÉS de `_acquire`, de modo que un id irresoluble (que ya
        lanzó KeyError) nunca cree un lock huérfano. NUNCA se evicta un lock en
        uso: romper la serialización de una escena activa reintroduciría la race
        GDAL (H2); si el LRU está tomado, se deja pasar del tope transitoriamente.
        """
        with self._lock:
            lk = self._scene_locks.get(scene_id)
            if lk is None:
                lk = _SceneLanes()
                self._scene_locks[scene_id] = lk
            self._scene_locks.move_to_end(scene_id)
            while len(self._scene_locks) > _SCENE_CACHE_MAX:
                old_id, old_lk = next(iter(self._scene_locks.items()))
                if old_lk.locked():
                    break
                self._scene_locks.pop(old_id)
            return lk

    def prewarm(self, scene_id: str, bounds: list[float],
                zooms: tuple[int, ...] = (13, 12, 14), max_tiles: int = 36,
                rescale: tuple[float, float] = (-1.0, 1.0),
                index: str = "ndvi", collection: str | None = None) -> None:
        """PRE-CALIENTA en background las teselas del AOI (fire-and-forget).

        Al terminar el tool NDVI, cuando el usuario mira el mapa las teselas
        z12-14 de su zona YA están en el caché PNG (~0 ms). Acotado a
        max_tiles y silencioso ante fallos de red (es una optimización).
        """
        def _work() -> None:
            done = 0
            for z in zooms:
                for x, y in _tiles_in(bounds, z):
                    if done >= max_tiles:
                        return
                    try:
                        self.render_tile(scene_id, z, x, y, rescale=rescale,
                                         index=index, collection=collection)
                        done += 1
                        # Ceder el lock: las teselas del USUARIO tienen prioridad
                        # (sin esto el prewarm monopolizaba la escena ~40s).
                        import time as _time
                        _time.sleep(0.15)
                    except Exception:  # noqa: BLE001 — best-effort
                        return
        threading.Thread(target=_work, daemon=True, name=f"prewarm-{scene_id[:20]}").start()

    def _cache_get(self, key: str) -> bytes | None:
        """Tesela cacheada (memoria → disco), o None si falta en ambos."""
        with self._lock:
            if key in self._png_cache:
                self._png_cache.move_to_end(key)
                self.metrics["cache_hits_mem"] += 1
                return self._png_cache[key]
        disk = self._disk_get(key)
        if disk is not None:
            with self._lock:
                self._png_cache[key] = disk
                self.metrics["cache_hits_disk"] += 1
            return disk
        return None

    def _cache_put(self, key: str, png: bytes, *, rendered: bool = True) -> None:
        """Guarda la tesela en memoria (LRU); si `rendered`, también en disco."""
        with self._lock:
            self._png_cache[key] = png
            if rendered:
                self.metrics["tiles_rendered"] += 1
            while len(self._png_cache) > _TILE_CACHE_MAX:
                self._png_cache.popitem(last=False)
        if rendered:
            self._disk_put(key, png)

    def record_tile_failure(self) -> None:
        """IMG (auditoría): registra una tesela fallida para /metrics. Antes la
        métrica ``tiles_failed`` estaba declarada pero nunca se incrementaba, así
        que /metrics siempre reportaba 0 aunque el pool devolviera 502."""
        with self._lock:
            self.metrics["tiles_failed"] += 1


def parse_tile_path(path: str) -> tuple[str, int, int, int] | None:
    """``/tiles/{scene_id}/{z}/{x}/{y}.png`` → (scene_id, z, x, y) o None."""
    if not path.startswith("/tiles/"):
        return None
    parts = path[len("/tiles/"):].split("/")
    if len(parts) != 4 or not parts[3].endswith(".png"):
        return None
    try:
        return (parts[0], int(parts[1]), int(parts[2]), int(parts[3][:-4]))
    except ValueError:
        return None


def parse_diff_tile_path(path: str) -> tuple[str, str, int, int, int] | None:
    """``/tiles-diff/{a}/{b}/{z}/{x}/{y}.png`` → (a, b, z, x, y) o None (M8)."""
    if not path.startswith("/tiles-diff/"):
        return None
    parts = path[len("/tiles-diff/"):].split("/")
    if len(parts) != 5 or not parts[4].endswith(".png"):
        return None
    try:
        return (parts[0], parts[1], int(parts[2]), int(parts[3]), int(parts[4][:-4]))
    except ValueError:
        return None


def parse_rgb_tile_path(path: str) -> tuple[str, str, int, int, int] | None:
    """``/tiles-rgb/{scene}/{combo}/{z}/{x}/{y}.png`` → (scene, combo, z, x, y)."""
    if not path.startswith("/tiles-rgb/"):
        return None
    parts = path[len("/tiles-rgb/"):].split("/")
    if len(parts) != 5 or not parts[4].endswith(".png"):
        return None
    if parts[1] not in _COMPOSITES:
        return None
    try:
        return (parts[0], parts[1], int(parts[2]), int(parts[3]), int(parts[4][:-4]))
    except ValueError:
        return None


def _tiles_in(bounds: list[float], z: int) -> list[tuple[int, int]]:
    """Índices XYZ que cubren el bbox 4326 en el zoom z."""
    import math
    n = 2 ** z
    def tx(lon): return int((lon + 180) / 360 * n)
    def ty(lat):
        lat = max(-85.05, min(85.05, lat))
        return int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    x0, x1 = tx(bounds[0]), tx(bounds[2])
    y0, y1 = ty(bounds[3]), ty(bounds[1])
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
