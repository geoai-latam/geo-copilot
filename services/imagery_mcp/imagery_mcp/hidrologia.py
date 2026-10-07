"""Hidrología sobre el DEM: la cuenca que drena a un punto y su red de drenaje.

Es el método clásico de los SIG (lo que hacen «Fill / Flow Direction / Flow Accumulation /
Watershed»), en numpy y sin dependencias pesadas:

1. relleno de depresiones por inundación con prioridad y épsilon (Barnes et al. 2014): todo
   píxel queda con un vecino más bajo y el agua siempre sale hacia el borde;
2. dirección de flujo D8: cada píxel vierte al vecino de mayor descenso;
3. acumulación: cuántos píxeles drenan por cada uno (de arriba abajo);
4. la cuenca de un punto: el punto se ajusta al píxel de mayor acumulación cercano (el cauce
   que el usuario quiso marcar) y se toman los píxeles cuyo camino pasa por él;
5. la red de drenaje: los píxeles con más de `umbral_km2` aguas arriba, como líneas.

Es el DEM de una VENTANA alrededor del punto: si la cuenca toca su borde, sigue más allá y el
hecho `toca_borde` lo dice (con un radio mayor se ve entera).
"""

from __future__ import annotations

import heapq
import math
from typing import Any

import numpy as np

from imagery_mcp.engine import ImageryError
from imagery_mcp.terreno import leer_zona

#: Vecinos D8 (fila, columna) y su distancia relativa.
_VECINOS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_EPS = 1e-3
#: Radio máximo de la ventana (km). El DEM se lee a ≤1.500 px de lado (más allá de ~22 km de
#: radio pierde resolución): es el tope de memoria y tiempo del relleno en Python puro.
RADIO_MAX_KM = 40.0


def rellenar(dem: np.ndarray) -> np.ndarray:
    """Relleno de depresiones con épsilon: el agua siempre baja hasta el borde (o la nada)."""
    h, w = dem.shape
    lleno = dem.astype("float64").copy()
    nan = ~np.isfinite(lleno)
    visto = nan.copy()
    cola: list[tuple[float, int]] = []
    borde = np.zeros_like(visto)
    borde[0, :] = borde[-1, :] = borde[:, 0] = borde[:, -1] = True
    # el borde y los vecinos de NaN (mar / sin datos) son salidas
    junto_nan = np.zeros_like(visto)
    if nan.any():
        for dr, dc in _VECINOS:
            junto_nan |= np.roll(np.roll(nan, dr, 0), dc, 1)
    for i in np.flatnonzero((borde | junto_nan) & ~nan):
        heapq.heappush(cola, (lleno.flat[i], int(i)))
        visto.flat[i] = True
    plano = lleno.ravel()
    vflat = visto.ravel()
    while cola:
        z, i = heapq.heappop(cola)
        r, c = divmod(i, w)
        for dr, dc in _VECINOS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                j = rr * w + cc
                if not vflat[j]:
                    vflat[j] = True
                    if plano[j] <= z:
                        plano[j] = z + _EPS
                    heapq.heappush(cola, (plano[j], j))
    return lleno


def direcciones(lleno: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Receptor D8 de cada píxel (índice plano); un píxel sin vecino más bajo se vierte a sí mismo."""
    h, w = lleno.shape
    pad = np.pad(lleno, 1, constant_values=np.inf)
    mejor = np.zeros((h, w))
    receptor = np.arange(h * w).reshape(h, w)
    filas, cols = np.indices((h, w))
    for dr, dc in _VECINOS:
        dist = math.hypot(dr * dy, dc * dx)
        vecino = pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
        caida = (lleno - vecino) / dist
        caida = np.where(np.isfinite(caida), caida, -np.inf)
        gana = caida > mejor
        mejor = np.where(gana, caida, mejor)
        receptor = np.where(gana, (filas + dr) * w + (cols + dc), receptor)
    return receptor.ravel()


def acumulacion(lleno: np.ndarray, receptor: np.ndarray) -> np.ndarray:
    """Píxeles que drenan por cada uno (él incluido), recorriendo de lo más alto a lo más bajo."""
    acc = np.ones(receptor.size)
    orden = np.argsort(-np.nan_to_num(lleno.ravel(), nan=-np.inf), kind="stable")
    rec = receptor.tolist()
    a = acc.tolist()
    for i in orden.tolist():
        j = rec[i]
        if j != i:
            a[j] += a[i]
    return np.asarray(a).reshape(lleno.shape)


def cuenca(receptor: np.ndarray, salida: int) -> np.ndarray:
    """Máscara (plana) de los píxeles que drenan a `salida`: saltos de puntero hasta el sumidero."""
    rec = receptor.copy()
    rec[salida] = salida
    for _ in range(64):
        siguiente = rec[rec]
        if np.array_equal(siguiente, rec):
            break
        rec = siguiente
    return rec == salida


def _ajustar(acc: np.ndarray, fila: int, col: int, radio_px: int) -> tuple[int, int]:
    """El píxel de mayor acumulación a `radio_px` del punto: el cauce que se quiso marcar."""
    h, w = acc.shape
    r0, r1 = max(0, fila - radio_px), min(h, fila + radio_px + 1)
    c0, c1 = max(0, col - radio_px), min(w, col + radio_px + 1)
    sub = acc[r0:r1, c0:c1]
    k = int(np.argmax(sub))
    return r0 + k // sub.shape[1], c0 + k % sub.shape[1]


def _poligono(mascara: np.ndarray, transform) -> dict:
    from rasterio.features import shapes

    partes = [g for g, v in shapes(mascara.astype("uint8"), mask=mascara, transform=transform) if v == 1]
    if len(partes) == 1:
        return partes[0]
    return {"type": "MultiPolygon", "coordinates": [p["coordinates"] for p in partes]}


def _red(acc: np.ndarray, receptor: np.ndarray, umbral_px: float, dentro: np.ndarray, transform,
         area_px_km2: float, max_tramos: int = 4000) -> list[dict]:
    """La red de drenaje como líneas: desde cada cabecera, aguas abajo hasta una confluencia."""
    h, w = acc.shape
    cauce = (acc.ravel() >= umbral_px) & dentro.ravel()
    rec = receptor
    # cabecera: cauce al que no llega ningún otro cauce
    llega = np.zeros(acc.size, dtype=int)
    idx = np.flatnonzero(cauce)
    np.add.at(llega, rec[idx][rec[idx] != idx], 1)
    cabeceras = idx[llega[idx] == 0]
    visto = np.zeros(acc.size, dtype=bool)

    def xy(i: int) -> list[float]:
        r, c = divmod(i, w)
        x, y = transform * (c + 0.5, r + 0.5)
        return [round(x, 6), round(y, 6)]

    tramos = []
    pendientes = list(cabeceras[np.argsort(acc.ravel()[cabeceras])])
    while pendientes and len(tramos) < max_tramos:
        i = int(pendientes.pop())
        linea = [xy(i)]
        visto[i] = True
        while True:
            j = int(rec[i])
            if j == i or not cauce[j]:
                break
            linea.append(xy(j))
            if visto[j]:
                break
            visto[j] = True
            if llega[j] > 1:    # confluencia: aquí empieza otro tramo
                pendientes.append(j)
                visto[j] = False
                break
            i = j
        if len(linea) > 1:
            tramos.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": linea},
                           "properties": {"tipo": "cauce",
                                          "area_drenada_km2": round(float(acc.flat[i]) * area_px_km2, 2)}})
    return tramos


def _geometrias(geojson: dict) -> list[dict]:
    g = geojson or {}
    if g.get("type") == "FeatureCollection":
        return [f.get("geometry") for f in g.get("features") or [] if f and f.get("geometry")]
    if g.get("type") == "Feature":
        return [g["geometry"]] if g.get("geometry") else []
    return [g] if g.get("type") else []


def _dilatar(m: np.ndarray, n: int) -> np.ndarray:
    out = m.copy()
    for _ in range(n):
        p = np.pad(out, 1)
        out = out | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return out


def cuenca_de(geojson: dict, radio_km: float = 15.0, umbral_km2: float = 1.0) -> dict[str, Any]:
    """La cuenca y su red de drenaje según lo que se señale: un PUNTO (la que drena a él, con el
    punto ajustado al cauce a ≤300 m), una LÍNEA — un río — (la de su punto aguas abajo: el de
    mayor acumulación sobre la línea) o un ÁREA (la del cauce principal que sale de ella)."""
    from rasterio.features import rasterize

    from imagery_mcp.aoi import aoi_bbox

    geoms = _geometrias(geojson)
    if not geoms:
        raise ImageryError("hace falta un punto, una línea (un río) o un área en GeoJSON")
    tipos = {g.get("type") for g in geoms}
    modo = "punto" if tipos <= {"Point"} and len(geoms) == 1 else         "linea" if tipos <= {"LineString", "MultiLineString"} else "area"
    minx, miny, maxx, maxy = aoi_bbox({"type": "GeometryCollection", "geometries": geoms})         if modo != "punto" else (*geoms[0]["coordinates"][:2], *geoms[0]["coordinates"][:2])
    lon, lat = (minx + maxx) / 2, (miny + maxy) / 2
    semidiag = math.hypot((maxx - minx) * 111.320 * math.cos(math.radians(lat)), (maxy - miny) * 110.574) / 2
    radio = max(radio_km, semidiag * 1.2 + 2) if modo != "punto" else radio_km
    if not (0.5 <= radio <= RADIO_MAX_KM):
        raise ImageryError(f"la ventana necesaria ({radio:.0f} km de radio) sale de 0,5–{RADIO_MAX_KM:g} km: "
                           "señala un punto, un tramo de río o un área más pequeña")
    dlat = radio / 110.574
    dlon = radio / (111.320 * max(0.05, math.cos(math.radians(lat))))
    dem, transform, (dx, dy) = leer_zona((lon - dlon, lat - dlat, lon + dlon, lat + dlat), max_px=1500)
    if not np.isfinite(dem).any():
        raise ImageryError("no hay modelo de elevación en esa zona (¿mar abierto?)")
    lleno = rellenar(dem)
    receptor = direcciones(lleno, dx, dy)
    acc = acumulacion(lleno, receptor)
    ajuste_px = max(1, int(300 / max(dx, dy)))
    if modo == "punto":
        col, fila = (~transform) * (geoms[0]["coordinates"][0], geoms[0]["coordinates"][1])
        fila, col = _ajustar(acc, int(fila), int(col), radio_px=ajuste_px)
    else:
        candidatos = rasterize(geoms, out_shape=dem.shape, transform=transform, all_touched=True).astype(bool)
        if modo == "linea":   # el trazo vectorial no cae justo en el cauce del DEM
            candidatos = _dilatar(candidatos, ajuste_px)
        if not candidatos.any():
            raise ImageryError("la geometría no cae dentro del modelo de elevación")
        k = int(np.argmax(np.where(candidatos, acc, -1)))
        fila, col = divmod(k, dem.shape[1])
    salida = fila * dem.shape[1] + col
    mascara = cuenca(receptor, salida).reshape(dem.shape) & np.isfinite(dem)
    area_px_km2 = dx * dy / 1e6
    toca_borde = bool(mascara[0, :].any() or mascara[-1, :].any() or mascara[:, 0].any() or mascara[:, -1].any())
    e = dem[mascara]
    x_sal, y_sal = transform * (col + 0.5, fila + 0.5)
    pedido = [round(lon, 6), round(lat, 6)]
    props = {
        "tipo": "cuenca", "area_km2": round(float(mascara.sum()) * area_px_km2, 2),
        "elevacion_min_m": round(float(e.min()), 1), "elevacion_media_m": round(float(e.mean()), 1),
        "elevacion_max_m": round(float(e.max()), 1), "toca_borde": toca_borde,
    }
    cuenca_f = {"type": "Feature", "geometry": _poligono(mascara, transform), "properties": props}
    salida_f = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(x_sal, 6), round(y_sal, 6)]},
                "properties": {"tipo": "salida", "area_drenada_km2": props["area_km2"]}}
    umbral_px = max(2.0, umbral_km2 / area_px_km2)
    red = _red(acc, receptor, umbral_px, mascara, transform, area_px_km2)
    return {
        "geojson": {"type": "FeatureCollection", "features": [cuenca_f, *red, salida_f]},
        "hechos": {
            "fuente": "Copernicus DEM GLO-30; relleno de depresiones + flujo D8",
            "señalado": modo,
            "salida_elegida": {"punto": "el cauce más cercano al punto (≤300 m)",
                               "linea": "el punto aguas abajo de la línea (mayor acumulación sobre ella)",
                               "area": "donde el cauce principal sale del área (mayor acumulación dentro)"}[modo],
            "salida": [round(x_sal, 6), round(y_sal, 6)],
            **({"ajuste_m": round(math.hypot((x_sal - pedido[0]) * 111_320 * math.cos(math.radians(lat)),
                                             (y_sal - pedido[1]) * 110_574), 0)} if modo == "punto" else {}),
            "cuenca": props, "tramos_de_cauce": len(red), "umbral_cauce_km2": umbral_km2,
            "ventana_km": round(radio, 1), "resolucion_m": round(max(dx, dy), 1),
            **({"nota": "con un ÁREA la salida es la del cauce que más agua saca de ella, que puede no ser "
                        "el río que se nombró; para un río concreto, pasa su capa (línea) o un punto sobre "
                        "él"} if modo == "area" else {}),
            **({"aviso": "la cuenca llega al borde de la ventana: sigue más allá; con un radio_km mayor "
                         "se ve entera"} if toca_borde else {}),
        },
    }
