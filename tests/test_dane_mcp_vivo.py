"""dane-mcp contra el DANE REAL (marca `integration`: red; `pytest -m integration`).

Verdades independientes de la herramienta (medidas el 2026-10-15):
- Bogotá en el CNPV 2018: 7 181 469 personas (cifra oficial publicada);
- Chapinero [-74.07, 4.63, -74.05, 4.66]: el PROPIO servidor del DANE suma con su geometría de 2018
  103 379 personas en 762 manzanas; el cruce MGN 2025 ↔ censo 2018 de la herramienta da 103 322 en 760
  (manzanas recodificadas entre 2018 y 2025 quedan «sin dato», y se cuentan).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "dane_mcp"))
from dane_mcp import fuentes
from dane_mcp import server as srv

pytestmark = pytest.mark.integration
CHAPINERO = [-74.07, 4.63, -74.05, 4.66]


@pytest.fixture(autouse=True)
def _dir(monkeypatch, tmp_path):
    monkeypatch.setattr(srv.resultados, "directorio", str(tmp_path))


def test_bogota_tiene_la_cifra_oficial_del_censo_y_su_limite():
    sc = srv.dane_municipios(codigo="11001", con_limite=True).structuredContent
    assert sc["facts"]["municipios"][0]["personas"] == 7_181_469
    assert sc["artifacts"][0]["data"]["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_la_suma_de_manzanas_coincide_con_la_suma_espacial_del_propio_dane():
    envolvente = {"xmin": CHAPINERO[0], "ymin": CHAPINERO[1], "xmax": CHAPINERO[2], "ymax": CHAPINERO[3],
                  "spatialReference": {"wkid": 4326}}
    estad = [{"statisticType": "sum", "onStatisticField": "TP27_PERSO", "outStatisticFieldName": "p"}]
    verdad = httpx.get(f"{fuentes.CENSO}/808/query", timeout=180, params={
        "geometry": json.dumps(envolvente), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outStatistics": json.dumps(estad), "f": "json"}
    ).json()["features"][0]["attributes"]["p"]
    f = srv.dane_censo_manzanas(["TP27_PERSO"], bbox=CHAPINERO).structuredContent["facts"]
    assert f["censo_2018_suma"]["TP27_PERSO"] == pytest.approx(verdad, rel=0.01)
    assert f["con_dato_censo_2018"] >= 0.97 * f["manzanas"]  # el cruce por código casa casi todo


def test_chia_por_nombre_esta_entre_los_candidatos_con_su_departamento():
    hallados = srv.dane_municipios(nombre="chia").structuredContent["facts"]["municipios"]
    chia = [m for m in hallados if m["codigo"] == "25175"]
    assert chia and chia[0]["municipio"] == "CHÍA" and chia[0]["departamento"] == "CUNDINAMARCA"
