"""amenazas-mcp contra las fuentes REALES (marca `integration`: red; `pytest -m integration`).

Verdades independientes de la herramienta:
- sismos: el `/count` de la USGS con los mismos filtros (Colombia ±, M≥4, oct 2025–oct 2026: 191);
- incendios: un recuento propio del CSV público de FIRMS (csv estándar, sin el código del servidor);
- caudal: el Magdalena en Honda es un río grande (miles de m³/s): la celda más caudalosa de las vecinas lo es,
  aunque la del punto sea un afluente (medido: 1,5 contra ~2 500);
- clima: cada fuente informa la elevación de su malla (Bogotá: ERA5 2 594 m, POWER 1 790 m).
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "amenazas_mcp"))
from amenazas_mcp import server as srv

pytestmark = pytest.mark.integration
COLOMBIA = [-79.1, -4.3, -66.8, 13.5]
VICHADA = [-71.5, 3.5, -67.5, 6.5]


def test_sismos_coinciden_con_el_conteo_directo_de_la_usgs():
    p = {"starttime": "2025-10-15", "endtime": "2026-10-15", "minlatitude": COLOMBIA[1], "maxlatitude": COLOMBIA[3],
         "minlongitude": COLOMBIA[0], "maxlongitude": COLOMBIA[2], "minmagnitude": 4}
    verdad = int(httpx.get("https://earthquake.usgs.gov/fdsnws/event/1/count", params=p, timeout=60).text)
    f = srv.amenazas_sismos(bbox=COLOMBIA, desde="2025-10-15", hasta="2026-10-15", magnitud_min=4).structuredContent
    assert f["facts"]["sismos"] == f["facts"]["en_el_catalogo"] == verdad > 0


def test_incendios_coinciden_con_un_recuento_propio_del_csv():
    texto = httpx.get("https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/"
                      "SUOMI_VIIRS_C2_South_America_7d.csv", timeout=120).text
    verdad = sum(1 for r in csv.DictReader(io.StringIO(texto))
                 if VICHADA[0] <= float(r["longitude"]) <= VICHADA[2] and VICHADA[1] <= float(r["latitude"]) <= VICHADA[3])
    f = srv.amenazas_incendios(bbox=VICHADA, periodo="7d", sensor="viirs_snpp").structuredContent["facts"]
    assert f["focos"] == pytest.approx(verdad, abs=5)  # el CSV se regenera cada pocas horas


def test_el_magdalena_en_honda_es_un_rio_grande_en_las_vecinas():
    f = srv.amenazas_caudal(lon=-74.74, lat=5.20, dias_pasados=7, dias_pronostico=1).structuredContent["facts"]
    assert f["vecinas_por_caudal"][0]["caudal_medio_m3s"] > 500
    assert f["celda_del_punto"]["caudal_medio_m3s"] < f["vecinas_por_caudal"][0]["caudal_medio_m3s"]


def test_cada_fuente_de_clima_dice_su_elevacion():
    era5 = srv.clima_serie("2023-01-01", "2023-01-31", lon=-74.08, lat=4.6, fuente="era5").structuredContent["facts"]
    power = srv.clima_serie("2023-01-01", "2023-01-31", lon=-74.08, lat=4.6, fuente="nasa_power").structuredContent["facts"]
    assert era5["elevacion_modelo_m"] > 2400 and power["elevacion_modelo_m"] < 2000
    assert era5["mensual"][0]["t_media_c"] < power["mensual"][0]["t_media_c"]  # más alto, más frío
