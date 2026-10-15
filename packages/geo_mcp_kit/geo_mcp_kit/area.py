"""Filtrar por un ÁREA real (la geometría de una capa de la sesión), no por su rectángulo. Requiere shapely.

V5 (ogc-mcp, 2026-10-15): «áreas protegidas en Bogotá» filtró por el rectángulo del límite y contó
Tibaitatá (Mosquera) como de Bogotá. El servicio filtra por el rectángulo (es lo que sabe hacer) y aquí
se dejan los elementos que de verdad intersecan el área.
"""

from __future__ import annotations

from typing import Any


class AreaInvalida(ValueError):
    pass


def geometria(area: dict) -> Any:
    """La unión (shapely) de las geometrías de un FeatureCollection (lo que el núcleo entrega por una capa)."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    feats = area.get("features") if isinstance(area, dict) else None
    geoms = [shape(f["geometry"]) for f in feats or [] if isinstance(f, dict) and f.get("geometry")]
    if not geoms:
        raise AreaInvalida("`area` no trae geometrías: usa una capa de la sesión (p. ej. un límite)")
    return unary_union([g if g.is_valid else g.buffer(0) for g in geoms])


def recortar(fc: dict, geom: Any) -> dict[str, Any]:
    """Deja en `fc` solo los elementos que intersecan `geom`; devuelve el hecho (cuántos había y quedan)."""
    from shapely.geometry import shape
    from shapely.prepared import prep

    area = prep(geom)
    en_rectangulo = len(fc["features"])
    fc["features"] = [f for f in fc["features"] if f.get("geometry") and area.intersects(shape(f["geometry"]))]
    return {"en_su_rectangulo": en_rectangulo, "intersecan_el_area": len(fc["features"]),
            "nota": "solo los que tocan el área (no su rectángulo); un elemento que la toca puede extenderse "
                    "fuera de ella"}
