"""dane-mcp: censo 2018 (CNPV) integrado al MGN; municipios y manzanas.

Fuentes simuladas con la forma de las reales medidas el 2026-10-15: el REST del censo devuelve atributos sin
geometría y nombres con la codificación rota; el WFS del MGN 2025 trae la geometría con el mismo código de
manzana de 22 dígitos (y alguna clave rota). Verdades reales: tests/test_dane_mcp_vivo.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "dane_mcp"))
from dane_mcp import censo, fuentes
from dane_mcp import server as srv

ROTA = "CHÃ\udc8dA"  # «CHÍA» tal como llega del servidor (UTF-8 leído como Latin-1)
CAMPOS_MANZANA = [{"campo": "COD_DANE_A", "descripcion": "Código"}, {"campo": "TP27_PERSO", "descripcion": "Número de personas"},
                  {"campo": "TP19_ALC_2", "descripcion": "Conteo de viviendas sin servicio de alcantarillado"}]
CAMPOS_MPIO = [{"campo": c, "descripcion": c} for c in ("MPIO_CDPMP", "STP27_PERS", "STVIVIENDA", "TSP16_HOG", "STP34_9_ED")]


def _manzana(cod: str, x: float) -> dict:
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[x, 4.6], [x + 0.001, 4.6],
                                                                               [x + 0.001, 4.601], [x, 4.6]]]},
            "properties": {censo.COD_MANZANA: cod, "Total_viviendas_manzana": 10,
                           "Total_unidades_economicas": 2, "Ã\udc81rea_manzana_en_metros_cuadrados": 900.0}}


@pytest.fixture
def dane(monkeypatch):
    consultas: list[tuple[str, str]] = []

    def consultar(nivel, where, campos):
        consultas.append((nivel, where))
        if nivel == "departamento":
            return [{"DPTO_CCDGO": "25", "DPTO_CNMBR": "CUNDINAMARCA"}, {"DPTO_CCDGO": "85", "DPTO_CNMBR": "CASANARE"}]
        if nivel == "municipio":
            filas = [{"MPIO_CDPMP": "25175", "MPIO_CNMBR": fuentes.reparar(ROTA), "DPTO_CCDGO": "25", "LATITUD": 4.86,
                      "LONGITUD": -74.03, "STP27_PERS": 129613, "STVIVIENDA": 45000, "TSP16_HOG": 44000},
                     {"MPIO_CDPMP": "85225", "MPIO_CNMBR": "NUNCHÍA", "DPTO_CCDGO": "85", "LATITUD": 5.5,
                      "LONGITUD": -72.1, "STP27_PERS": 8469, "STVIVIENDA": 3000, "TSP16_HOG": 2800}]
            return filas if where == "1=1" else [f for f in filas if f["MPIO_CDPMP"] in where]
        return [{"COD_DANE_A": "1100110000000011010101", "TP27_PERSO": 13, "TP19_ALC_2": 0},
                {"COD_DANE_A": "1100110000000011010102", "TP27_PERSO": 442, "TP19_ALC_2": 3}]

    monkeypatch.setattr(fuentes, "consultar_censo", consultar)
    monkeypatch.setattr(fuentes, "campos", lambda nivel: CAMPOS_MANZANA if nivel == "manzana" else CAMPOS_MPIO)
    monkeypatch.setattr(fuentes, "manzanas_mgn", lambda bbox, maximo: [
        _manzana("1100110000000011010101", -74.07), _manzana("1100110000000011010102", -74.06),
        _manzana("1100110000000099999999", -74.05)])  # nueva en 2025: sin dato 2018
    fuentes._cache.clear()
    return consultas


def test_reparar_nombres_y_claves_rotas_sin_tocar_las_buenas():
    assert fuentes.reparar(ROTA) == "CHÍA" and fuentes.reparar("Código") == "Código"
    assert fuentes.reparar("Ã\udc81rea") == "Área" and fuentes.reparar(7) == 7


def test_municipios_por_nombre_sin_tildes_con_totales_oficiales_y_departamento(dane):
    sc = srv.dane_municipios(nombre="chia").structuredContent
    (m,) = [x for x in sc["facts"]["municipios"] if x["codigo"] == "25175"]
    assert m == {"codigo": "25175", "municipio": "CHÍA", "departamento": "CUNDINAMARCA", "centroide": [-74.03, 4.86],
                 "personas": 129613, "viviendas": 45000, "hogares": 44000}
    assert sc["facts"]["encontrados"] == 2  # «chia» también está en «Nunchía»: decide quien pregunta
    assert "2018" in sc["facts"]["nota"] and "no proyecciones" in sc["facts"]["nota"]


def test_codigo_divipola_se_valida_y_las_variables_tambien(dane):
    assert "5 dígitos" in srv.dane_municipios(codigo="1100").structuredContent["error"]
    sc = srv.dane_municipios(codigo="25175", variables=["stp34_9_ed"]).structuredContent
    assert sc["facts"]["municipios"][0]["STP34_9_ED"] is None or "STP34_9_ED" in sc["facts"]["municipios"][0]
    assert "dane_variables" in srv.dane_municipios(codigo="25175", variables=["TP27_PERSO"]).structuredContent["error"]


def test_manzanas_cruzan_por_codigo_suman_y_cuentan_las_que_no_cruzan(dane, monkeypatch, tmp_path):
    monkeypatch.setattr(srv.resultados, "directorio", str(tmp_path))
    sc = srv.dane_censo_manzanas(["TP27_PERSO", "tp19_alc_2"], bbox=[-74.08, 4.59, -74.04, 4.61]).structuredContent
    f = sc["facts"]
    assert f["manzanas"] == 3 and f["con_dato_censo_2018"] == 2 and f["sin_dato_censo_2018"] == 1
    assert f["censo_2018_suma"] == {"TP27_PERSO": 455, "TP19_ALC_2": 3}
    assert f["mgn_2025_suma"] == {"viviendas_recuento_2025": 30, "unidades_economicas_cenu_2025": 6}
    assert "rural DISPERSA" in f["nota"]  # la suma de manzanas no es el total del municipio
    props = sc["artifacts"][0]["data"]["features"][0]["properties"]
    assert props["area_m2"] == 900.0  # clave reparada («Ã\x81rea» → «Área»)
    nueva = sc["artifacts"][0]["data"]["features"][2]["properties"]
    assert nueva["con_dato_censo_2018"] is False and nueva["TP27_PERSO"] is None  # sin dato: vacío, no cero


def test_area_filtra_por_interseccion_y_sin_zona_es_error(dane, monkeypatch, tmp_path):
    monkeypatch.setattr(srv.resultados, "directorio", str(tmp_path))
    caja = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[-74.075, 4.59], [-74.055, 4.59], [-74.055, 4.61], [-74.075, 4.61],
                                            [-74.075, 4.59]]]}}]}
    sc = srv.dane_censo_manzanas(["TP27_PERSO"], area=caja).structuredContent
    assert sc["facts"]["filtro_area"]["intersecan_el_area"] == 2 and sc["facts"]["manzanas"] == 2
    assert "falta la zona" in srv.dane_censo_manzanas(["TP27_PERSO"]).structuredContent["error"]


def test_variables_por_nivel_y_filtro_por_palabras(dane):
    sc = srv.dane_variables("manzana", "alcantarillado").structuredContent
    assert [v["campo"] for v in sc["facts"]["variables"]] == ["TP19_ALC_2"]
    assert "nivel" in srv.dane_variables("barrio").structuredContent["error"]


def test_todas_las_tools_con_scope():
    assert {t.name for t in srv.mcp._tool_manager.list_tools()} == set(srv.SCOPES)


def test_municipios_dice_cuantos_vio_de_cuantos(monkeypatch):
    """Terranova F1.3: se muestran 25; si hay más, la completitud lo dice."""
    monkeypatch.setattr(censo, "municipios", lambda *a, **k: [{"codigo": f"{i:05d}"} for i in range(30)])
    out = srv.dane_municipios(nombre="san")
    c = out.structuredContent["facts"]["completitud"]
    assert c == {"vistos": 25, "total": 30, "truncado": True, "motivo": "se muestran 25"}
