"""Proveedores STAC de imagery óptica (Sentinel-2 L2A y Landsat C2 L2) detrás de una interfaz única.

T5.5: la colección se elige POR PETICIÓN (`COLECCIONES`): cada una declara sus bandas, su
máscara de nubes (SCL de Sentinel-2 o bits de `qa_pixel` de Landsat) y si su factor de
reflectancia se puede derivar del baseline de ESA (solo Sentinel-2; Landsat lo publica en cada
asset). El resto del motor (índices, máscara, teselas) trabaja con nombres canónicos de banda.

Evidencia (sondas 2026-07-19 desde la red del despliegue):
- Planetary Computer (Azure): búsqueda 2.9 s; lectura de banda 2–12 s → DEFAULT.
- Earth Search (Element84 / AWS us-west-2): búsqueda 0.4 s pero lecturas de
  ~131 s/banda con truncados frecuentes desde esta red → FALLBACK.

Cada proveedor normaliza: ids de assets (PC usa B04/B08; ES usa red/nir),
firma de URLs (PC exige SAS; ES es público) y el shape de las escenas.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger("imagery_mcp")

# F4: el escalado radiométrico vive en escalado.py; se reexporta porque engine, operaciones y
# radiometria lo importan de aquí.
from imagery_mcp.escalado import (  # noqa: F401
    _BOA_ADD_OFFSET,
    _HTTP_TIMEOUT,
    _IDENTITY_SCALING,
    _QUANTIFICATION_VALUE,
    _SRC_ASSET,
    _SRC_BASELINE,
    _SRC_COLECCION,
    _SRC_IDENTIDAD,
    BOA_OFFSET_BASELINE,
    BOA_OFFSET_FECHA,
    BandScaling,
    _asset_scaling,
    _fetch_collection_scaling,
    _resolve_scaling,
    _scaling_from_assets,
    baseline_value,
    scaling_from_baseline,
)

_PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
_PC_SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
_ES_STAC = "https://earth-search.aws.element84.com/v1/search"

_COLLECTION = "sentinel-2-l2a"
_PC_COLLECTION_DOC = (
    "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a"
)
_ES_COLLECTION_DOC = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a"


@dataclass
class Scene:
    """Escena Sentinel-2 normalizada entre proveedores."""

    id: str
    datetime: str
    cloud_pct: float
    bbox: list[float]                 # [minx, miny, maxx, maxy] EPSG:4326
    red_href: str
    nir_href: str
    provider: str
    scl_href: str | None = None       # máscara de clasificación (v2: nubes)
    # Nombre canónico de banda → href, para composición RGB (color real y combos).
    # Claves: red, green, blue, nir, swir16, swir22, visual (las que existan).
    bands: dict = field(default_factory=dict)
    # Nombre canónico de banda → BandScaling leído de `raster:bands` del asset.
    # Sin esto el motor entregaba el DN crudo y todo NDVI posterior a enero de
    # 2022 salía ~0,20 bajo (auditoría 2026-09-08, §1.3).
    scaling: dict = field(default_factory=dict)
    # `s2:processing_baseline` ('02.14', '04.00', '05.11'…). Es el dato que dice
    # si el producto trae BOA_ADD_OFFSET; se guarda para poder RECHAZAR una
    # comparación entre dos escenas radiométricamente incompatibles (§1.3).
    processing_baseline: str | None = None
    # T5.5: colección STAC y cómo se lee su máscara de nubes ("scl" | "qa_pixel"). La
    # máscara vive en `scl_href` (nombre histórico) sea cual sea la colección.
    collection: str = "sentinel-2-l2a"
    mask_kind: str = "scl"

    def scaling_for(self, band: str) -> BandScaling:
        """Factor de la banda, o la identidad si el asset no publica ninguno."""
        return self.scaling.get(band) or _IDENTITY_SCALING

    def contains(self, bbox: tuple[float, float, float, float]) -> bool:
        b = self.bbox
        return (b[0] <= bbox[0] and b[1] <= bbox[1]
                and b[2] >= bbox[2] and b[3] >= bbox[3])

    def coverage_pct(self, bbox: tuple[float, float, float, float]) -> float:
        """% del AOI cubierto por el bbox de la escena (aprox planar, hecho)."""
        b = self.bbox
        ix = max(0.0, min(b[2], bbox[2]) - max(b[0], bbox[0]))
        iy = max(0.0, min(b[3], bbox[3]) - max(b[1], bbox[1]))
        aoi = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        return round(100.0 * (ix * iy) / aoi, 1) if aoi > 0 else 0.0


class StacProvider(Protocol):
    name: str

    def search(
        self, aoi_geojson: dict, date_from: str, date_to: str,
        max_cloud_pct: float, limit: int,
    ) -> list[Scene]: ...

    def sign(self, href: str) -> str: ...


# Mapeo nombre canónico de banda → clave de asset STAC, por proveedor. PC usa
# B0x; Earth Search usa nombres. `visual` es el TCI (color real ya renderizado).
_PC_BANDS = {"blue": "B02", "green": "B03", "red": "B04", "nir": "B08",
             "swir16": "B11", "swir22": "B12", "visual": "visual"}
_ES_BANDS = {"blue": "blue", "green": "green", "red": "red", "nir": "nir",
             "swir16": "swir16", "swir22": "swir22", "visual": "visual"}
# Landsat Collection 2 Level-2 (Planetary Computer): 30 m, factor publicado en cada asset
# (scale 2.75e-05, offset -0.2), máscara por bits en `qa_pixel`.
_PC_LANDSAT_BANDS = {"blue": "blue", "green": "green", "red": "red", "nir": "nir08",
                     "swir16": "swir16", "swir22": "swir22"}


@dataclass(frozen=True)
class Coleccion:
    """Lo que el motor necesita saber de una colección (hechos del catálogo, no heurísticas)."""

    id: str
    etiqueta: str
    resolucion_m: int
    bandas: dict                      # por proveedor: {"planetary-computer": {...}, ...}
    mascara: dict                     # por proveedor: clave del asset de la máscara
    mask_kind: str                    # "scl" | "qa_pixel"
    baseline_esa: bool                # ¿el factor se deriva del baseline de ESA si falta?


COLECCIONES: dict[str, Coleccion] = {
    "sentinel-2-l2a": Coleccion(
        id="sentinel-2-l2a", etiqueta="Sentinel-2 L2A", resolucion_m=10,
        bandas={"planetary-computer": _PC_BANDS, "earth-search": _ES_BANDS},
        mascara={"planetary-computer": "SCL", "earth-search": "scl"},
        mask_kind="scl", baseline_esa=True),
    "landsat-c2-l2": Coleccion(
        id="landsat-c2-l2", etiqueta="Landsat 8/9 C2 L2", resolucion_m=30,
        # Earth Search sirve Landsat desde un bucket de pago por el solicitante: sin
        # credenciales de AWS no se puede leer, así que solo Planetary Computer.
        bandas={"planetary-computer": _PC_LANDSAT_BANDS},
        mascara={"planetary-computer": "qa_pixel"},
        mask_kind="qa_pixel", baseline_esa=False),
}
COLECCION_DEFECTO = "sentinel-2-l2a"


class ColeccionNoDisponible(ValueError):
    """La colección no existe o este proveedor no la sirve (mensaje apto para el LLM)."""


def coleccion(nombre: str | None, proveedor: str) -> Coleccion:
    c = COLECCIONES.get(nombre or COLECCION_DEFECTO)
    if c is None:
        raise ColeccionNoDisponible(f"colección desconocida: {nombre!r}; válidas: {', '.join(COLECCIONES)}")
    if proveedor not in c.bandas:
        raise ColeccionNoDisponible(f"{c.etiqueta} no está disponible con el proveedor {proveedor} "
                                    f"(sí con: {', '.join(c.bandas)})")
    return c


def _bands_from_assets(assets: dict, mapping: dict) -> dict:
    """{nombre_canónico: href} de las bandas presentes (para composición RGB)."""
    out = {}
    for canon, key in mapping.items():
        href = (assets.get(key) or {}).get("href")
        if href:
            out[canon] = href
    return out


def _ids_payload(scene_id: str, collection: str = _COLLECTION) -> dict:
    """Búsqueda STAC por ID exacto (resolución perezosa de teselas)."""
    return {"collections": [collection], "ids": [scene_id], "limit": 1}


def _search_payload(aoi_geojson: dict, date_from: str, date_to: str,
                    max_cloud_pct: float, limit: int, collection: str = _COLLECTION) -> dict:
    return {
        "collections": [collection],
        "intersects": aoi_geojson,
        "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud_pct}},
        "limit": limit,
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }


def parse_item(f: dict, col: Coleccion, proveedor: str, coll_lookup=None) -> Scene | None:
    """Un item STAC → Scene con bandas CANÓNICAS de la colección (una sola ruta de parseo
    para search y get_scene: el factor de reflectancia se lee SIEMPRE, §1.3)."""
    assets = f.get("assets", {})
    mapa = col.bandas[proveedor]
    red = (assets.get(mapa["red"]) or {}).get("href")
    nir = (assets.get(mapa["nir"]) or {}).get("href")
    if not red or not nir:
        return None
    props = f.get("properties") or {}
    baseline = props.get("s2:processing_baseline")
    return Scene(
        id=f["id"], datetime=props.get("datetime", ""),
        cloud_pct=float(props.get("eo:cloud_cover", -1)),
        bbox=list(f.get("bbox") or [0, 0, 0, 0]),
        red_href=red, nir_href=nir,
        scl_href=(assets.get(col.mascara[proveedor]) or {}).get("href"),
        bands=_bands_from_assets(assets, mapa),
        scaling=_resolve_scaling(assets, mapa, baseline, coll_lookup, derivar_baseline=col.baseline_esa),
        processing_baseline=baseline,
        provider=proveedor, collection=col.id, mask_kind=col.mask_kind,
    )


@dataclass
class PlanetaryComputerProvider:
    """Microsoft Planetary Computer — SAS anónimo cacheado con margen de expiry."""

    name: str = "planetary-computer"
    # SAS por cuenta/contenedor de almacenamiento (cada colección vive en el suyo)
    _tokens: dict = field(default_factory=dict, repr=False, compare=False)
    # IMG (auditoría): serializa el refresco/lectura del token SAS (singleton
    # compartido entre hilos de teselas). Sin lock, sign() podía firmar '?None'.
    _lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)
    # Escalón 2 del factor de reflectancia, resuelto una sola vez (§1.3). None =
    # aún no consultado; {} = consultado y la colección no lo declara (el caso
    # real de PC), que se cachea igual para no re-preguntar en cada búsqueda.
    _coll_scaling: dict | None = field(default=None, repr=False, compare=False)

    def search(self, aoi_geojson, date_from, date_to, max_cloud_pct, limit,
               collection: str | None = None) -> list[Scene]:
        col = coleccion(collection, self.name)
        resp = httpx.post(
            _PC_STAC,
            json=_search_payload(aoi_geojson, date_from, date_to, max_cloud_pct, limit, col.id),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        # search y get_scene comparten _parse: si una sola de las dos rutas leyera
        # el `scale`/`offset`, el NDVI saldría bien o sesgado según por dónde se
        # hubiera resuelto la escena (auditoría 2026-09-08, §1.3).
        return [s for s in (self._parse(f, self._lookup(col), col)
                            for f in resp.json().get("features", []))
                if s is not None]

    def get_scene(self, scene_id: str, collection: str | None = None):
        """Resuelve UNA escena por ID (para teselas tras un restart)."""
        col = coleccion(collection, self.name)
        resp = httpx.post(_PC_STAC, json=_ids_payload(scene_id, col.id), timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        feats = resp.json().get("features", [])
        return self._parse(feats[0], self._lookup(col), col) if feats else None

    def _lookup(self, col: Coleccion):
        # el escalón 2 (factor declarado por la colección) es el de Sentinel-2
        return self._collection_scaling if col.id == _COLLECTION else None

    def _collection_scaling(self) -> dict:
        """Escalón 2 cacheado. PC no lo declara (comprobado 2026-09-08), así que
        en la práctica esto devuelve {} una vez y no se vuelve a preguntar."""
        with self._lock:
            if self._coll_scaling is not None:
                return self._coll_scaling
        found, concluyente = _fetch_collection_scaling(_PC_COLLECTION_DOC, _PC_BANDS)
        if concluyente:      # un fallo de red NO se cachea (media 10)
            with self._lock:
                self._coll_scaling = found
        return found

    def _parse(self, f: dict, coll_lookup=None, col: Coleccion | None = None):
        return parse_item(f, col or COLECCIONES[_COLLECTION], self.name, coll_lookup)

    def sign(self, href: str) -> str:
        """URL firmada con el SAS de SU cuenta/contenedor de almacenamiento.

        Cada colección de PC vive en una cuenta distinta (Sentinel-2 y Landsat no comparten
        token): el token se pide por `<cuenta>/<contenedor>` sacados del propio href.
        IMG (auditoría): refresco Y lectura bajo lock (double-check + copia local); antes,
        entre el check y el return otro hilo podía nulificar el token → URL firmada '?None'."""
        from urllib.parse import urlparse

        p = urlparse(href)
        cuenta = (p.hostname or "").split(".")[0]
        contenedor = p.path.lstrip("/").split("/")[0]
        clave = f"{cuenta}/{contenedor}"
        with self._lock:
            token, vence = self._tokens.get(clave, (None, 0.0))
            if token is None or time.time() > vence:
                resp = httpx.get(f"{_PC_SAS}/{clave}", timeout=_HTTP_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                token = data["token"]
                self._tokens[clave] = (token, self._parse_expiry(data.get("msft:expiry")))
        return f"{href}?{token}"

    def invalidate_token(self) -> None:
        """Fuerza re-firma en la próxima llamada (tras un 401/403 del store)."""
        with self._lock:
            self._tokens.clear()

    @staticmethod
    def _parse_expiry(iso: str | None) -> float:
        """Epoch REAL del SAS ('msft:expiry' ISO) menos 5 min de margen.

        El código anterior fijaba ``now + 30 min`` ignorando el expiry real: si
        el token duraba menos, se seguía usando expirado → 403 a mitad de sesión
        (M4). Sin expiry parseable, margen corto (10 min): mejor re-firmar de más
        que arriesgar un token vencido."""
        if iso:
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.timestamp() - 300
            except (ValueError, TypeError):
                pass
        return time.time() + 10 * 60


@dataclass
class EarthSearchProvider:
    """Element84 Earth Search — assets públicos, sin firma."""

    name: str = "earth-search"
    _lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)
    _coll_scaling: dict | None = field(default=None, repr=False, compare=False)

    def search(self, aoi_geojson, date_from, date_to, max_cloud_pct, limit,
               collection: str | None = None) -> list[Scene]:
        col = coleccion(collection, self.name)
        resp = httpx.post(
            _ES_STAC,
            json=_search_payload(aoi_geojson, date_from, date_to, max_cloud_pct, limit, col.id),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        # Misma razón que en PC: una sola ruta de parseo para search y get_scene,
        # para que el factor de reflectancia se lea SIEMPRE (§1.3).
        return [s for s in (self._parse(f, self._collection_scaling)
                            for f in resp.json().get("features", []))
                if s is not None]

    def get_scene(self, scene_id: str, collection: str | None = None):
        coleccion(collection, self.name)
        resp = httpx.post(_ES_STAC, json=_ids_payload(scene_id), timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        feats = resp.json().get("features", [])
        return self._parse(feats[0], self._collection_scaling) if feats else None

    def _collection_scaling(self) -> dict:
        """Escalón 2 cacheado. ES publica el factor en cada asset, así que este
        lookup perezoso normalmente ni se invoca."""
        with self._lock:
            if self._coll_scaling is not None:
                return self._coll_scaling
        found, concluyente = _fetch_collection_scaling(_ES_COLLECTION_DOC, _ES_BANDS)
        if concluyente:      # un fallo de red NO se cachea (media 10)
            with self._lock:
                self._coll_scaling = found
        return found

    def _parse(self, f: dict, coll_lookup=None, col: Coleccion | None = None):
        return parse_item(f, col or COLECCIONES[_COLLECTION], self.name, coll_lookup)

    def sign(self, href: str) -> str:
        return href


def build_provider(name: str) -> Any:
    """Proveedor por nombre; desconocido = error honesto (no default sorpresa)."""
    if name == "planetary-computer":
        return PlanetaryComputerProvider()
    if name == "earth-search":
        return EarthSearchProvider()
    raise ValueError(
        f"Proveedor STAC desconocido: {name!r} "
        "(válidos: planetary-computer, earth-search)"
    )
