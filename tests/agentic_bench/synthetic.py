"""Capas sintéticas DETERMINISTAS para el benchmark agéntico.

Sin aleatoriedad: cada llamada produce exactamente el mismo GeoJSON, para que
los scores del benchmark sean comparables entre corridas y ramas.
"""

from __future__ import annotations

# Centro aproximado de Bogotá — coherente con los datos reales de catastro.
_LON0, _LAT0 = -74.10, 4.62


def synthetic_points(n: int = 60) -> dict:
    """``n`` puntos en una grilla 8×N con atributos analizables.

    Propiedades por feature:
      - ``uso``: categórica de 3 valores (residencial/comercial/industrial)
      - ``valor``: numérica creciente con 3 outliers deliberados (idx 10/30/50)
      - ``pisos``: numérica pequeña 1..6
      - ``nombre``: identificador legible
    """
    feats = []
    for i in range(n):
        valor = 100 + i * 10
        if i in (10, 30, 50):  # outliers para tareas de detección
            valor *= 20
        feats.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [
                    round(_LON0 + (i % 8) * 0.008, 6),
                    round(_LAT0 + (i // 8) * 0.008, 6),
                ],
            },
            "properties": {
                "uso": ("residencial", "comercial", "industrial")[i % 3],
                "valor": valor,
                "pisos": 1 + (i % 6),
                "nombre": f"punto_{i:03d}",
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def synthetic_polygons(n: int = 24) -> dict:
    """``n`` cuadrados con áreas crecientes, para tareas de áreas/distribución."""
    feats = []
    for i in range(n):
        side = 0.001 + (i % 6) * 0.0005  # ~110m .. ~380m de lado
        x = _LON0 + (i % 6) * 0.01
        y = _LAT0 + (i // 6) * 0.01
        feats.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [x, y], [x + side, y], [x + side, y + side], [x, y + side], [x, y],
                ]],
            },
            "properties": {
                "zona": ("norte", "sur", "oriente", "occidente")[i % 4],
                "categoria": ("A", "B")[i % 2],
                "id_zona": i,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


GENERATORS = {
    "synthetic_points": synthetic_points,
    "synthetic_polygons": synthetic_polygons,
}


def build_layer(name: str) -> dict:
    """Resuelve el nombre declarado en ``setup.previous_geojson`` del YAML."""
    if name not in GENERATORS:
        raise KeyError(f"capa sintética desconocida: {name!r} (hay: {sorted(GENERATORS)})")
    return GENERATORS[name]()
