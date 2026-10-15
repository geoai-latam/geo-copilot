"""Teselas XYZ de una capa WMS: el mapa del cliente pide `/wms/<ficha>/{z}/{x}/{y}.png` (por el proxy del
núcleo) y este servidor hace el GetMap en EPSG:3857 con la extensión de esa tesela.

La ficha lleva QUÉ pedir (URL del WMS, capa, estilo, versión) y va FIRMADA (HMAC): la ruta no sirve para
pedirle a este servidor una URL cualquiera, solo las que una herramienta ya validó. La clave sale de
`OGC_MCP_TILE_SECRET` o, si no está, de las claves del servidor (estable entre reinicios: la capa sigue
viéndose tras recargar). Medido (PNN, 2026-10-15): GetMap 1.3.0 en EPSG:3857 → PNG de 25 KB.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import threading
from collections import OrderedDict
from typing import Any

from ogc_mcp import ows

RUTA_RE = re.compile(r"^/wms/([A-Za-z0-9_-]{8,2048})\.([0-9a-f]{32})/(\d{1,2})/(\d{1,8})/(\d{1,8})\.png$")
CRS_WEB = ("EPSG:3857", "EPSG:900913", "EPSG:102100")
_ORIGEN = 20037508.342789244
_cache: OrderedDict[tuple, bytes] = OrderedDict()
_cerrojo = threading.Lock()
_CACHE_MAX = 512


def _clave() -> bytes:
    propia = os.environ.get("OGC_MCP_TILE_SECRET", "")
    semilla = propia or os.environ.get("OGC_MCP_KEYS", "")
    if not semilla:
        raise RuntimeError("ogc-mcp necesita OGC_MCP_TILE_SECRET u OGC_MCP_KEYS para firmar las teselas")
    return hashlib.sha256(b"ogc-mcp-teselas|" + semilla.encode()).digest()


def _firma(datos: str) -> str:
    return hmac.new(_clave(), datos.encode(), hashlib.sha256).hexdigest()[:32]


def ficha(url: str, capa: str, estilo: str, version: str, crs: str) -> str:
    """`<datos>.<firma>`: lo que la ruta de teselas sabe pedir."""
    crudo = json.dumps({"u": url, "l": capa, "s": estilo, "v": version, "c": crs}, separators=(",", ":"))
    datos = base64.urlsafe_b64encode(crudo.encode()).decode().rstrip("=")
    return f"{datos}.{_firma(datos)}"


def parse_ruta(ruta: str) -> tuple[dict, int, int, int] | None:
    m = RUTA_RE.match(ruta)
    if not m:
        return None
    datos, firma, z, x, y = m.groups()
    if not hmac.compare_digest(firma, _firma(datos)):
        return None
    try:
        relleno = "=" * (-len(datos) % 4)
        spec = json.loads(base64.urlsafe_b64decode(datos + relleno))
    except ValueError:
        return None
    z, x, y = int(z), int(x), int(y)
    if z > 22 or x >= 2 ** z or y >= 2 ** z:
        return None
    return spec, z, x, y


def bbox_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    lado = 2 * _ORIGEN / (2 ** z)
    minx = -_ORIGEN + x * lado
    maxy = _ORIGEN - y * lado
    return minx, maxy - lado, minx + lado, maxy


def crs_web(capa: dict[str, Any]) -> str | None:
    """El CRS web-mercator que la capa declara (o ninguno: entonces no se puede teselar)."""
    return next((c for c in CRS_WEB if c in capa.get("_crs", set())), None)


def atiende_3857(url: str, version: str, capa: str, bbox: list[float] | None) -> bool:
    """¿Dibuja la capa en EPSG:3857 aunque no lo declare? Se comprueba con UNA tesela en su centro.

    Medido (SGC, ArcGIS Server 10.9, 2026-10-15): declara solo CRS:84/4326/4686 y aun así devuelve la
    capa reproyectada en 3857 (sombras: 124 KB en 3857 contra 121 KB en CRS:84; PGA75: 42 043 píxeles).
    """
    lon, lat = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2) if bbox else (0.0, 0.0)
    lat = max(-85.0, min(85.0, lat))
    x, y = tesela_xyz_de(lon, lat, 6)
    try:
        getmap({"u": url, "l": capa, "s": "", "v": version, "c": "EPSG:3857"}, 6, x, y)
    except ows.ErrorOgc:
        return False
    return True


def getmap(spec: dict, z: int, x: int, y: int) -> bytes:
    clave = (spec["u"], spec["l"], spec["s"], z, x, y)
    with _cerrojo:
        if clave in _cache:
            _cache.move_to_end(clave)
            return _cache[clave]
    bb = ",".join(f"{v:.6f}" for v in bbox_3857(z, x, y))
    clave_crs = "crs" if spec["v"].startswith("1.3") else "srs"
    r = ows.pedir(spec["u"], {"service": "WMS", "version": spec["v"], "request": "GetMap", "layers": spec["l"],
                              "styles": spec["s"], clave_crs: spec["c"], "bbox": bb, "width": 256, "height": 256,
                              "format": "image/png", "transparent": "TRUE"})
    if not r.headers.get("content-type", "").startswith("image/"):
        raise ows.ErrorOgc(f"el WMS no devolvió imagen: {r.text[:200]}")
    with _cerrojo:
        _cache[clave] = r.content
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return r.content


def tesela_xyz_de(lon: float, lat: float, z: int) -> tuple[int, int]:
    """La tesela que contiene un punto (para pruebas y para comprobar que la capa se ve)."""
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n)
    return x, y
