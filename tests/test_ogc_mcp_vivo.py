"""ogc-mcp contra los servicios REALES (marca `integration`: red, lento; `pytest -m integration`).

La verdad se mide con una consulta DIRECTA e independiente al servicio (resultType=hits), no con la
herramienta: lo que devuelve la herramienta debe coincidir. Medido el 2026-10-15: RUNAP en
[-74.5, 4, -73.5, 5] = 169 (PNN); manzanas IGAC en Luruaco = 151; departamentos DIVIPOLA 2025 = 33.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ogc_mcp"))
from ogc_mcp import catalogo, ows
from ogc_mcp import server as srv

pytestmark = pytest.mark.integration

PNN_WFS = "https://mapas.parquesnacionales.gov.co/services/pnn/wfs"
IGAC_CATASTRO = "https://mapas.igac.gov.co/server/services/Dato_Fundamental_Catastro/MapServer/WFSServer"
DANE_DIVIPOLA = "https://geoportal.dane.gov.co/mparcgis/services/Divipola/Serv_DIVIPOLA_MGN_2025/MapServer/WFSServer"


@pytest.fixture(autouse=True)
def _claves(monkeypatch, tmp_path):
    monkeypatch.setenv("OGC_MCP_KEYS", '[{"name":"t","key":"k","scopes":["ogc:read"]}]')
    monkeypatch.setattr(srv, "RESULTADOS", str(tmp_path))


def _features(sc: dict) -> list[dict]:
    """Los elementos del artefacto: en línea o, si pesaban mucho, del GeoJSON servido por referencia."""
    art = sc["artifacts"][0]
    if art["kind"] == "feature_collection":
        return art["data"]["features"]
    ruta = Path(srv.RESULTADOS) / art["uri"].split("/")[-1]
    return json.loads(ruta.read_text(encoding="utf-8"))["features"]


def _hits_directo(url: str, capa: str, bbox: list[float] | None = None) -> int:
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": capa,
              "resultType": "hits"}
    if bbox:
        params["bbox"] = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]},urn:ogc:def:crs:EPSG::4326"
    texto = httpx.get(url, params=params, timeout=180).text
    return int(re.search(r'numberMatched="(\d+)"', texto).group(1))


@pytest.mark.parametrize(("url", "capa", "corto", "bbox"), [
    (PNN_WFS, "pnn:runap", "runap", [-74.5, 4.0, -73.5, 5.0]),
    (IGAC_CATASTRO, "Dato_Fundamental_Catastro:U_MANZANA", "U_MANZANA", [-75.17, 10.58, -75.11, 10.64]),
    (DANE_DIVIPOLA, "Serv_DIVIPOLA_MGN_2025:Departamento", "Departamento", None),
])
def test_la_herramienta_trae_lo_mismo_que_cuenta_el_servicio(url, capa, corto, bbox):
    verdad = _hits_directo(url, capa, bbox)
    assert verdad > 0
    sc = srv.ogc_traer_elementos(url, corto, tipo="wfs", bbox=bbox, max_elementos=10000).structuredContent
    hechos = sc["facts"]
    sufijo = "_dentro_del_filtro" if bbox else ""
    assert hechos["elementos"] == verdad and hechos[f"en_el_servicio{sufijo}"] == verdad
    assert hechos[f"completa{sufijo}"] is True
    feats = _features(sc)
    assert len(feats) == verdad
    if bbox:  # lon/lat de verdad: cada elemento cae en (o toca) el bbox pedido
        x0, y0, x1, y1 = bbox
        for f in feats[:50]:
            pts = re.findall(r"\[(-?\d+\.\d+), (-?\d+\.\d+)\]", str(f["geometry"]["coordinates"]))[:20]
            assert any(x0 - 0.5 <= float(x) <= x1 + 0.5 and y0 - 0.5 <= float(y) <= y1 + 0.5 for x, y in pts)


def test_divipola_2025_tiene_los_33_departamentos():
    sc = srv.ogc_traer_elementos(DANE_DIVIPOLA, "Departamento", tipo="wfs", max_elementos=100).structuredContent
    nombres = {f["properties"]["Nombre_departamento"] for f in _features(sc)}
    assert len(nombres) == 33 and any("BOGOT" in n.upper() for n in nombres)


def test_la_tesela_wms_de_un_servicio_que_no_declara_3857_trae_imagen():
    url = "https://srvags.sgc.gov.co/arcgis/services/Amenaza_Sismica/Amenaza_Sismica_Nacional/MapServer/WMSServer"
    sc = srv.ogc_ver_mapa(url, "5").structuredContent
    assert "lo atiende" in sc["facts"]["crs_teselas"]


@pytest.mark.parametrize("entrada", catalogo.cargar(), ids=lambda e: e["id"])
def test_cada_entrada_del_catalogo_responde_por_sus_protocolos(entrada):
    for tipo in ("wms", "wfs", "ogcapi"):
        if entrada.get(tipo):
            caps = ows.describir(entrada[tipo], tipo)
            assert caps["capas"], f"{entrada['id']} {tipo}: sin capas"


def test_area_real_bogota_deja_fuera_lo_que_solo_toca_el_rectangulo():
    """V5: el rectángulo de Bogotá trajo Tibaitatá (Mosquera). Con el área, solo lo que la interseca."""
    from shapely.geometry import box

    bogota_urbana = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                     "geometry": box(-74.20, 4.47, -74.01, 4.83).__geo_interface__}]}
    sc = srv.ogc_traer_elementos(PNN_WFS, "runap", tipo="wfs", area=bogota_urbana).structuredContent
    filtro = sc["facts"]["filtro_area"]
    assert filtro["intersecan_el_area"] == sc["facts"]["elementos"] == len(_features(sc))
    assert filtro["intersecan_el_area"] <= filtro["en_su_rectangulo"]
