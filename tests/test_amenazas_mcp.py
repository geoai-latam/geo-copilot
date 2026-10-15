"""amenazas-mcp: sismos (USGS), incendios (FIRMS), caudales (GloFAS) y clima (ERA5 / NASA POWER).

Fuentes simuladas con la forma de las respuestas reales medidas el 2026-10-15. Las verdades contra las fuentes
reales (conteo directo de la USGS, el CSV de FIRMS, el Magdalena en Honda) están en
tests/test_amenazas_mcp_vivo.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "amenazas_mcp"))
from amenazas_mcp import clima, fuentes, resumen
from amenazas_mcp import server as srv


def _celda(lat, lon, q):
    return {"latitude": lat, "longitude": lon, "daily": {"time": ["2026-10-14", "2026-10-15"], "river_discharge": [q, q]}}


def test_caudal_da_la_celda_del_punto_y_las_vecinas_por_caudal(monkeypatch):
    """Medido: en Honda la celda del punto da 1,5 m³/s y a ~5 km el Magdalena ~2 500."""
    monkeypatch.setattr(fuentes, "caudal", lambda *a: [_celda(5.175, -74.725, 1.5), _celda(5.275, -74.775, 2546.0),
                                                       _celda(5.225, -74.775, 2491.0), _celda(5.075, -74.875, 0.0)])
    sc = srv.amenazas_caudal(lon=-74.74, lat=5.20).structuredContent
    f = sc["facts"]
    assert f["celda_del_punto"] == {"lat": 5.175, "lon": -74.725, "caudal_medio_m3s": 1.5}
    assert [v["caudal_medio_m3s"] for v in f["vecinas_por_caudal"][:2]] == [2546.0, 2491.0]
    assert "VECINA" in f["nota"] and "modelo" in f["nota"]
    assert sc["artifacts"][0]["rows"] == [{"fecha": "2026-10-14", "caudal_m3s": 1.5}, {"fecha": "2026-10-15", "caudal_m3s": 1.5}]


def test_sismos_hechos_y_recorte_por_area(monkeypatch):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-76.25, 4.86, 116.1]},
         "properties": {"mag": 7.4, "magType": "mww", "place": "San José del Palmar", "time": 1760000000000, "url": "u"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-67.2, 10.6, 10]},
         "properties": {"mag": 7.5, "magType": "mww", "place": "Catia La Mar", "time": 1760000000000, "url": "v"}}]}
    monkeypatch.setattr(fuentes, "sismos", lambda *a: (fc, 2))
    area = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[-79, -4], [-70, -4], [-70, 12], [-79, 12], [-79, -4]]]}}]}
    sc = srv.amenazas_sismos(area=area, desde="2025-10-15", hasta="2026-10-15").structuredContent
    assert sc["facts"]["en_el_catalogo"] == 2 and sc["facts"]["sismos"] == 1  # el de Venezuela queda fuera
    assert sc["facts"]["zona_consultada_bbox"] == [-79.0, -4.0, -70.0, 12.0]  # qué zona se miró, explícito
    props = sc["artifacts"][0]["data"]["features"][0]["properties"]
    assert props["profundidad_km"] == 116.1 and props["fecha"].endswith("UTC") and "time" not in props


def test_incendios_valida_periodo_y_sensor(monkeypatch):
    monkeypatch.setattr(fuentes, "incendios", lambda *a: [
        {"longitude": -70.1, "latitude": 5.1, "acq_date": "2026-10-13", "acq_time": "0610", "confidence": "nominal",
         "frp": "25.23", "daynight": "N"}])
    sc = srv.amenazas_incendios(bbox=[-71.5, 3.5, -67.5, 6.5]).structuredContent
    assert sc["facts"]["focos"] == 1 and sc["facts"]["frp_max_mw"] == 25.23
    assert "no todo foco es un incendio" in sc["facts"]["fuente"]
    assert "periodo" in srv.amenazas_incendios(bbox=[-71, 3, -67, 6], periodo="30d").structuredContent["error"]


def test_firms_filtra_el_csv_regional_por_la_zona(monkeypatch):
    csv = ("latitude,longitude,frp,acq_date\n5.1,-70.1,1.2,2026-10-13\n-20.9,-51.7,3.4,2026-10-13\n"
           "x,y,1,2026-10-13\n")
    monkeypatch.setattr(fuentes, "_get", lambda url, params=None, timeout=90: type("R", (), {"text": csv})())
    filas = fuentes.incendios([-71.5, 3.5, -67.5, 6.5], "7d", "viirs_snpp")
    assert [(f["longitude"], f["latitude"]) for f in filas] == [(-70.1, 5.1)]


def test_clima_power_ignora_el_sin_dato_y_resume_por_mes(monkeypatch):
    monkeypatch.setattr(fuentes, "clima_power", lambda *a: {
        "geometry": {"coordinates": [-74.08, 4.6, 1790.38]},
        "properties": {"parameter": {"T2M": {"20230101": 17.0, "20230102": 18.0, "20230201": -999.0},
                                     "T2M_MAX": {}, "T2M_MIN": {},
                                     "PRECTOTCORR": {"20230101": 5.0, "20230102": 1.0, "20230201": 2.0}}}})
    sc = srv.clima_serie("2023-01-01", "2023-02-01", lon=-74.08, lat=4.6, fuente="nasa_power").structuredContent
    f = sc["facts"]
    assert f["elevacion_modelo_m"] == 1790.38 and "SU elevación" in f["nota"]
    assert f["precipitacion_total_periodo_mm"] == 8.0 and f["t_media_periodo_c"] == 17.5  # sin sumas a mano
    assert f["mensual"] == [{"mes": "2023-01", "dias": 2, "t_media_c": 17.5, "precipitacion_total_mm": 6.0},
                            {"mes": "2023-02", "dias": 1, "t_media_c": None, "precipitacion_total_mm": 2.0}]


def test_clima_valida_fechas_fuente_y_pronostico():
    assert "AAAA-MM-DD" in srv.clima_serie("ayer", "hoy", lon=-74, lat=4.6).structuredContent["error"]
    assert "no pronostica" in srv.clima_serie("2026-10-15", "2030-01-01", lon=-74, lat=4.6,
                                              fuente="nasa_power").structuredContent["error"]
    assert "fuente" in srv.clima_serie("2023-01-01", "2023-01-02", lon=-74, lat=4.6, fuente="x").structuredContent["error"]
    assert clima.mensual([]) == []


def test_zona_y_punto_validan():
    with pytest.raises(resumen.ErrorAmenazas, match="falta la zona"):
        resumen.zona(None, [1, 2, 3])
    with pytest.raises(resumen.ErrorAmenazas, match="hace falta UNO"):
        resumen.punto({"features": []}, None, None)
    assert resumen.punto(None, -74.0, 4.6) == (-74.0, 4.6)


def test_todas_las_tools_con_scope():
    assert {t.name for t in srv.mcp._tool_manager.list_tools()} == set(srv.SCOPES)
