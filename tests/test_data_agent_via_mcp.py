"""T5.2 — el nodo data_agent carga servicios ArcGIS a través del servidor MCP de ArcGIS.

Sin conectores en el núcleo: una capa vectorial viene de `arcgis_query_features` (y si es una
muestra, el mensaje lo dice), una imagen de `arcgis_describe_service` (su extensión en 4326 y
el descriptor para el mapa), y si el servidor falla el usuario lo sabe (no «0 resultados»).
"""
from __future__ import annotations

import json

import pytest

from geo_copilot.agents.data_agent import servicio_arcgis
from geo_copilot.orchestrator.nodes import data_agent as nodo

URL = "https://services.example.org/arcgis/rest/services/Sedes/FeatureServer/0"
FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]}, "properties": {"n": 1}}]}


@pytest.mark.asyncio
async def test_una_capa_que_es_muestra_se_dice_en_el_mensaje(monkeypatch):
    pedidos = []

    async def consultar(url, **kw):
        pedidos.append((url, kw))
        return FC, {"total_en_servicio": 5400, "traidos": 1, "completo": False}

    monkeypatch.setattr(servicio_arcgis, "consultar_capa", consultar)
    out = await nodo._handle_external_url(URL)
    assert out["has_external_data"] and out["geojson"] is FC and out["external_source_url"] == URL
    msg = out["messages"][0]
    assert msg["success"] and "MUESTRA: el servicio tiene 5400" in msg["content"]
    assert msg["data"]["total_en_servicio"] == 5400
    assert pedidos == [(URL, {})]  # sin tope: completa (max_external_features, paginada en el servidor)


@pytest.mark.asyncio
async def test_si_el_servidor_falla_el_error_llega_al_usuario(monkeypatch):
    async def caido(url, **kw):
        raise servicio_arcgis.ArcGISNoDisponible("el servicio 'arcgis' no respondió (ConnectError)")

    monkeypatch.setattr(servicio_arcgis, "consultar_capa", caido)
    out = await nodo._handle_external_url(URL)
    assert "no respondió" in out["final_response"] and out["messages"][0]["success"] is False


@pytest.mark.asyncio
async def test_una_imagen_se_monta_con_el_descriptor_y_la_extension_del_servidor(monkeypatch):
    url = "https://x.org/arcgis/rest/services/Orto/ImageServer"

    async def describir(u):
        return {"tipo": "ImageServer", "extent_4326": [-74.2, 4.5, -74.0, 4.8],
                "imagen": {"type": "imagery", "service_url": u, "export_url": u + "/exportImage", "extent": None}}

    monkeypatch.setattr(servicio_arcgis, "describir", describir)
    out = await nodo._handle_external_url(url)
    img = out["external_imagery"]
    assert img["export_url"].endswith("/exportImage") and img["service_kind"] == "ImageServer"
    assert img["extent"] == {"xmin": -74.2, "ymin": 4.5, "xmax": -74.0, "ymax": 4.8}


@pytest.mark.asyncio
async def test_una_capa_por_referencia_se_descarga_del_servidor_entera(monkeypatch):
    """E2.1: lo grande viene como feature_ref; la fachada lo baja por el hub (rutas declaradas)."""
    pedidas = []

    class Hub:
        async def llamar_directo(self, servidor, tool, args):
            return {"geo_result": "1", "facts": {"completo": True, "total_en_servicio": 1},
                    "artifacts": [{"kind": "feature_ref", "crs": "EPSG:4326", "name": "Sedes", "format": "geojson",
                                   "uri": "/resultados/abc.geojson", "feature_count": 1}]}

        async def descargar_recurso(self, servidor, ruta):
            pedidas.append((servidor, ruta))
            return json.dumps(FC).encode()

    monkeypatch.setattr("geo_copilot.platform.mcp.hub.hub_actual", lambda: Hub())
    fc, hechos = await servicio_arcgis.consultar_capa(URL, max_features=200_000)
    assert fc["features"] == FC["features"] and hechos["completo"] is True
    assert pedidas == [("arcgis", "/resultados/abc.geojson")]


@pytest.mark.asyncio
async def test_el_hub_solo_descarga_de_las_rutas_declaradas_del_servidor():
    from geo_copilot.platform.mcp.config import McpConfig
    from geo_copilot.platform.mcp.connection import McpError
    from geo_copilot.platform.mcp.hub import McpHub

    hub = McpHub(McpConfig.model_validate({"servers": [{
        "id": "arcgis", "url": "http://arcgis-mcp:9400/mcp", "tools": {"allow": ["arcgis_*"]}, "recursos": {"prefixes": ["/resultados/"]}}]}))
    for ruta in ("/mcp", "/resultados/../mcp", "resultados/x.geojson"):
        with pytest.raises(McpError, match="no está entre los recursos"):
            await hub.descargar_recurso("arcgis", ruta)
    with pytest.raises(McpError, match="no está configurado"):
        await hub.descargar_recurso("otro", "/resultados/x.geojson")


@pytest.mark.asyncio
async def test_sin_servidor_de_arcgis_configurado_se_dice(monkeypatch):
    monkeypatch.setattr("geo_copilot.platform.mcp.hub.hub_actual", lambda: None)
    with pytest.raises(servicio_arcgis.ArcGISNoDisponible, match="falta el de ArcGIS"):
        await servicio_arcgis.consultar_capa(URL)


@pytest.mark.asyncio
async def test_la_fachada_saca_la_capa_y_los_hechos_del_resultado_del_servidor(monkeypatch):
    llamadas = []

    class Hub:
        async def llamar_directo(self, servidor, tool, args):
            llamadas.append((servidor, tool, args))
            return {"geo_result": "1", "facts": {"completo": True},
                    "artifacts": [{"kind": "feature_collection", "crs": "EPSG:4326", "name": "Sedes", "data": FC}]}

    monkeypatch.setattr("geo_copilot.platform.mcp.hub.hub_actual", lambda: Hub())
    fc, hechos = await servicio_arcgis.consultar_capa(URL, where="SECTOR='OFICIAL'", max_features=50)
    assert fc is FC and hechos == {"completo": True}
    # los argumentos vacíos no viajan (el servidor aplica sus valores por defecto)
    assert llamadas == [("arcgis", "arcgis_query_features", {"url": URL, "where": "SECTOR='OFICIAL'",
                                                              "max_features": 50})]


@pytest.mark.asyncio
async def test_la_busqueda_del_chat_ordena_sobre_un_lote_amplio_y_muestra_los_primeros(monkeypatch):
    """V5 T5.2: con solo 10 pedidos al Hub, lo pertinente listado más abajo no llegaba a ordenarse."""
    from geo_copilot.agents.data_agent import discovery
    from geo_copilot.agents.data_agent.hub_items import HubItem
    from geo_copilot.agents.data_agent.tools.external_apis import search_open_data_portals

    pedidos = {}

    async def discover(self, query, *, hints=None, conversation_history=None):
        pedidos["max_results"] = hints.max_results
        items = [HubItem(id=str(i), source="hub", org="o", title=f"t{i}", description="",
                         service_type="FeatureServer", service_url=f"https://x/{i}/FeatureServer") for i in range(30)]
        return discovery.DiscoveryResponse(items=items, intent="topic_focused")

    monkeypatch.setattr(discovery.DiscoveryAgent, "discover", discover)
    out = await search_open_data_portals("equipamientos de Cundinamarca", limit=10)
    assert pedidos["max_results"] == 50 and len(out["services"]) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("desde_bucle, hay_servicios, al_bucle", [(False, True, True), (True, True, False),
                                                                 (False, False, False)])
async def test_con_servicios_conectados_la_busqueda_en_portales_pasa_por_el_bucle(
        monkeypatch, desde_bucle, hay_servicios, al_bucle):
    """T5.4 (V5): «muéstrame en el mapa las sedes de Soacha» iba a buscar en portales (6/6) aunque
    una base conectada lo tenía. Con servicios conectados, el bucle decide; si el bucle ya eligió
    buscar en portales, no se le devuelve el turno (sin recursión)."""
    from geo_copilot.orchestrator.nodes import agent_loop

    llamadas = {"bucle": [], "hub": []}

    async def bucle(graph, state):
        llamadas["bucle"].append(state)
        return {"final_response": "desde el bucle"}

    async def hub_search(graph, query, session_id, *a, **k):
        llamadas["hub"].append(query)
        return {"final_response": "desde el hub"}

    monkeypatch.setattr(agent_loop, "run", bucle)
    monkeypatch.setattr(nodo, "_handle_external_search", hub_search)
    monkeypatch.setattr(nodo, "_al_bucle_antes_de_portales", lambda: hay_servicios)
    state = {"query": "muéstrame en el mapa las sedes de Soacha", "intent": "search_external", "session_id": "s",
             **({"_desde_bucle": True} if desde_bucle else {})}
    out = await nodo.run(None, state)
    assert bool(llamadas["bucle"]) is al_bucle and bool(llamadas["hub"]) is (not al_bucle)
    if al_bucle:
        assert "portales" in llamadas["bucle"][0]["interpretacion_previa"] and out["final_response"] == "desde el bucle"


@pytest.mark.asyncio
@pytest.mark.parametrize("desde_bucle, al_bucle", [(False, True), (True, False)])
async def test_cargar_sin_url_con_servicios_conectados_pasa_al_bucle(monkeypatch, desde_bucle, al_bucle):
    """T5.6 (V5): «carga los predios del GeoParquet que caen en @Área 1» → load_external sin URL;
    lo que se quiere cargar está en un servicio conectado."""
    from geo_copilot.orchestrator.nodes import agent_loop

    llamadas = []

    async def bucle(graph, state):
        llamadas.append(state)
        return {"final_response": "desde el bucle"}

    monkeypatch.setattr(agent_loop, "run", bucle)
    monkeypatch.setattr(nodo, "_al_bucle_antes_de_portales", lambda: True)
    graph = type("G", (), {"db_pool": object()})()
    out = await nodo.run(graph, {"query": "carga los predios del GeoParquet", "intent": "load_external",
                                 "session_id": "s", **({"_desde_bucle": True} if desde_bucle else {})})
    assert bool(llamadas) is al_bucle
    if al_bucle:
        assert "no hay una URL" in llamadas[0]["interpretacion_previa"]
    else:
        assert out["messages"][0]["data"] == {"validated": True}


@pytest.mark.asyncio
async def test_una_url_sin_procedencia_falla_diciendo_por_que():
    """V3 F5: el bucle pasó como URL `data:` el GeoJSON de otra herramienta; la respuesta era
    «ok, 0 elementos» (validaba la BD) y el LLM reintentó tres veces."""
    graph = type("G", (), {"db_pool": object()})()
    out = await nodo.run(graph, {"query": "muéstrame las sedes", "intent": "load_external", "session_id": "s",
                                 "external_url": "data:application/json,{\"type\":\"FeatureCollection\"}",
                                 "_desde_bucle": True})
    assert out.get("error") and "procedencia" in out["final_response"]


@pytest.mark.asyncio
async def test_una_url_inventada_por_el_router_no_corta_el_traspaso_al_bucle(monkeypatch):
    """V3 F5: el router rellenó external_url con «(se espera que el usuario proporcione la URL…)»;
    el control de procedencia la descarta y, con servicios conectados, decide el bucle."""
    from geo_copilot.orchestrator.nodes import agent_loop

    llamadas = []

    async def bucle(graph, state):
        llamadas.append(state)
        return {"final_response": "desde el bucle"}

    monkeypatch.setattr(agent_loop, "run", bucle)
    monkeypatch.setattr(nodo, "_al_bucle_antes_de_portales", lambda: True)
    graph = type("G", (), {"db_pool": object()})()
    out = await nodo.run(graph, {"query": "carga los predios del GeoParquet", "intent": "load_external",
                                 "session_id": "s",
                                 "external_url": "(se espera que el usuario proporcione la URL del GeoParquet)"})
    assert llamadas and out["final_response"] == "desde el bucle"
