"""La geometría de un resultado SQL es el DATO: se extrae sin simplificar.

V5 F4 (regresión acumulada): «¿cuántas construcciones caen dentro de ese buffer?»
→ el SQL contó 8442; guardadas en el workspace y cruzadas otra vez daban 8431.
Una «red de seguridad» simplificaba el GeoJSON (~5 m) al pasar de 40 000 vértices
para aligerar el mapa, y esa versión deformada era la que se cruzaba y medía: 11
construcciones que tocan el buffer quedaban hasta 2,3 m fuera.
"""
from __future__ import annotations

import json

from geo_copilot.agents.gis_agent.agent import GISAgent


def _poligono_detallado(i: int, n: int = 60) -> dict:
    import math

    cx, cy = -74.1 + i * 1e-4, 4.6
    anillo = [[cx + 1e-5 * math.cos(2 * math.pi * k / n), cy + 1e-5 * math.sin(2 * math.pi * k / n)]
              for k in range(n)]
    return {"type": "Polygon", "coordinates": [[*anillo, anillo[0]]]}


def test_un_resultado_grande_conserva_cada_vertice():
    filas = [{"objectid": i, "geom_geojson": json.dumps(_poligono_detallado(i))} for i in range(1000)]
    fc = GISAgent.__new__(GISAgent)._extract_geojson(filas)
    assert len(fc["features"]) == 1000
    # 61 000 vértices: por encima del antiguo umbral de 40 000
    for fila, feat in zip(filas, fc["features"], strict=True):
        assert feat["geometry"] == json.loads(fila["geom_geojson"])
