"""Filas de un portal → hechos de la consulta y, si traen latitud/longitud, una capa de puntos."""

from __future__ import annotations

import re
from typing import Any

from geo_mcp_kit import completitud

_AGREGADO = re.compile(r"\b(count|sum|avg|min|max|stddev_pop|stddev_samp)\s*\(", re.IGNORECASE)
_DISTINTAS = re.compile(r"^\s*distinct\b", re.IGNORECASE)


def es_agregado(select: str | None) -> bool:
    """`select` con agregados o `distinct`: el resultado son grupos, no filas del conjunto.

    `distinct` (Terranova, spec 07): SECOP II publica copias exactas de algunas modificaciones (33 filas
    para 16). Que lleguen menos filas que las del filtro no es un corte.
    """
    return bool(select and (_AGREGADO.search(select) or _DISTINTAS.search(select)))


class ErrorColumnas(ValueError):
    """Las columnas pedidas no están en los datos (el mensaje lista las que hay)."""


def _numero(valor: Any) -> float | None:
    """Un número de una celda de portal («6,17» o «6.17»); None si no lo es."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None


def puntos(filas: list[dict], lat: str, lon: str, hechos: dict[str, Any]) -> dict:
    """FeatureCollection de puntos con las columnas `lat`/`lon`; las filas sin coordenadas válidas se cuentan.

    Las coordenadas fuera de rango (o invertidas: latitud en la columna de longitud) NO se corrigen: se
    dicen. Adivinar el orden ubicaría un colegio en el océano sin que nadie lo note.
    """
    # Socrata omite en cada fila las columnas nulas: se mira la unión de varias, no la primera
    claves = {k for f in filas[:200] for k in f}
    if filas and (lat not in claves or lon not in claves):
        columnas = ", ".join(sorted(claves))
        raise ErrorColumnas(f"las columnas «{lat}»/«{lon}» no están en los datos; las que hay: {columnas}")
    features, sin_coords, fuera = [], 0, 0
    for fila in filas:
        y, x = _numero(fila.get(lat)), _numero(fila.get(lon))
        if x is None or y is None:
            sin_coords += 1
            continue
        if not (-180 <= x <= 180 and -90 <= y <= 90):
            fuera += 1
            continue
        props = {k: v for k, v in fila.items() if not isinstance(v, (dict, list))}
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
                         "properties": props})
    hechos["elementos"] = len(features)
    if sin_coords:
        hechos["filas_sin_coordenadas"] = sin_coords
    if fuera:
        hechos["filas_con_coordenadas_fuera_de_rango"] = fuera
    return {"type": "FeatureCollection", "features": features}


def hechos(ident: str, portal: dict, datos: list, total: int | None, maximo: int,
           filtro: str | None, *, select: str | None = None, group: str | None = None,
           having: str | None = None, previo_group: str | None = None) -> dict[str, Any]:
    vinieron = len(datos)
    h: dict[str, Any] = {"conjunto": ident, "portal": portal["id"], "filas": vinieron}
    if filtro:
        h["filtro_soql"] = filtro
    h["consulta"] = {"conjunto": ident, "portal": portal["id"], **({"select": select} if select else {}),
                     **({"where": filtro} if filtro else {}), **({"group": group} if group else {}),
                     **({"having": having} if having else {}),
                     **({"previo_group": previo_group} if previo_group else {}), "max_filas": maximo}
    if es_agregado(select):
        # un agregado (count, sum…) resume TODAS las filas que cumplen: no es una muestra aunque
        # devuelva 1 fila de 12 149 (antes decía «no es el conjunto completo»). Con group by,
        # solo está cortado si llegó al tope.
        h["filas_que_resume"] = total
        cortado = vinieron >= maximo
        h["completo"] = not cortado
        h["completitud"] = completitud(vinieron, None if cortado else vinieron, truncado=cortado,
                                       motivo=f"max_filas={maximo}" if cortado else None)
        if cortado:
            h["aviso"] = f"vinieron {vinieron} grupos y es el tope: puede haber más; sube `max_filas`"
        return h
    # el conteo y las filas son dos peticiones: si el conjunto cambió en medio, el total no sirve
    total_valido = total if total is not None and total >= vinieron else None
    h["completitud"] = completitud(vinieron, total_valido, truncado=total_valido is None and vinieron >= maximo,
                                   motivo=f"max_filas={maximo}" if (total_valido or 0) > vinieron else None)
    if total is not None:
        h["cumplen_en_el_portal"] = total
        h["completo"] = vinieron >= total
        if vinieron < total:
            h["aviso"] = (f"vinieron {vinieron} de {total}: no es el conjunto completo. Para más, sube "
                          f"`max_filas` (hasta 50000) o acota el filtro")
    return h
