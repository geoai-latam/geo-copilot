"""¿Se puede medir en ese CRS? Base EPSG local de pyproj, sin red (extraído del núcleo, T5.1).

Dos preguntas distintas: `is_metric_crs` (¿coordenadas en metros?; 3857 sí) y
`check_measurable_crs` (¿se puede MEDIR área o longitud?; 3857 no: deforma).
"""

from __future__ import annotations

import functools

# Proyectados y en metros donde medir sigue siendo un error, porque la
# proyección no conserva la magnitud. No es un problema de unidades sino de
# deformación, y por eso es una lista corta y explícita en vez de salir de la
# base EPSG (que no modela "sirve para medir").
_METRICOS_QUE_DEFORMAN: dict[int, str] = {
    3857: (
        "Web Mercator deforma con la latitud (~0,7 % de error de área en "
        "Bogotá, >170 % en la Patagonia)"
    ),
    900913: "alias no-EPSG de Web Mercator, misma deformación",
    3785: "Pseudo-Mercator deprecado, misma deformación",
    4087: "Equidistant Cylindrical sólo conserva distancias sobre meridianos",
    4088: "Equidistant Cylindrical (sphere), misma limitación",
}


@functools.lru_cache(maxsize=512)
def _describe_crs(srid: int) -> tuple[str, str, str]:
    """`(clase, nombre, unidad)` de un EPSG, leído de la base local de pyproj.

    `clase` es ``"proyectado"``, ``"geografico"`` o ``"desconocido"``. No hace
    red: PROJ trae la base EPSG embebida.
    """
    try:
        from pyproj import CRS
        from pyproj.exceptions import CRSError
    except ImportError:  # pragma: no cover - pyproj es dependencia declarada
        return "desconocido", "", ""

    try:
        crs = CRS.from_epsg(srid)
    except CRSError:
        return "desconocido", "", ""

    unidad = crs.axis_info[0].unit_name if crs.axis_info else ""
    return ("proyectado" if crs.is_projected else "geografico"), crs.name, unidad


def is_metric_crs(srid: int | None) -> bool:
    """¿Las coordenadas de ese SRID están en metros? F1.3/F2.1.

    Proyectado **y** con el eje en metros. Un CRS geográfico (4326, 4686) mide
    en grados; uno proyectado puede medir en pies (EPSG:2276, Texas North
    Central, en `US survey foot`).

    3857 devuelve `True` a propósito: sus coordenadas SON metros. Que no sirva
    para medir áreas es otra pregunta, y la contesta `check_measurable_crs`.
    """
    if not srid:
        return False
    clase, _nombre, unidad = _describe_crs(srid)
    return clase == "proyectado" and unidad == "metre"


def check_measurable_crs(srid: int | None) -> tuple[bool, str]:
    """¿Se puede medir área o longitud en ese SRID? `(apto, motivo)`.

    Más estricto que `is_metric_crs`: además de estar en metros, la proyección
    tiene que conservar la magnitud. Es la comprobación que aplica
    `sql_ast_validator` a `ST_Area`, `ST_Length`, `ST_Distance`, `ST_Buffer`,
    `ST_Perimeter`, `ST_DWithin` y `ST_ClusterDBSCAN`.

    Falla cerrado: un SRID que no se puede resolver no se da por bueno.
    """
    if not srid:
        return False, "SRID desconocido"

    if srid in _METRICOS_QUE_DEFORMAN:
        return False, f"EPSG:{srid} — {_METRICOS_QUE_DEFORMAN[srid]}"

    clase, nombre, unidad = _describe_crs(srid)
    if clase == "desconocido":
        return False, f"EPSG:{srid} no existe en la base EPSG local"
    if clase == "geografico":
        return False, f"EPSG:{srid} ({nombre}) es geográfico: mide en grados"
    if unidad != "metre":
        return False, f"EPSG:{srid} ({nombre}) mide en {unidad or '?'}, no en metros"

    return True, ""
