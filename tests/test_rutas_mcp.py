"""rutas-mcp: ruta, isócronas y matriz sobre Valhalla.

Motor simulado con la forma de las respuestas reales medidas el 2026-10-15 (FOSSGIS, Valhalla 3.9.1).
Verdades contra el motor real y contra OSRM (otro motor): tests/test_rutas_mcp_vivo.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "rutas_mcp"))
from rutas_mcp import puntos, valhalla
from rutas_mcp import server as srv


def _codificar(coords: list[list[float]], precision: int = 6) -> str:
    """Codificador de polilíneas (el de Google, con precisión 6 como Valhalla) para fabricar respuestas."""
    salida, prev_lat, prev_lon, f = [], 0, 0, 10 ** precision
    for lon, lat in coords:
        for valor, prev in ((round(lat * f), prev_lat), (round(lon * f), prev_lon)):
            d = valor - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                salida.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            salida.append(chr(d + 63))
        prev_lat, prev_lon = round(lat * f), round(lon * f)
    return "".join(salida)


LINEA = [[-74.076433, 4.598343], [-74.05, 4.7], [-74.031837, 4.862609]]


@pytest.fixture
def motor(monkeypatch):
    pedidos: list[tuple[str, dict]] = []

    def pedir(accion, cuerpo):
        pedidos.append((accion, cuerpo))
        if accion == "route":
            return {"trip": {"summary": {"length": 33.086, "time": 4290}, "legs": [{
                "shape": _codificar(LINEA), "maneuvers": [{"instruction": "Conduzca hacia el suroeste por Carrera 8."}]}]}}
        if accion == "isochrone":
            return {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"contour": m}, "geometry": {"type": "Polygon", "coordinates": [[
                    [-74.1, 4.57], [-74.05, 4.57], [-74.05, 4.57 + m / 300], [-74.1, 4.57 + m / 300], [-74.1, 4.57]]]}}
                for m in (c["time"] for c in cuerpo["contours"])]}
        if accion == "sources_to_targets":
            return {"sources_to_targets": [[{"distance": 10.95, "time": 2100}, {"distance": 10.38, "time": 1950},
                                            {"distance": None, "time": None}]]}
        raise AssertionError(accion)

    monkeypatch.setattr(valhalla, "pedir", pedir)
    return pedidos


def test_decodificar_es_el_inverso_de_la_polilinea_de_valhalla():
    assert valhalla.decodificar(_codificar(LINEA)) == LINEA


def test_ruta_con_numeros_da_linea_y_hechos(motor):
    sc = srv.rutas_ruta(origen_lon=-74.076, origen_lat=4.5981, destino_lon=-74.0325, destino_lat=4.8615).structuredContent
    f = sc["facts"]
    assert f["distancia_km"] == 33.09 and f["tiempo_min"] == 71.5 and f["modo"] == "auto"
    assert "sin tráfico en vivo" in f["motor"] and f["indicaciones"] == ["Conduzca hacia el suroeste por Carrera 8."]
    assert sc["artifacts"][0]["data"]["features"][0]["geometry"]["coordinates"] == LINEA
    accion, cuerpo = motor[0]
    assert cuerpo["costing"] == "auto" and cuerpo["language"] == "es-ES" and cuerpo["units"] == "kilometers"


def test_ruta_desde_una_referencia_de_un_elemento_y_rechazo_de_varios(motor):
    punto = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                                                       "geometry": {"type": "Point", "coordinates": [-74.076, 4.598]}}]}
    sc = srv.rutas_ruta(origen=punto, destino_lon=-74.03, destino_lat=4.86).structuredContent
    assert sc["facts"]["origen"] == [-74.076, 4.598]
    dos = {"type": "FeatureCollection", "features": punto["features"] * 2}
    assert "hace falta UNO" in srv.rutas_ruta(origen=dos, destino_lon=-74, destino_lat=4.8).structuredContent["error"]
    lote = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.09, 4.6], [-74.09, 4.61], [-74.1, 4.61], [-74.1, 4.6]]]}}]}
    sc = srv.rutas_ruta(origen=lote, destino_lon=-74.03, destino_lat=4.86).structuredContent
    assert "punto interior" in sc["facts"]["notas"][0]  # de un polígono se dice qué punto se usó


def test_extremos_mal_dados_son_errores_que_dicen_que_dar(motor):
    assert "origen_lon" in srv.rutas_ruta(destino_lon=-74, destino_lat=4).structuredContent["error"]
    assert "invertiste" in srv.rutas_ruta(origen_lon=4.6, origen_lat=-174.0, destino_lon=-74,
                                         destino_lat=4).structuredContent["error"]
    assert "modo" in srv.rutas_ruta(origen_lon=-74, origen_lat=4, destino_lon=-74.1, destino_lat=4.1,
                                    modo="avion").structuredContent["error"]


def test_isocronas_con_area_por_tiempo_y_limites(motor):
    sc = srv.rutas_isocronas([30, 15], centro_lon=-74.076, centro_lat=4.598).structuredContent
    areas = sc["facts"]["area_km2_por_tiempo"]
    assert set(areas) == {"15 min", "30 min"} and areas["30 min"] > areas["15 min"] > 0
    assert [c["time"] for c in motor[0][1]["contours"]] == [15, 30]
    for malos in ([], [0], [121], [5, 10, 15, 20, 25]):
        assert "minutos" in srv.rutas_isocronas(malos, centro_lon=-74, centro_lat=4).structuredContent["error"]


def test_matriz_con_el_mas_cercano_en_tiempo_y_sin_ruta_dicho(motor):
    sc = srv.rutas_matriz(origenes_lonlat=[[-74.076, 4.598]],
                          destinos_lonlat=[[-74.05, 4.67], [-74.13, 4.62], [-80.0, 1.0]]).structuredContent
    filas = sc["artifacts"][0]["rows"]
    assert filas[1] == {"origen": "#0", "destino": "#1", "km": 10.38, "min": 32.5}
    assert filas[2]["km"] is None and filas[2]["min"] is None  # sin ruta: vacío, no cero
    assert sc["facts"]["mas_cercano_por_origen"] == [{"origen": "#0", "destino_mas_cercano": "#1", "min": 32.5}]


def test_matriz_rechaza_mas_de_50(motor):
    muchos = [[-74 + i / 1000, 4.6] for i in range(51)]
    assert "máx. 50" in srv.rutas_matriz(origenes_lonlat=muchos, destinos_lonlat=muchos[:2]).structuredContent["error"]


def test_area_geodesica_de_un_cuadrado_de_un_grado_en_el_ecuador():
    cuadro = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    assert puntos.area_km2(cuadro) == pytest.approx(12308, rel=0.01)  # ~111 km × 111 km


def test_todas_las_tools_con_scope():
    assert {t.name for t in srv.mcp._tool_manager.list_tools()} == set(srv.SCOPES)
