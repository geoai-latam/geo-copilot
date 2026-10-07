"""Catálogo Sentinel-2 en GeoParquet estático, consultado con DuckDB (explorador S2).

Fuente: `tge-labs/s2-stac-geoparquet` en Source Cooperative — cada escena de Earth Search
`sentinel-2-c1-l2a`, un archivo por año (`year=YYYY/items.parquet`) más la cola del mes
(`live-MM.parquet`), ordenado por (tesela MGRS, fecha) y con estadísticas geo por grupo de
filas. DuckDB lo lee por HTTP range: solo baja los grupos de filas que el filtro admite.

Para qué, frente al STAC: lo que el STAC no da — estadísticas por tesela MGRS en una ventana
de fechas (el globo coloreado del explorador) y listas largas de escenas con miniatura. La
búsqueda por AOI de las tools de cálculo sigue en el STAC.

Medido 2026-10-06 desde la red de dev: Colombia entera, 3 meses (292 teselas, 11.573
escenas) en 19 s en frío y 0,5 s con la conexión caliente; por eso la conexión vive en el
proceso y `precalentar` lee los pies de los archivos al arrancar.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date
from typing import Any

import httpx

_BASE = "https://data.source.coop/tge-labs/s2-stac-geoparquet/sentinel-2-c1-l2a"
# Listado S3 del bucket público (el glob `year=*/*.parquet` no funciona sobre https).
_LISTADO = "https://data.source.coop/tge-labs/s2-stac-geoparquet/?prefix=sentinel-2-c1-l2a/year={anio}/"
_LISTADO_TTL_S = 3600.0
_HTTP_TIMEOUT = 30.0

#: Id de escena de Collection 1: S2B_T18NWL_20260810T152745_L2A (tesela y fecha dentro).
ID_C1 = re.compile(r"^S2[A-Z]_T(?P<tile>\d{2}[A-Z]{3})_(?P<fecha>\d{8})T\d{6}_L2A$")
_TILE = re.compile(r"^\d{2}[A-Z]{3}$")

ORDENES = {
    "menos_nubes": "nubes ASC, fecha DESC",
    "mas_cobertura": "cobertura DESC, nubes ASC",
    "reciente": "fecha DESC",
}

# Una fila por escena: las partes (archivo del año y cola del mes) solo se solapan en una
# escena reprocesada, que conserva su id con un `s2:generation_time` más nuevo.
# Se filtra en un CTE MATERIALIZED y se deduplica después: con el QUALIFY sobre la lectura,
# DuckDB 1.5 lo reescribe como semi-join por número de fila y la rama que trae las columnas
# lee el parquet entero, sin podar (medido 2026-10-07: >200 s frente a ~20 s en frío).
_ESCENAS = """
WITH filtradas AS MATERIALIZED (
SELECT id, _tile AS tile, datetime AS fecha, "eo:cloud_cover" AS nubes,
       100 - coalesce("s2:nodata_pixel_percentage", 0) AS cobertura,
       platform AS plataforma, thumbnail_url AS miniatura, geometry, "s2:generation_time" AS gen
FROM read_parquet({archivos}, union_by_name = true)
WHERE {donde})
SELECT * EXCLUDE (gen) FROM filtradas
QUALIFY row_number() OVER (PARTITION BY id ORDER BY gen DESC) = 1
"""


# `&&` (cruce de extensiones) es lo que DuckDB poda con las estadísticas `geo_bbox` de cada
# grupo de filas; ST_Intersects a secas no poda y baja la geometría de los 4,7 GB del año
# (medido 2026-10-07: 768 s frente a 10 s en frío). ST_Intersects queda para el corte exacto.
_EN_BBOX = ("geometry && ST_MakeEnvelope(?, ?, ?, ?) "
            "AND ST_Intersects(geometry, ST_MakeEnvelope(?, ?, ?, ?))")


class CatalogoError(ValueError):
    """Error honesto del catálogo (apto para el LLM)."""


class Catalogo:
    """Lector del GeoParquet con UNA conexión DuckDB por proceso (sus cachés HTTP la hacen
    rápida tras la primera consulta). Cada consulta usa su propio cursor: DuckDB permite
    cursores concurrentes sobre una conexión."""

    def __init__(self, base: str = _BASE, listar=None) -> None:
        self._base = base.rstrip("/")
        self._listar = listar or self._listar_remoto
        self._lock = threading.Lock()
        self._con: Any = None
        self._listados: dict[int, tuple[float, list[str]]] = {}

    # -- conexión y archivos ---------------------------------------------------------------
    def _conexion(self):
        with self._lock:
            if self._con is None:
                import duckdb

                con = duckdb.connect()
                ext = os.environ.get("DUCKDB_EXTENSION_DIR")
                if ext:   # en la imagen Docker las extensiones vienen instaladas aquí
                    con.execute(f"SET extension_directory = '{ext}'")
                con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
                # GLOBAL: cada consulta usa un cursor, y un cursor no hereda los SET de sesión
                # (con SET a secas las fechas salían y se filtraban en la hora local).
                con.execute("SET GLOBAL TimeZone = 'UTC'; SET GLOBAL enable_http_metadata_cache = true;")
                self._con = con
            return self._con.cursor()

    def _listar_remoto(self, anio: int) -> list[str]:
        """Nombres de los .parquet del año según el listado S3 (archivo del año + colas)."""
        resp = httpx.get(_LISTADO.format(anio=anio), timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        claves = re.findall(r"<Key>([^<]+\.parquet)</Key>\s*<LastModified>[^<]*</LastModified>"
                            r"\s*<ETag>[^<]*</ETag>\s*<Size>(\d+)</Size>", resp.text)
        # `live.parquet` viejo queda publicado con 0 filas: se puede leer, pero no aporta.
        return [k.rsplit("/", 1)[-1] for k, tam in claves if int(tam) > 0]

    def archivos(self, anio: int) -> list[str]:
        ahora = time.time()
        with self._lock:
            hit = self._listados.get(anio)
            if hit and ahora - hit[0] < _LISTADO_TTL_S:
                return hit[1]
        nombres = self._listar(anio)
        rutas = [f"{self._base}/year={anio}/{n}" for n in sorted(nombres)]
        with self._lock:
            self._listados[anio] = (ahora, rutas)
        return rutas

    def _fuente(self, desde: str, hasta: str) -> str:
        anios = range(date.fromisoformat(desde).year, date.fromisoformat(hasta).year + 1)
        rutas = [r for a in anios for r in self.archivos(a)]
        if not rutas:
            raise CatalogoError(f"el catálogo no tiene escenas entre {desde} y {hasta}")
        return "[" + ", ".join("'" + r.replace("'", "''") + "'" for r in rutas) + "]"

    def precalentar(self, anios: list[int]) -> None:
        """Lee en segundo plano los pies de los archivos de esos años (best-effort)."""
        def _trabajo() -> None:
            try:
                for a in anios:
                    for r in self.archivos(a):
                        self._conexion().execute("SELECT count(*) FROM parquet_metadata(?)", [r])
            except Exception:  # noqa: BLE001 — optimización: si falla, la 1.ª consulta paga
                return
        threading.Thread(target=_trabajo, daemon=True, name="catalogo-precalentar").start()

    # -- consultas -------------------------------------------------------------------------
    @staticmethod
    def _filtros(desde: str, hasta: str, max_nubes: float | None,
                 min_cobertura: float | None) -> tuple[list[str], list[Any]]:
        donde = ["datetime >= ?::TIMESTAMPTZ", "datetime < (?::DATE + 1)::TIMESTAMPTZ"]
        params: list[Any] = [desde, hasta]
        if max_nubes is not None:
            donde.append('"eo:cloud_cover" <= ?')
            params.append(float(max_nubes))
        if min_cobertura is not None:
            donde.append('100 - coalesce("s2:nodata_pixel_percentage", 0) >= ?')
            params.append(float(min_cobertura))
        return donde, params

    def cuadricula(self, bbox: tuple[float, float, float, float], desde: str, hasta: str,
                   max_nubes: float | None = None, min_cobertura: float | None = None) -> list[dict]:
        """Una fila por tesela MGRS que toca el bbox, con las escenas que pasan los filtros:
        cuántas, la más despejada, nubes mínima y mediana, cobertura máxima y su huella."""
        donde, params = self._filtros(desde, hasta, max_nubes, min_cobertura)
        donde.insert(0, _EN_BBOX)
        params = [*bbox, *bbox, *params]
        sql = f"""
WITH s AS ({_ESCENAS.format(archivos=self._fuente(desde, hasta), donde=" AND ".join(donde))})
SELECT tile, count(*) AS escenas, round(min(nubes), 2) AS nubes_min,
       round(median(nubes), 2) AS nubes_mediana, round(max(cobertura), 2) AS cobertura_max,
       arg_min(id, nubes) AS mejor_escena, strftime(arg_min(fecha, nubes), '%Y-%m-%d') AS mejor_fecha,
       ST_AsGeoJSON(arg_max(geometry, cobertura)) AS huella
FROM s GROUP BY tile ORDER BY tile"""
        filas = self._conexion().execute(sql, params).fetchall()
        cols = ("tile", "escenas", "nubes_min", "nubes_mediana", "cobertura_max",
                "mejor_escena", "mejor_fecha", "huella")
        out = [dict(zip(cols, f, strict=True)) for f in filas]
        for d in out:
            d["huella"] = json.loads(d["huella"]) if d["huella"] else None
        return out

    def escenas(self, desde: str, hasta: str, *, tile: str | None = None,
                bbox: tuple[float, float, float, float] | None = None,
                max_nubes: float | None = None, min_cobertura: float | None = None,
                orden: str = "menos_nubes", limite: int = 50) -> list[dict]:
        """Escenas de una tesela MGRS (o que tocan un bbox) con fecha, nubes, cobertura y
        miniatura, en el orden pedido."""
        if orden not in ORDENES:
            raise CatalogoError(f"orden desconocido: {orden!r}; válidos: {', '.join(ORDENES)}")
        if tile is None and bbox is None:
            raise CatalogoError("hace falta una tesela MGRS o un área")
        donde, params = self._filtros(desde, hasta, max_nubes, min_cobertura)
        if tile is not None:
            if not _TILE.match(tile):
                raise CatalogoError(f"tesela MGRS inválida: {tile!r} (p. ej. 18NWL)")
            donde.insert(0, "_tile = ?")
            params.insert(0, tile)
        if bbox is not None:
            donde.insert(0, _EN_BBOX)
            params = [*bbox, *bbox, *params]
        sql = f"""
SELECT id, tile, strftime(fecha, '%Y-%m-%dT%H:%M:%SZ') AS fecha, round(nubes, 2) AS nubes,
       round(cobertura, 2) AS cobertura, plataforma, miniatura
FROM ({_ESCENAS.format(archivos=self._fuente(desde, hasta), donde=" AND ".join(donde))})
ORDER BY {ORDENES[orden]} LIMIT {max(1, min(int(limite), 200))}"""
        filas = self._conexion().execute(sql, params).fetchall()
        cols = ("id", "tile", "fecha", "nubes", "cobertura", "plataforma", "miniatura")
        return [dict(zip(cols, f, strict=True)) for f in filas]

    def item(self, scene_id: str) -> dict | None:
        """La escena como item STAC (id, bbox, properties, assets) o None si no está. Barato:
        el id trae la tesela y la fecha, que son el orden del archivo."""
        m = ID_C1.match(scene_id)
        if not m:
            return None
        f = m.group("fecha")
        dia = f"{f[:4]}-{f[4:6]}-{f[6:]}"
        try:
            fuente = self._fuente(dia, dia)
        except CatalogoError:   # el catálogo no cubre ese año: la escena no está
            return None
        sql = f"""
SELECT id, bbox, strftime(datetime, '%Y-%m-%dT%H:%M:%SZ'), "eo:cloud_cover",
       "s2:processing_baseline", assets
FROM read_parquet({fuente}, union_by_name = true)
WHERE _tile = ? AND id = ?
ORDER BY "s2:generation_time" DESC LIMIT 1"""
        fila = self._conexion().execute(sql, [m.group("tile"), scene_id]).fetchone()
        if fila is None:
            return None
        return {
            "id": fila[0], "bbox": list(fila[1] or []),
            "properties": {"datetime": fila[2], "eo:cloud_cover": fila[3],
                           "s2:processing_baseline": fila[4]},
            "assets": json.loads(fila[5] or "{}"),
        }


class ConCatalogo:
    """El proveedor STAC de siempre + las escenas del catálogo.

    Una escena del catálogo (id de Collection 1) se resuelve contra el GeoParquet, así sirve a
    las tools de cálculo (`scene_id`) y a las teselas aunque el proveedor sea Planetary
    Computer. Sus COG están en un bucket público de AWS: no se firman. Todo lo demás pasa
    tal cual al proveedor."""

    def __init__(self, inner, catalogo: Catalogo) -> None:
        self._inner = inner
        self.catalogo = catalogo

    def __getattr__(self, nombre: str):
        return getattr(self._inner, nombre)

    def get_scene(self, scene_id: str, collection: str | None = None):
        if ID_C1.match(scene_id):
            from imagery_mcp.providers import COLECCIONES, parse_item

            item = self.catalogo.item(scene_id)
            return parse_item(item, COLECCIONES["sentinel-2-c1-l2a"], "earth-search") if item else None
        kw = {"collection": collection} if collection else {}
        return self._inner.get_scene(scene_id, **kw)

    def sign(self, href: str) -> str:
        if href.startswith("https://e84-earth-search-sentinel-data.s3."):
            return href
        return str(self._inner.sign(href))
