"""rutas-mcp contra el motor REAL (marca `integration`: red; `pytest -m integration`).

Verdades independientes de la herramienta:
- la DISTANCIA de una ruta contra OSRM (otro motor, otra implementación sobre los mismos datos OSM);
- las isócronas por consistencia: lo que queda DENTRO de la de 15 min se alcanza en ~15 min con una ruta
  real y lo de FUERA no;
- la matriz contra la ruta suelta del mismo par.
Medido el 2026-10-15: Plaza de Bolívar → Chía 33,09 km (OSRM 33,94).
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "rutas_mcp"))
from rutas_mcp import server as srv

pytestmark = pytest.mark.integration
PLAZA = (-74.0760, 4.5981)
CHIA = (-74.0325, 4.8615)


def _ruta_min(a, b) -> float:
    return srv.rutas_ruta(origen_lon=a[0], origen_lat=a[1], destino_lon=b[0], destino_lat=b[1]).structuredContent[
        "facts"]["tiempo_min"]


def test_la_distancia_coincide_con_otro_motor():
    f = srv.rutas_ruta(origen_lon=PLAZA[0], origen_lat=PLAZA[1], destino_lon=CHIA[0],
                       destino_lat=CHIA[1]).structuredContent["facts"]
    osrm = httpx.get(f"https://router.project-osrm.org/route/v1/driving/{PLAZA[0]},{PLAZA[1]};{CHIA[0]},{CHIA[1]}",
                     params={"overview": "false"}, timeout=60).json()["routes"][0]["distance"] / 1000
    assert f["distancia_km"] == pytest.approx(osrm, rel=0.10)


def test_la_isocrona_es_consistente_con_rutas_reales():
    from shapely.geometry import Point, shape

    sc = srv.rutas_isocronas([15], centro_lon=PLAZA[0], centro_lat=PLAZA[1]).structuredContent
    zona = shape(sc["artifacts"][0]["data"]["features"][0]["geometry"])
    x1, y1, x2, y2 = zona.bounds
    dentro = [zona.representative_point()] + [p for p in (Point(x1 + (x2 - x1) * f, (y1 + y2) / 2)
                                                           for f in (0.3, 0.7)) if zona.contains(p)]
    fuera = [Point(x2 + (x2 - x1) * 0.6, (y1 + y2) / 2), Point((x1 + x2) / 2, y2 + (y2 - y1) * 0.6)]
    for p in dentro:  # las isócronas se generalizan: margen del 35 %
        assert _ruta_min(PLAZA, (p.x, p.y)) <= 15 * 1.35
    for p in fuera:
        assert _ruta_min(PLAZA, (p.x, p.y)) >= 15 * 0.8


def test_la_matriz_coincide_con_la_ruta_suelta():
    sc = srv.rutas_matriz(origenes_lonlat=[list(PLAZA)], destinos_lonlat=[list(CHIA)]).structuredContent
    celda = sc["artifacts"][0]["rows"][0]
    ruta = srv.rutas_ruta(origen_lon=PLAZA[0], origen_lat=PLAZA[1], destino_lon=CHIA[0],
                          destino_lat=CHIA[1]).structuredContent["facts"]
    assert celda["km"] == pytest.approx(ruta["distancia_km"], rel=0.03)
