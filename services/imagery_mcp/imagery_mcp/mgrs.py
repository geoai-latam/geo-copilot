"""La huella de una tesela MGRS de Sentinel-2 a partir de su id (p. ej. «18NWL»).

Una tesela de Sentinel-2 es el cuadrado de 100 km de MGRS ampliado a 109,8 km (solapa 9,8 km
con la vecina del este y la del sur), en la proyección UTM de su zona. El id dice la zona (18),
la banda de latitud (N) y el cuadrado de 100 km (columna W, fila L); de ahí sale su esquina.
Sirve para pintar la cuadrícula del mundo sin leer la geometría de 30 millones de escenas.
"""

from __future__ import annotations

import re
from functools import lru_cache

_ID = re.compile(r"^(\d{2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])$")
_COLUMNAS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_FILAS = "ABCDEFGHJKLMNPQRSTUV"
_BANDAS = "CDEFGHJKLMNPQRSTUVWX"      # de 8° en 8° desde -80° (la X llega a 84°)
_LADO_M = 109_800
_DENSO = 1                             # puntos por lado: a 110 km el borde UTM es casi recto en lon/lat


@lru_cache(maxsize=64)
def _a_wgs84(zona: int, sur: bool):
    from pyproj import Transformer

    return Transformer.from_crs(f"EPSG:{32700 + zona if sur else 32600 + zona}", "EPSG:4326",
                                always_xy=True)


@lru_cache(maxsize=64)
def _a_utm(zona: int, sur: bool):
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4326", f"EPSG:{32700 + zona if sur else 32600 + zona}",
                                always_xy=True)


def esquina_utm(tile: str) -> tuple[int, bool, float, float] | None:
    """(zona, ¿hemisferio sur?, este, norte) de la esquina suroeste del cuadrado de 100 km."""
    m = _ID.match(tile or "")
    if not m:
        return None
    zona, banda, col, fila = int(m[1]), m[2], m[3], m[4]
    conjunto = (zona - 1) % 3
    if col not in _COLUMNAS[conjunto]:
        return None
    este = (_COLUMNAS[conjunto].index(col) + 1) * 100_000
    # En las zonas pares las letras de fila empiezan corridas 5 posiciones (esquema AA de WGS84).
    norte_mod = ((_FILAS.index(fila) - (5 if zona % 2 == 0 else 0)) % 20) * 100_000
    sur = banda < "N"
    lat_min = -80 + 8 * _BANDAS.index(banda)
    lon_central = -183 + 6 * zona
    _, n_banda = _a_utm(zona, sur).transform(lon_central, lat_min)
    # El norte real es norte_mod + k·2000 km: el primero cuyo cuadrado toca la banda.
    norte = norte_mod
    while norte + 100_000 <= n_banda - 1:
        norte += 2_000_000
    return zona, sur, float(este), float(norte)


def huella(tile: str) -> dict | None:
    """Polígono GeoJSON (lon/lat) de la tesela de Sentinel-2, o None si el id no es MGRS."""
    e = esquina_utm(tile)
    if e is None:
        return None
    zona, sur, x0, n0 = e
    # Cubre de x0 a x0+109,8 km y de (n0+100 km)−109,8 km a n0+100 km (el solape va al sur).
    xmin, xmax, ymax = x0, x0 + _LADO_M, n0 + 100_000
    ymin = ymax - _LADO_M
    pasos = [i / _DENSO for i in range(_DENSO + 1)]
    anillo = ([(xmin + (xmax - xmin) * t, ymin) for t in pasos]
              + [(xmax, ymin + (ymax - ymin) * t) for t in pasos[1:]]
              + [(xmax - (xmax - xmin) * t, ymax) for t in pasos[1:]]
              + [(xmin, ymax - (ymax - ymin) * t) for t in pasos[1:]])
    lons, lats = _a_wgs84(zona, sur).transform([p[0] for p in anillo], [p[1] for p in anillo])
    coords = [[round(lo, 4), round(la, 4)] for lo, la in zip(lons, lats, strict=True)]
    if max(c[0] for c in coords) - min(c[0] for c in coords) > 180:   # cruza el antimeridiano
        return None
    return {"type": "Polygon", "coordinates": [coords]}
