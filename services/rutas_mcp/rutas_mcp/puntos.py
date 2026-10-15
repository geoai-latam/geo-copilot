"""De lo que pide el agente (referencias resueltas por el núcleo o números) a coordenadas lon/lat, y hechos."""

from __future__ import annotations

from typing import Any


class ErrorPuntos(ValueError):
    """El extremo no se pudo convertir en un punto (el mensaje dice por qué y qué dar)."""


def par(p: Any, nombre: str) -> tuple[float, float]:
    """[lon, lat] validado (los números de lugares_buscar vienen así)."""
    try:
        lon, lat = (float(v) for v in p)
    except (TypeError, ValueError) as exc:
        raise ErrorPuntos(f"`{nombre}` debe ser [lon, lat]") from exc
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ErrorPuntos(f"`{nombre}` fuera de rango (lon -180..180, lat -90..90): ¿invertiste lat y lon?")
    return lon, lat


def _centro(geom: dict) -> tuple[tuple[float, float], bool]:
    """(lon, lat) del punto o del centro (punto interior) de otra geometría; True si NO era un punto."""
    from shapely.geometry import shape

    g = shape(geom)
    if g.geom_type == "Point":
        return (g.x, g.y), False
    c = g.representative_point()  # dentro del polígono (el centroide puede caer fuera)
    return (c.x, c.y), True


def _features(fc: dict | None) -> list[dict]:
    return [f for f in (fc or {}).get("features") or [] if isinstance(f, dict) and f.get("geometry")]


def uno(fc: dict | None, lon: float | None, lat: float | None, nombre: str, notas: list[str]) -> tuple[float, float]:
    """Un extremo: de la referencia (un elemento) o de los números; nunca se elige entre varios."""
    if fc is not None:
        feats = _features(fc)
        if len(feats) != 1:
            raise ErrorPuntos(f"`{nombre}` tiene {len(feats)} elementos: para un extremo hace falta UNO "
                              "(selecciona uno, usa `punto` o da sus coordenadas)")
        p, era_area = _centro(feats[0]["geometry"])
        if era_area:
            notas.append(f"{nombre}: no es un punto; se usó un punto interior de su geometría")
        return par(p, nombre)
    if lon is None or lat is None:
        raise ErrorPuntos(f"falta `{nombre}`: una referencia (`punto`, una capa de un elemento) o "
                          f"`{nombre}_lon` y `{nombre}_lat`")
    return par([lon, lat], nombre)


def _etiqueta(f: dict, i: int) -> str:
    """«#i» y, si lo hay, el primer atributo de texto (para que la tabla se lea sin el mapa)."""
    texto = next((str(v) for v in (f.get("properties") or {}).values() if isinstance(v, str) and v.strip()), "")
    return f"#{i} {texto[:60]}".strip()


def varios(fc: dict | None, lista: list[list[float]] | None, nombre: str, maximo: int,
           notas: list[str]) -> list[tuple[tuple[float, float], str]]:
    if fc is not None:
        feats = _features(fc)
        salida, areas = [], 0
        for i, f in enumerate(feats):
            p, era_area = _centro(f["geometry"])
            areas += era_area
            salida.append((par(p, nombre), _etiqueta(f, i)))
        if areas:
            notas.append(f"{nombre}: {areas} no son puntos; se usó un punto interior de cada uno")
    else:
        salida = [(par(p, nombre), f"#{i}") for i, p in enumerate(lista or [])]
    if not salida:
        raise ErrorPuntos(f"faltan `{nombre}`: una capa de puntos, la selección o una lista [[lon, lat], …]")
    if len(salida) > maximo:
        raise ErrorPuntos(f"`{nombre}` tiene {len(salida)} puntos (máx. {maximo}): acota con la selección o un filtro")
    return salida


def area_km2(geom: dict) -> float:
    from pyproj import Geod
    from shapely.geometry import shape

    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(shape(geom))
    return round(abs(area) / 1e6, 2)


def tabla_matriz(res: dict, a: list, b: list) -> tuple[list[dict], list[dict]]:
    """Filas origen×destino (km, min; vacío si no hay ruta) y, por origen, el destino de menor tiempo."""
    celdas = res.get("sources_to_targets") or []
    filas, cercanos = [], []
    for i, fila in enumerate(celdas):
        mejor = None
        for j, c in enumerate(fila):
            tiempo = c.get("time")
            filas.append({"origen": a[i][1], "destino": b[j][1],
                          "km": round(c["distance"], 2) if c.get("distance") is not None else None,
                          "min": round(tiempo / 60, 1) if tiempo is not None else None})
            if tiempo is not None and (mejor is None or tiempo < mejor[1]):
                mejor = (j, tiempo)
        cercanos.append({"origen": a[i][1], "destino_mas_cercano": b[mejor[0]][1] if mejor else None,
                         "min": round(mejor[1] / 60, 1) if mejor else None})
    return filas, cercanos
