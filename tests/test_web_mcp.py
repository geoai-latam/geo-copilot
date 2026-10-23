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


NORMA = """<html><head><title>Ley 1712 de 2014 - Gestor Normativo</title><script>var x = 1;</script></head>
<body><nav>Inicio | Normas</nav><div id="contenido"><h1>LEY 1712 DE 2014</h1>
<p>Por medio de la cual se crea la Ley de Transparencia.</p>
<p><b>ARTÍCULO 1.</b> Objeto. El objeto de la presente ley es regular el derecho de acceso.</p>
<p><b>ARTÍCULO 2.</b> Principio de máxima publicidad.</p>
<a href="docs/resolucion_creg_101_2026.htm">Resolución 101 de 2026</a> <a href="/otra.html">otra</a>
</div><footer>© Función Pública</footer></body></html>"""
URL_NORMA = "https://relatoria.colombiacompra.gov.co/eva/norma.php?i=56882"


def test_html_saca_titulo_y_texto_sin_menus_ni_scripts(monkeypatch) -> None:
    """Terranova (G4 parte 2): normas del Gestor Normativo y sentencias de la Corte en HTML estático."""
    _red(monkeypatch, {URL_NORMA: Respuesta(NORMA, cabeceras={"content-type": "text/html; charset=utf-8"})})
    sc = srv.web_html(URL_NORMA).structuredContent
    fila = sc["artifacts"][0]["rows"][0]
    assert fila["titulo"] == "Ley 1712 de 2014 - Gestor Normativo"
    assert "ARTÍCULO 1. Objeto." in fila["texto"] and "var x" not in fila["texto"]
    assert "Inicio | Normas" not in fila["texto"] and "Función Pública" not in fila["texto"]
    assert sc["facts"]["completitud"]["truncado"] is False


def test_html_busca_fragmentos_y_corta_con_aviso(monkeypatch) -> None:
    _red(monkeypatch, {URL_NORMA: Respuesta(NORMA, cabeceras={"content-type": "text/html"})})
    sc = srv.web_html(URL_NORMA, buscar="artículo 2").structuredContent
    assert sc["facts"]["apariciones"] == 1 and "máxima publicidad" in sc["artifacts"][0]["rows"][0]["texto"]
    corto = srv.web_html(URL_NORMA, max_caracteres=20).structuredContent
    assert corto["facts"]["completitud"]["truncado"] is True and len(corto["artifacts"][0]["rows"][0]["texto"]) == 20


def test_html_lista_enlaces_de_un_indice(monkeypatch) -> None:
    _red(monkeypatch, {URL_NORMA: Respuesta(NORMA, cabeceras={"content-type": "text/html"})})
    sc = srv.web_html(URL_NORMA, enlaces_con=r"resolucion_creg_\d+").structuredContent
    assert sc["artifacts"][0]["rows"] == [{"texto": "Resolución 101 de 2026",
                                           "url": "https://relatoria.colombiacompra.gov.co/eva/docs/resolucion_creg_101_2026.htm"}]


def test_html_rechaza_lo_que_no_es_html(monkeypatch) -> None:
    _red(monkeypatch, {URL_NORMA: Respuesta("%PDF-1.7", cabeceras={"content-type": "application/pdf"})})
    sc = srv.web_html(URL_NORMA).structuredContent
    assert "no es HTML" in sc["error"]


def test_toda_tool_tiene_scope() -> None:
    """Fail-closed: una tool registrada sin scope respondería 403."""
    import asyncio

    nombres = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    assert nombres <= set(srv.SCOPES), sorted(nombres - set(srv.SCOPES))


def test_html_no_se_come_la_pagina_con_un_menu_sin_cerrar() -> None:
    """La CREG envuelve la página en un <header> sin cerrar: sin la salvaguarda, 0 de 125 enlaces."""
    from web_mcp import paginas

    html = "<html><body><header>Menú<div><a href='docs/resolucion_creg_1_2026.htm'>Res 1</a> texto largo de la norma</div></body></html>"
    _, texto, enlaces = paginas.leer(html, "https://gestornormativo.creg.gov.co/gestor/entorno/x.html")
    assert "texto largo de la norma" in texto and len(enlaces) == 1
    form = "<form><p>ARTÍCULO 1. Objeto.</p></form>"
    assert "ARTÍCULO 1" in paginas.leer(form, "https://x.gov.co/")[1]


def test_html_respeta_el_charset_del_meta() -> None:
    from web_mcp import paginas

    crudo = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1252"></head><body>identificación</body></html>'.encode("cp1252")
    assert "identificación" in paginas.decodificar(crudo, "text/html")
