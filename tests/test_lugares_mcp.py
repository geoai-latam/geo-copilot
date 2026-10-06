"""lugares-mcp: de un nombre de lugar a candidatos (con hechos) y al límite del elegido.

V5 (validando imagery): «el NDVI de Chía en enero de 2025» acababa en «dibuja el área de Chía».
Nominatim simulado con las respuestas reales medidas (Chía: municipio de Cundinamarca, su casco
urbano y un pueblo de Huesca).
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "lugares_mcp"))
from lugares_mcp import server as srv

CHIA_MUNICIPIO = {
    "osm_type": "relation", "osm_id": 10687625, "category": "boundary", "type": "administrative",
    "addresstype": "municipality", "display_name": "Chía, Sabana Centro, Cundinamarca, Colombia", "name": "Chía",
    "lat": "4.86", "lon": "-74.05", "boundingbox": ["4.82", "4.93", "-74.10", "-73.99"],
    "address": {"country": "Colombia", "country_code": "co"},
    "geojson": {"type": "Polygon", "coordinates": [[[-74.10, 4.82], [-73.99, 4.82], [-73.99, 4.93],
                                                    [-74.10, 4.93], [-74.10, 4.82]]]},
}
CHIA_HUESCA = {**CHIA_MUNICIPIO, "osm_id": 340972, "addresstype": "village",
               "display_name": "Chía, Ribagorza, Huesca, Aragón, España",
               "address": {"country": "España", "country_code": "es"}}
PUNTO = {**CHIA_MUNICIPIO, "osm_type": "node", "osm_id": 7, "addresstype": "neighbourhood",
         "geojson": {"type": "Point", "coordinates": [-74.05, 4.86]}}


@pytest.fixture
def nominatim(monkeypatch):
    pedidos: list[tuple[str, dict]] = []
    respuestas: dict[str, list] = {"/search": [CHIA_HUESCA, CHIA_MUNICIPIO], "/lookup": [CHIA_MUNICIPIO]}

    def get(url, params=None, headers=None, timeout=None):
        ruta = url.removeprefix(srv.NOMINATIM)
        pedidos.append((ruta, dict(params or {}), dict(headers or {})))
        return httpx.Response(200, json=respuestas[ruta], request=httpx.Request("GET", url))

    monkeypatch.setattr(srv.httpx, "get", get)
    monkeypatch.setattr(srv, "ESPACIADO_S", 0.0)
    srv._cache.clear()
    return pedidos, respuestas


def test_buscar_devuelve_los_candidatos_con_sus_hechos_y_sin_elegir(nominatim):
    pedidos, _ = nominatim
    sc = srv.lugares_buscar("Chía")
    cands = sc["facts"]["candidatos"]
    assert [c["ref"] for c in cands] == ["R340972", "R10687625"]  # decide quien pide, no el servidor
    assert cands[1] == {"ref": "R10687625", "nombre": "Chía, Sabana Centro, Cundinamarca, Colombia",
                        "que_es": "boundary/administrative", "nivel": "municipality", "pais": "Colombia",
                        "codigo_pais": "co", "tiene_limite": True, "area_km2_aprox": cands[1]["area_km2_aprox"],
                        "centro": [-74.05, 4.86], "bbox": [-74.10, 4.82, -73.99, 4.93]}
    assert 100 < cands[1]["area_km2_aprox"] < 200  # el tamaño distingue un municipio de un barrio
    # F0 (verdades): sale de un límite simplificado; llamada `area_km2`, el agente la daba como el área
    # del lugar (Chía 85,42 km², son 80,03). Se dice aproximada y dónde está la exacta.
    assert "APROXIMADA" in sc["facts"]["nota"] and "lugares_limite" in sc["facts"]["nota"]
    assert sc["artifacts"] == []  # buscar no pinta nada en el mapa
    # política de uso de Nominatim: se identifica
    assert pedidos[0][2]["User-Agent"] == srv.USER_AGENT


def test_codigo_pais_acota_y_se_valida(nominatim):
    pedidos, _ = nominatim
    srv.lugares_buscar("Chía", codigo_pais="CO")
    assert pedidos[-1][1]["countrycodes"] == "co"
    assert "error" in srv.lugares_buscar("Chía", codigo_pais="Colombia")


def test_el_limite_del_elegido_es_una_capa_con_su_area(nominatim):
    pedidos, _ = nominatim
    sc = srv.lugares_limite("r10687625")
    (art,) = sc["artifacts"]
    assert art["kind"] == "feature_collection" and art["crs"] == "EPSG:4326" and art["name"] == "Chía"
    assert art["data"]["features"][0]["geometry"]["type"] == "Polygon"
    assert sc["facts"]["nivel"] == "municipality" and 100 < sc["facts"]["area_km2"] < 200
    assert pedidos[-1][1]["osm_ids"] == "R10687625"
    # un límite es un área de interés: se dibuja como contorno (no tapa el NDVI calculado dentro)
    assert sc["style_hint"] == {"solo_contorno": True}


def test_un_lugar_que_es_solo_un_punto_lo_dice(nominatim):
    _, respuestas = nominatim
    respuestas["/lookup"] = [PUNTO]
    sc = srv.lugares_limite("N7")
    assert sc["artifacts"][0]["name"].endswith("(punto)") and "sin límite" in sc["facts"]["nota"]


def test_entradas_invalidas_y_lugar_inexistente(nominatim):
    _, respuestas = nominatim
    assert "error" in srv.lugares_limite("10687625; DROP")
    assert "error" in srv.lugares_buscar("   ")
    respuestas["/search"] = []
    assert srv.lugares_buscar("Xyzzy")["facts"]["nota"] == "ningún lugar con ese nombre"


def test_la_misma_busqueda_sale_de_la_cache(nominatim):
    pedidos, _ = nominatim
    srv.lugares_buscar("Chía")
    srv.lugares_buscar("Chía")
    assert len(pedidos) == 1


def test_si_el_geocodificador_falla_se_dice(monkeypatch):
    def caido(*a, **k):
        raise httpx.ConnectTimeout("lento")

    monkeypatch.setattr(srv.httpx, "get", caido)
    monkeypatch.setattr(srv, "ESPACIADO_S", 0.0)
    srv._cache.clear()
    assert "no respondió" in srv.lugares_buscar("Chía")["error"]


def _lugar(osm_id: int, nombre: str, nivel: str = "quarter") -> dict:
    return {**CHIA_MUNICIPIO, "osm_id": osm_id, "addresstype": nivel, "display_name": nombre,
            "name": nombre.split(",")[0]}


def test_el_limite_de_una_parte_dice_que_no_es_la_division_que_la_contiene(nominatim):
    """F3: con el aviso de la búsqueda, el agente IGUAL tomaba «UPZs Localidad Chapinero» y la titulaba
    «Límite Localidad Chapinero» (1.992 lotes; la localidad tiene 2.584). El límite lo dice donde se usa."""
    _, respuestas = nominatim
    respuestas["/lookup"] = [_lugar(11249984, "UPZs Localidad Chapinero, Localidad Chapinero, Bogotá ciudad, "
                                              "Bogotá, Colombia")]
    hechos = srv.lugares_limite("R11249984")["facts"]
    assert next(iter(hechos)) == "aviso"
    assert "PARTE de «Localidad Chapinero»" in hechos["aviso"]
    respuestas["/lookup"] = [CHIA_MUNICIPIO]
    assert "aviso" not in srv.lugares_limite("R10687625")["facts"]
