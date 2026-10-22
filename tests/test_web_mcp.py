"""web-mcp (Terranova F5, G4): APIs JSON y SDMX con lista de dominios, robots.txt y turno. Sin red."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "web_mcp"))
from web_mcp import fuentes
from web_mcp import server as srv

SDMX_ESPECIFICO = """<?xml version="1.0"?>
<message:StructureSpecificData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
  <message:DataSet>
    <Series FREQ="D" UNIT_MEASURE="COP"><Obs TIME_PERIOD="2026-10-20" OBS_VALUE="3912.5"/>
      <Obs TIME_PERIOD="2026-10-21" OBS_VALUE="3920.1"/></Series>
  </message:DataSet>
</message:StructureSpecificData>"""
SDMX_GENERICO = """<?xml version="1.0"?>
<message:GenericData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
  xmlns:generic="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic">
  <message:DataSet><generic:Series>
    <generic:SeriesKey><generic:Value id="FREQ" value="M"/></generic:SeriesKey>
    <generic:Obs><generic:ObsDimension value="2026-09"/><generic:ObsValue value="9.25"/></generic:Obs>
  </generic:Series></message:DataSet>
</message:GenericData>"""


class Respuesta:
    def __init__(self, texto: str = "", json: object = None, cabeceras: dict | None = None) -> None:
        self.text, self._json, self.headers = texto, json, cabeceras or {}

    def json(self) -> object:
        if self._json is None:
            raise ValueError("no es JSON")
        return self._json


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("WEB_MCP_DOMINIOS", "relatoria.colombiacompra.gov.co,totoro.banrep.gov.co")
    monkeypatch.setattr(fuentes, "INTERVALO_S", 0.0)
    fuentes._robots.clear()
    fuentes._ultima.clear()


def _red(monkeypatch, respuestas: dict[str, Respuesta], robots: str = "User-agent: *\nAllow: /\n") -> list[str]:
    pedidas: list[str] = []

    def get(url, params=None, **kw):
        pedidas.append(url)
        if url.endswith("/robots.txt"):
            return Respuesta(robots)
        return respuestas[url]

    monkeypatch.setattr(fuentes.red, "get", get)
    return pedidas


def test_sdmx_en_sus_dos_formas() -> None:
    assert fuentes.filas_sdmx(SDMX_ESPECIFICO) == [
        {"FREQ": "D", "UNIT_MEASURE": "COP", "TIME_PERIOD": "2026-10-20", "OBS_VALUE": "3912.5"},
        {"FREQ": "D", "UNIT_MEASURE": "COP", "TIME_PERIOD": "2026-10-21", "OBS_VALUE": "3920.1"},
    ]
    assert fuentes.filas_sdmx(SDMX_GENERICO) == [{"FREQ": "M", "TIME_PERIOD": "2026-09", "OBS_VALUE": "9.25"}]
    with pytest.raises(fuentes.ErrorWeb, match="SDMX-ML"):
        fuentes.filas_sdmx("<html>mantenimiento")


def test_json_aplana_y_toma_el_total_de_wordpress(monkeypatch) -> None:
    url = "https://relatoria.colombiacompra.gov.co/wp-json/wp/v2/conceptos"
    datos = [{"id": 1, "title": {"rendered": "C-1394 de 2026"}}, {"id": 2, "title": {"rendered": "C-1408"}}]
    _red(monkeypatch, {url: Respuesta(json=datos, cabeceras={"X-WP-Total": "7695"})})
    sc = srv.web_json(url, params={"per_page": 2}).structuredContent
    assert sc["artifacts"][0]["rows"][0] == {"id": 1, "title.rendered": "C-1394 de 2026"}
    assert sc["facts"]["completitud"] == {"vistos": 2, "total": 7695, "truncado": True}


def test_robots_txt_se_respeta(monkeypatch) -> None:
    url = "https://relatoria.colombiacompra.gov.co/wp-admin/x"
    pedidas = _red(monkeypatch, {}, robots="User-agent: *\nDisallow: /wp-admin/\n")
    sc = srv.web_json(url).structuredContent
    assert "robots.txt" in sc["error"] and sc["codigo"] == "parametro_invalido"
    assert pedidas == ["https://relatoria.colombiacompra.gov.co/robots.txt"]  # no se tocó la página


def test_solo_https_y_dominios_permitidos(monkeypatch) -> None:
    _red(monkeypatch, {})
    assert "https" in srv.web_json("http://relatoria.colombiacompra.gov.co/x").structuredContent["error"]
    monkeypatch.setenv("WEB_MCP_DOMINIOS", "")
    assert srv.web_json("https://relatoria.colombiacompra.gov.co/x").structuredContent["codigo"] == "error_interno"


def test_un_sitio_a_la_vez(monkeypatch) -> None:
    dormido: list[float] = []
    monkeypatch.setattr(fuentes, "INTERVALO_S", 1.0)
    monkeypatch.setattr(fuentes.time, "sleep", dormido.append)
    fuentes._esperar_turno("totoro.banrep.gov.co")
    fuentes._esperar_turno("totoro.banrep.gov.co")
    assert dormido and dormido[0] > 0.9  # la segunda petición espera su turno


def test_las_tools_tienen_scope() -> None:
    assert set(srv.SCOPES) == {"web_json", "web_sdmx"}
