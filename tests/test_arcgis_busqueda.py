"""Rama arcgis-busqueda: lo que el LLM sabe de lo que encontró en los portales.

V5 (Chrome, como usuario): «necesito una capa con las vías de Bogotá» cargó 462 PUNTOS de la
cartografía básica de Cota y respondió «He cargado las vías de Bogotá». El LLM solo leía
«5 servicio(s) externo(s) encontrados» y eligió «el 1» a ciegas; los hechos de cada candidato
(organización, vistas, capa…) se perdían entre el servidor MCP y el bucle.
"""
from __future__ import annotations

from typing import Any

import pytest

from geo_copilot.agents.data_agent.hub_items import HubItem
from geo_copilot.orchestrator.react_tools import _observe

MOVILIDAD = {
    "id": "a", "source": "arcgis_online", "org": "Secretaría Distrital de Movilidad",
    "title": "Malla Vial Integral Bogota D_C", "description": "Conjunto de líneas que definen los ejes viales",
    "service_type": "FeatureServer",
    "service_url": "https://services2.arcgis.com/NEw/arcgis/rest/services/MVI/FeatureServer",
    "layer_id": None, "owner": "SecretariaMovilidad", "credits": "Secretaría Distrital de Movilidad",
    "views": 60010, "completeness": 98, "single_layer": True, "modified": "2024-07-23",
    "sources": ["arcgis_online", "hub"], "campo_que_no_existe": "se ignora",
}
COTA = {
    "id": "b", "source": "hub", "org": "Departamento de Cundinamarca",
    "title": "Cartografía Básica. Municipio de Cota. Escala 1K. 2022", "description": "Producto cartográfico",
    "service_type": "FeatureServer", "service_url": "https://services7.arcgis.com/x/Cota/FeatureServer",
    "layer_id": None, "owner": "ideradmin", "single_layer": False, "sources": ["hub"],
}


def test_un_item_del_servidor_conserva_los_hechos_para_juzgar_la_fuente():
    it = HubItem.from_dict(MOVILIDAD)
    assert (it.credits, it.views, it.completeness, it.single_layer, it.sources) == (
        "Secretaría Distrital de Movilidad", 60010, 98, True, ["arcgis_online", "hub"])
    d = it.to_dict()
    assert d["views"] == 60010 and d["sources"] == ["arcgis_online", "hub"]


@pytest.mark.asyncio
async def test_los_hechos_llegan_hasta_la_lista_de_servicios_del_agente(monkeypatch):
    from geo_copilot.agents.data_agent import discovery
    from geo_copilot.agents.data_agent.tools import external_apis

    class Respuesta:
        items = [HubItem.from_dict(MOVILIDAD), HubItem.from_dict(COTA)]
        debug: dict[str, Any] = {"search_params": {"text_query": "malla vial"}}
        intent = "vías"
        authority_warning = False
        place_mismatch = False
        place_queried = "Bogotá"
        criterio = "La Malla Vial de Movilidad es la red vial de Bogotá."
        otras_busquedas: list[str] = []
        relevantes = 1

    class Descubridor:
        def __init__(self, llm_client):
            pass

        async def discover(self, *a, **k):
            return Respuesta()

    monkeypatch.setattr(discovery, "DiscoveryAgent", Descubridor)
    res = await external_apis.search_open_data_portals("vías de Bogotá", llm_client=object())
    s1, s2 = res["services"]
    assert (s1["name"], s1["credits"], s1["views"], s1["owner"], s1["single_layer"]) == (
        "Malla Vial Integral Bogota D_C", "Secretaría Distrital de Movilidad", 60010, "SecretariaMovilidad", True)
    assert s2["single_layer"] is False and s2["views"] is None


def test_tras_buscar_el_llm_lee_cada_candidato_con_sus_hechos_no_solo_cuantos_hay():
    encontrados = [
        {"name": "Cartografía Básica. Municipio de Cota. Escala 1K. 2022", "type": "FeatureServer",
         "org": "Departamento de Cundinamarca", "owner": "ideradmin", "single_layer": False,
         "description": "Producto cartográfico básico a escala 1:1.000"},
        {"name": "Malla Vial Integral Bogota D_C", "type": "FeatureServer", "owner": "SecretariaMovilidad",
         "credits": "Secretaría Distrital de Movilidad", "views": 60010, "completeness": 98,
         "single_layer": True, "modified": "2024-07-23", "description": "Ejes viales de la ciudad"},
    ]
    texto, ok = _observe("search_external", {"found_services": encontrados})
    assert ok
    assert "2 candidato(s)" in texto and "busca otra vez" in texto
    assert "1. «Cartografía Básica. Municipio de Cota. Escala 1K. 2022» — Departamento de Cundinamarca " \
           "(cuenta ideradmin) · FeatureServer · servicio con varias capas" in texto
    assert ("2. «Malla Vial Integral Bogota D_C» — Secretaría Distrital de Movilidad (cuenta SecretariaMovilidad)"
            " · FeatureServer · 60.010 vistas · metadatos 98/100 · actualizado 2024-07-23 · una capa") in texto
    assert "Ejes viales de la ciudad" in texto


# ---------------------------------------------------------------------------
# Capa del servicio, nombre y verificación de lo cargado
# ---------------------------------------------------------------------------

COTA_URL = "https://services7.arcgis.com/x/arcgis/rest/services/Cota/FeatureServer"


def _servicio(monkeypatch, capas, *, geometria="Polyline", capa_nombre="Vías"):
    from geo_copilot.agents.data_agent import servicio_arcgis

    pedidas: list[str] = []

    async def describir(url):
        return {"capas": capas}

    async def consultar_capa(url, **kw):
        pedidas.append(url)
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-74.1, 4.6], [-74.0, 4.7]]},
             "properties": {"OBJECTID": 1, "MVINOMBRE": "Av. Boyacá", "MVITIPO": "arterial", "Shape__Length": 9}}]}
        return fc, {"capa": capa_nombre, "tipo_geometria": geometria, "total_en_servicio": 1, "completo": True}

    monkeypatch.setattr(servicio_arcgis, "describir", describir)
    monkeypatch.setattr(servicio_arcgis, "consultar_capa", consultar_capa)
    return pedidas


@pytest.mark.asyncio
async def test_un_servicio_con_varias_capas_no_se_carga_por_la_0_se_ofrecen_sus_capas(monkeypatch):
    """V5: la cartografía de Cota se cargó por su capa 0 (PUNTOS) pidiendo vías."""
    from geo_copilot.orchestrator.nodes.data_agent import _handle_external_url

    pedidas = _servicio(monkeypatch, [{"id": 0, "nombre": "Puntos de control", "tipo_geometria": "Point"},
                                      {"id": 3, "nombre": "Vías", "tipo_geometria": "Polyline"},
                                      {"id": 7, "nombre": "Tabla sin geometría", "tipo_geometria": None}])
    delta = await _handle_external_url(COTA_URL, "Cartografía Básica. Municipio de Cota")
    assert pedidas == []  # no se cargó nada a ciegas
    assert [c["id"] for c in delta["service_layers"]] == [0, 3]  # solo las que tienen geometría
    texto, ok = _observe("select_service", delta)
    assert ok and "select_service(number, layer=<id>)" in texto
    assert "id 0: «Puntos de control» (Point)" in texto and "id 3: «Vías» (Polyline)" in texto


@pytest.mark.asyncio
async def test_con_una_sola_capa_se_carga_esa_con_el_nombre_de_la_fuente_y_sus_hechos(monkeypatch):
    from geo_copilot.orchestrator.nodes.data_agent import _handle_external_url

    pedidas = _servicio(monkeypatch, [{"id": 2, "nombre": "Ejes viales", "tipo_geometria": "Polyline"}],
                        capa_nombre="Ejes viales")
    delta = await _handle_external_url(COTA_URL, "Malla Vial Integral Bogota D_C")
    assert pedidas == [COTA_URL + "/2"]
    assert delta["layer_name"] == delta["external_source_name"] == "Malla Vial Integral Bogota D_C · Ejes viales"
    assert delta["external_layer_facts"]["geometria"] == "Polyline"
    assert delta["external_layer_facts"]["campos"] == ["MVINOMBRE", "MVITIPO"]  # sin OBJECTID ni Shape_*
    texto, _ = _observe("select_service", delta)
    assert "1 elemento(s) (de «Malla Vial Integral Bogota D_C · Ejes viales»; geometría Polyline; " \
           "campos: MVINOMBRE, MVITIPO)" in texto


def test_la_capa_de_un_servicio_mostrado_tiene_procedencia_y_otra_url_no():
    from geo_copilot.orchestrator.nodes.data_agent import _external_url_con_procedencia

    estado = {"found_services": [{"name": "Cota", "url": COTA_URL}]}
    assert _external_url_con_procedencia({**estado, "external_url": COTA_URL + "/3"}, "vías") == COTA_URL + "/3"
    assert _external_url_con_procedencia({**estado, "external_url": COTA_URL + "/3/query?x=1"}, "vías") is None
    assert _external_url_con_procedencia(
        {**estado, "external_url": "https://evil.example.com/arcgis/rest/services/Cota/FeatureServer/3"}, "v") is None


@pytest.mark.asyncio
async def test_select_service_carga_la_capa_elegida_con_el_nombre_que_se_mostro(monkeypatch):
    from geo_copilot.orchestrator import capabilities_core, react_tools

    vistos: dict = {}

    async def run_node(graph, fn, working, extra, clear=None):
        vistos.update(extra)
        return react_tools.ToolOutcome(observation="ok", success=True)

    monkeypatch.setattr(react_tools, "_run_node", run_node)
    working = {"found_services": [{"name": "Cartografía Básica. Municipio de Cota", "url": COTA_URL + "/"}],
               "service_layers_of": {"url": COTA_URL}}  # ya vio las capas de ese servicio
    await capabilities_core._select_service(None, working, {"number": 1, "layer": 3})
    assert vistos["external_url"] == COTA_URL + "/3"
    assert vistos["_nombre_a_cargar"] == "Cartografía Básica. Municipio de Cota"


@pytest.mark.asyncio
async def test_la_aprobacion_de_la_busqueda_muestra_los_candidatos(monkeypatch):
    """V5: el panel decía «ArcGIS Hub · 5 resultados» con el recuadro vacío (0 caracteres)."""
    from geo_copilot.agents.data_agent import agent as mod
    from geo_copilot.security.hitl import HITLResponse, HITLStatus

    async def portales(**kw):
        return {"services": [
            {"id": 1, "name": "Malla Vial Integral Bogota D_C", "type": "FeatureServer",
             "credits": "Secretaría Distrital de Movilidad", "views": 60010, "modified": "2024-07-23", "layer_count": 1},
            {"id": 2, "name": "Cartografía Básica. Municipio de Cota", "type": "FeatureServer",
             "org": "Departamento de Cundinamarca", "layer_count": 0}],
            "query": {"text_query": "malla vial"}}

    pedido: dict = {}

    class Hitl:
        async def request_approval(self, **kw):
            pedido.update(kw)
            return HITLResponse(request_id="r", status=HITLStatus.REJECTED)

    monkeypatch.setattr(mod, "search_open_data_portals", portales)
    monkeypatch.setattr(mod.settings, "hitl_enabled", True)
    agente = mod.DataAgent(hitl_manager=Hitl(), llm_client=object())
    await agente.search_open_data("vías de Bogotá")
    assert pedido["title"] == "ArcGIS · 2 resultados"
    assert pedido["preview"] == ("1. Malla Vial Integral Bogota D_C\n   Secretaría Distrital de Movilidad · "
                                 "FeatureServer · 60.010 vistas · 2024-07-23\n"
                                 "2. Cartografía Básica. Municipio de Cota\n   Departamento de Cundinamarca · FeatureServer")


@pytest.mark.asyncio
async def test_si_la_busqueda_no_se_pudo_hacer_se_dice_por_que_y_no_como_sin_resultados(monkeypatch):
    """V5: la tool quedó deshabilitada (su descripción cambió: re-aprobación) y el agente, con
    «inténtalo de nuevo», respondió «no hay vías de Bogotá en los portales»."""
    from geo_copilot.agents.data_agent.servicio_arcgis import ArcGISNoDisponible
    from geo_copilot.orchestrator.nodes import data_agent as nodo

    class Agente:
        async def process(self, *a, **k):
            raise ArcGISNoDisponible("«arcgis__arcgis_search_items» está deshabilitada: la descripción o el "
                                     "esquema cambió desde que se aprobó; queda deshabilitada hasta que un "
                                     "administrador la re-apruebe")

    class Grafo:
        data_agent = Agente()

    delta = await nodo._handle_external_search(Grafo(), "vías Bogotá", "s1", None)
    assert "no es que no haya resultados" in delta["error"] and "re-apruebe" in delta["error"]


def test_el_orden_acepta_la_fecha_sola_de_arcgis_online():
    """V5: «2024-07-23» (sin zona) contra now(UTC) lanzaba TypeError y la búsqueda entera fallaba."""
    from datetime import UTC, datetime, timedelta

    from geo_copilot.agents.data_agent.hub_items import _modified_recency_bonus, rank_results

    hoy = datetime.now(UTC).date().isoformat()
    assert _modified_recency_bonus(hoy) > 1.9
    assert _modified_recency_bonus((datetime.now(UTC) - timedelta(days=800)).date().isoformat()) == 0.0
    items = [HubItem.from_dict(MOVILIDAD), HubItem.from_dict({**COTA, "modified": "2026-01-01T00:00:00Z"})]
    assert len(rank_results(items, "Bogotá")) == 2


@pytest.mark.asyncio
async def test_el_nombre_no_repite_la_capa_si_es_la_misma_con_otras_tildes(monkeypatch):
    """V5: «Malla Vial Integral de Bogotá D.C. · Malla Vial Integral Bogota D.C»."""
    from geo_copilot.orchestrator.nodes.data_agent import _handle_external_url

    _servicio(monkeypatch, [{"id": 0, "nombre": "x", "tipo_geometria": "Polyline"}],
              capa_nombre="Malla Vial Integral Bogota D.C")
    delta = await _handle_external_url(COTA_URL, "Malla Vial Integral de Bogotá D.C.")
    assert delta["layer_name"] == "Malla Vial Integral de Bogotá D.C."


@pytest.mark.asyncio
async def test_una_capa_que_es_un_grupo_ofrece_las_capas_del_servicio(monkeypatch):
    """V5: «Áreas Protegidas Colombia (sin solapa)» apuntaba a un grupo de capas; el error decía
    «describe el servicio» y el bucle no tiene esa herramienta: probó otro servicio (de minería)."""
    from geo_copilot.agents.data_agent import servicio_arcgis
    from geo_copilot.orchestrator.nodes.data_agent import _handle_external_url

    async def describir(url):
        assert url == COTA_URL  # el servicio, no la capa
        return {"capas": [{"id": 0, "nombre": "Áreas protegidas", "tipo_geometria": None},
                          {"id": 1, "nombre": "Parques Nacionales", "tipo_geometria": "Polygon"},
                          {"id": 2, "nombre": "Reservas", "tipo_geometria": "Polygon"}]}

    async def consultar_capa(url, **kw):
        raise servicio_arcgis.ArcGISNoDisponible("esa capa no tiene geometría (¿es una tabla o un grupo de "
                                                 "capas?); describe el servicio para ver sus capas")

    monkeypatch.setattr(servicio_arcgis, "describir", describir)
    monkeypatch.setattr(servicio_arcgis, "consultar_capa", consultar_capa)
    delta = await _handle_external_url(COTA_URL + "/0", "Áreas Protegidas Colombia (sin solapa)")
    assert [c["id"] for c in delta["service_layers"]] == [1, 2]
    texto, ok = _observe("select_service", delta)
    assert ok and texto.startswith("select_service: la capa 0 no tiene geometría (es un grupo o una tabla); ")
    assert "id 1: «Parques Nacionales» (Polygon)" in texto


@pytest.mark.asyncio
async def test_una_busqueda_no_aprobada_no_se_lee_como_cero_resultados():
    """V5: la aprobación de la 2.ª búsqueda caducó y el bucle leyó «0 elemento(s)»."""
    from geo_copilot.orchestrator.nodes import data_agent as nodo

    class Respuesta:
        success = True
        data = {"search_results": {"status": "cancelled", "caducada": True}}
        message = ""

    class Agente:
        async def process(self, *a, **k):
            return Respuesta()

    class Grafo:
        data_agent = Agente()

    delta = await nodo._handle_external_search(Grafo(), "áreas protegidas", "s1", None)
    texto, ok = _observe("search_external", delta)
    assert not ok and "caducó sin respuesta: no se buscó" in texto


def test_las_fechas_del_hub_en_epoch_salen_como_fecha():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "arcgis_mcp"))
    from arcgis_mcp.hub import normalizar

    it = normalizar({"attributes": {"url": "https://s/x/FeatureServer", "type": "Feature Service",
                                    "modified": 1784823664000, "created": "2024-01-02T00:00:00Z"}})
    assert it["modified"] == "2026-07-23" and it["created"] == "2024-01-02T00:00:00Z"


@pytest.mark.asyncio
async def test_una_capa_adivinada_sin_haber_visto_las_del_servicio_se_ignora(monkeypatch):
    """V4 (LLM real): select_service(1, layer=0) sin haber visto las capas → se cargaba la 0 a ciegas."""
    from geo_copilot.orchestrator import capabilities_core, react_tools

    vistos: dict = {}

    async def run_node(graph, fn, working, extra, clear=None):
        vistos.clear()
        vistos.update(extra)
        return react_tools.ToolOutcome(observation="ok", success=True)

    monkeypatch.setattr(react_tools, "_run_node", run_node)
    working = {"found_services": [{"name": "Cota", "url": COTA_URL}]}
    await capabilities_core._select_service(None, working, {"number": 1, "layer": 0})
    assert vistos["external_url"] == COTA_URL  # sin capa: el servicio ofrecerá las suyas
    working["service_layers_of"] = {"url": COTA_URL}  # ya las vio
    await capabilities_core._select_service(None, working, {"number": 1, "layer": 3})
    assert vistos["external_url"] == COTA_URL + "/3"


def test_el_orden_del_panel_premia_uso_y_autoria_declarada_sobre_una_copia_reciente():
    """V5 panel «malla vial Bogotá»: la copia de un particular (501 vistas, sin créditos, julio 2026)
    salía primera y la Malla Vial de la Secretaría de Movilidad (60.010 vistas, 2024) más abajo."""
    from datetime import UTC, datetime, timedelta

    from geo_copilot.agents.data_agent.hub_items import HubItem, rank_results

    base = {"source": "arcgis_online", "description": "red vial", "service_type": "FeatureServer",
            "service_url": "https://s/x/FeatureServer"}
    copia = HubItem(id="copia", org="ent_aherran", title="Malla Vial Integral de Bogotá D.C.", owner="ent_aherran",
                    views=501, modified=(datetime.now(UTC) - timedelta(days=80)).date().isoformat(), **base)
    oficial = HubItem(id="oficial", org="Secretaría Distrital de Movilidad", title="Malla Vial Integral Bogota D_C",
                      owner="SecretariaMovilidad", credits="Secretaría Distrital de Movilidad", views=60010,
                      completeness=98, modified="2024-07-23", **base)
    assert [i.id for i in rank_results([copia, oficial], place="Bogotá")] == ["oficial", "copia"]
