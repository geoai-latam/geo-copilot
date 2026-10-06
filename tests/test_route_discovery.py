"""Tests para los endpoints de /api/v1/discovery/*."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from geo_copilot.agents.data_agent.discovery import DiscoveryResponse
from geo_copilot.agents.data_agent.hub_items import HubItem
from geo_copilot.agents.data_agent.servicio_arcgis import ArcGISNoDisponible
from geo_copilot.api.app import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def _sample_item(**kwargs):
    base = {
        "id": "i1",
        "source": "hub",
        "org": "IGAC",
        "title": "Manzanas Bogota",
        "description": "x",
        "service_type": "FeatureServer",
        "service_url": "https://services2.arcgis.com/X/rest/services/Manzanas/FeatureServer/0",
        "owner": "IGAC.Comunicaciones",
        "source_field": "Instituto Geográfico Agustín Codazzi",
        "tags": ["catastro"],
        "type_raw": "Feature Service",
        "rank_score": 10.0,
    }
    base.update(kwargs)
    return HubItem(**base)


class TestSearchEndpoint:
    def test_search_returns_normalized_items(self, client):
        async def fake_discover(self, query, hints=None):
            return DiscoveryResponse(
                items=[_sample_item()],
                intent="entity_focused",
                authority_warning=False,
            )

        with patch(
            "geo_copilot.api.routes.discovery.DiscoveryAgent.discover",
            new=fake_discover,
        ):
            resp = client.post(
                "/api/v1/discovery/search",
                json={"query": "datos IGAC"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["intent"] == "entity_focused"
        assert len(body["items"]) == 1
        assert body["items"][0]["title"] == "Manzanas Bogota"
        assert body["authority_warning"] is False

    def test_search_with_hints(self, client):
        async def fake_discover(self, query, hints=None):
            assert hints is not None
            assert hints.bbox == [-74.22, 4.47, -73.99, 4.83]
            assert "Image Service" in (hints.service_types or [])
            return DiscoveryResponse(items=[], intent="imagery_focused")

        with patch(
            "geo_copilot.api.routes.discovery.DiscoveryAgent.discover",
            new=fake_discover,
        ):
            resp = client.post(
                "/api/v1/discovery/search",
                json={
                    "query": "ortofotos",
                    "hints": {
                        "service_types": ["Image Service"],
                        "bbox": [-74.22, 4.47, -73.99, 4.83],
                    },
                },
            )
        assert resp.status_code == 200


class TestLoadEndpoint:
    def test_feature_server_returns_geojson(self, client):
        """La capa la trae el servidor MCP de ArcGIS (T5.2); el total del servicio llega al cliente."""
        pedidos = []

        async def fake_consultar(url, **kwargs):
            pedidos.append((url, kwargs))
            return ({"type": "FeatureCollection", "features": []},
                    {"total_en_servicio": 5400, "traidos": 0, "completo": False})

        with patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=fake_consultar):
            item = _sample_item().to_dict()
            resp = client.post(
                "/api/v1/discovery/load",
                json={"item": item, "limit": 100},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "geojson"
        assert body["service_type"] == "FeatureServer"
        assert body["geojson"]["type"] == "FeatureCollection"
        assert body["total_available"] == 5400
        assert pedidos == [(item["service_url"], {"max_features": 100})]

    def test_si_el_servidor_de_arcgis_falla_se_dice(self, client):
        async def caido(url, **kwargs):
            raise ArcGISNoDisponible("el servicio 'arcgis' no respondió (ConnectError)")

        with patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=caido):
            resp = client.post("/api/v1/discovery/load", json={"item": _sample_item().to_dict()})
        assert resp.status_code == 502 and "no respondió" in resp.json()["detail"]

    def test_image_server_returns_imagery_descriptor(self, client):
        async def fake_describir(url):
            return {"tipo": "ImageServer", "extent_4326": [-74, 4, -73, 5],
                    "imagen": {"type": "imagery", "service_url": url.rstrip("/"),
                               "export_url": url.rstrip("/") + "/exportImage", "extent": None}}

        with patch("geo_copilot.agents.data_agent.servicio_arcgis.describir", new=fake_describir):
            item = _sample_item(
                service_type="ImageServer",
                type_raw="Image Service",
                service_url="https://services2.arcgis.com/X/rest/services/Ortofoto/ImageServer",
            ).to_dict()
            resp = client.post("/api/v1/discovery/load", json={"item": item})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "imagery"
        assert body["service_type"] == "ImageServer"
        assert body["imagery"]["service_url"].endswith("/ImageServer")
        assert body["imagery"]["export_url"].endswith("/exportImage")
        assert body["extent"] == {"xmin": -74, "ymin": 4, "xmax": -73, "ymax": 5} == body["imagery"]["extent"]

    def test_mapserver_sin_extension_usa_la_del_hub_solo_si_es_lonlat(self, client):
        async def sin_extension(url):
            return {"tipo": "MapServer", "extent_4326": None,
                    "imagen": {"type": "imagery", "service_url": url, "export_url": None, "extent": None}}

        url = "https://services2.arcgis.com/X/rest/services/Vias/MapServer"
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.describir", new=sin_extension):
            en_grados = client.post("/api/v1/discovery/load", json={"item": _sample_item(
                service_type="MapServer", service_url=url, extent=[-74.2, 4.5, -74.0, 4.8]).to_dict()})
            en_metros = client.post("/api/v1/discovery/load", json={"item": _sample_item(
                service_type="MapServer", service_url=url, extent=[-8238310, 504757, -8226310, 516757]).to_dict()})
        assert en_grados.json()["extent"] == {"xmin": -74.2, "ymin": 4.5, "xmax": -74.0, "ymax": 4.8}
        assert en_metros.json()["extent"] is None  # mejor sin vuelo que volar a otro continente

    def test_unsupported_service_type_400(self, client):
        item = _sample_item(service_type="GeoJSON").to_dict()
        resp = client.post("/api/v1/discovery/load", json={"item": item})
        assert resp.status_code == 400

    def test_paused_plan_dispatches_pending_paint_on_load(self):
        """#35 (audit Docker 2026-06): cargar una capa desde una card cuando el
        plan quedó PAUSADO (cadena buscar→cargar→pintar) despacha las
        ``pending_operations`` POR EL GRAFO sobre la capa cargada por identidad
        (URL) y devuelve la simbología ya pintada; las pendientes se consumen.
        """
        from unittest.mock import MagicMock

        from geo_copilot.api.dependencies import (
            get_agent_graph,
            get_conversation_manager,
            get_llm_client,
        )

        red_symbology = {
            "symbology_type": "single_symbol",
            "fill": {"color": "#e41a1c"},
            "layer_title": "Bomberos",
        }
        loaded_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-74, 4]},
                "properties": {"id": 1},
            }],
        }

        # Grafo mock: el despacho del paint pendiente devuelve la simbología roja.
        mock_graph = MagicMock()
        mock_graph.process = AsyncMock(
            return_value={"symbology": red_symbology, "geojson": loaded_geojson}
        )

        # Contexto de sesión con un plan PAUSADO (pending paint).
        cleared: dict = {}
        ctx = MagicMock()
        ctx.get_variable = MagicMock(
            side_effect=lambda k: (
                [{"query": "aplica simbología color rojo"}]
                if k == "pending_operations" else None
            )
        )
        ctx.get_messages_for_llm = MagicMock(return_value=[])
        ctx.set_variable = MagicMock(side_effect=lambda k, v: cleared.__setitem__(k, v))
        mock_cm = MagicMock()
        mock_cm.get_session = MagicMock(return_value=ctx)

        async def fake_query(url, **kwargs):
            return loaded_geojson, {"completo": True}

        app = create_app()
        app.dependency_overrides[get_agent_graph] = lambda: mock_graph
        app.dependency_overrides[get_conversation_manager] = lambda: mock_cm
        # Sin LLM → se salta la simbología por defecto; aislamos el despacho.
        app.dependency_overrides[get_llm_client] = lambda: None
        local_client = TestClient(app)

        with patch(
            "geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa",
            new=fake_query,
        ):
            item = _sample_item().to_dict()
            resp = local_client.post(
                "/api/v1/discovery/load",
                json={"item": item, "limit": 100, "session_id": "s-paused"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # La simbología devuelta es la PINTADA por el despacho (no la de item.title).
        assert body["symbology"] == red_symbology
        # El grafo recibió la query de pintado pendiente + la capa cargada.
        mock_graph.process.assert_awaited_once()
        kw = mock_graph.process.await_args.kwargs
        assert "rojo" in kw["query"]
        assert kw["external_geojson"] == loaded_geojson
        assert kw["has_external_data"] is True
        # Las operaciones pendientes se consumieron.
        assert "pending_operations" in cleared and cleared["pending_operations"] is None

    def test_unknown_session_skips_dispatch(self):
        """Ownership: si la sesión declarada NO existe (get_session → None) no se
        despacha ningún plan — un session_id spoofeado/desconocido es no-op."""
        from unittest.mock import MagicMock

        from geo_copilot.api.dependencies import (
            get_agent_graph,
            get_conversation_manager,
            get_llm_client,
        )

        loaded = {"type": "FeatureCollection", "features": []}
        mock_graph = MagicMock()
        mock_graph.process = AsyncMock()
        mock_cm = MagicMock()
        mock_cm.get_session = MagicMock(return_value=None)  # sesión desconocida

        async def fake_query(url, **kwargs):
            return loaded, {"completo": True}

        app = create_app()
        app.dependency_overrides[get_agent_graph] = lambda: mock_graph
        app.dependency_overrides[get_conversation_manager] = lambda: mock_cm
        app.dependency_overrides[get_llm_client] = lambda: None
        local_client = TestClient(app)

        with patch(
            "geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa",
            new=fake_query,
        ):
            item = _sample_item().to_dict()
            resp = local_client.post(
                "/api/v1/discovery/load",
                json={"item": item, "limit": 100, "session_id": "s-fantasma"},
            )

        assert resp.status_code == 200, resp.text
        # No se despachó nada porque la sesión no existe.
        mock_graph.process.assert_not_awaited()

    def test_invalid_session_id_rejected(self, client):
        """Un session_id con caracteres inválidos se rechaza con 400."""
        item = _sample_item().to_dict()
        resp = client.post(
            "/api/v1/discovery/load",
            json={"item": item, "limit": 100, "session_id": "bad id/../x"},
        )
        assert resp.status_code == 400
        assert "session_id" in resp.json()["detail"]

    def test_load_without_session_skips_dispatch(self, client):
        """Sin ``session_id`` (carga directa desde el panel 'Datos', sin plan
        pendiente) NO se despacha nada: carga normal con simbología por defecto.
        """
        async def fake_query(url, **kwargs):
            return {"type": "FeatureCollection", "features": []}, {"completo": True}

        with patch(
            "geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa",
            new=fake_query,
        ):
            item = _sample_item().to_dict()
            resp = client.post("/api/v1/discovery/load", json={"item": item, "limit": 100})

        assert resp.status_code == 200, resp.text
        assert resp.json()["type"] == "geojson"


class TestHealth:
    def test_discovery_health(self, client):
        resp = client.get("/api/v1/discovery/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestCapaElegida:
    """Rama arcgis-busqueda (V5): el panel cargaba siempre la capa 0 — en la cartografía de Cota eran
    PUNTOS y el RUNAP no tiene capa 0 (su única capa es la 59)."""

    RAIZ = "https://services3.arcgis.com/X/arcgis/rest/services/RUNAP/FeatureServer"

    @staticmethod
    def _describir(capas):
        async def describir(url):
            return {"capas": capas}
        return describir

    @staticmethod
    def _consultar(pedidas):
        async def consultar(url, max_features=2000, **_):
            pedidas.append(url)
            return ({"type": "FeatureCollection", "features": []}, {"total_en_servicio": 0})
        return consultar

    def test_lista_solo_las_capas_con_geometria(self, client):
        capas = [{"id": 0, "nombre": "Grupo", "tipo_geometria": None},
                 {"id": 3, "nombre": "Vías", "tipo_geometria": "Polyline"},
                 {"id": 5, "nombre": "Construcciones", "tipo_geometria": "Polygon"}]
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.describir", new=self._describir(capas)):
            resp = client.post("/api/v1/discovery/layers", json={"service_url": self.RAIZ})
        assert resp.status_code == 200, resp.text
        assert resp.json()["layers"] == [{"id": 3, "nombre": "Vías", "tipo_geometria": "Polyline"},
                                         {"id": 5, "nombre": "Construcciones", "tipo_geometria": "Polygon"}]

    def test_capas_rechaza_urls_internas(self, client):
        resp = client.post("/api/v1/discovery/layers",
                           json={"service_url": "http://169.254.169.254/arcgis/rest/services/x/FeatureServer"})
        assert resp.status_code == 400

    def test_un_servicio_de_una_sola_capa_carga_esa_y_no_la_0(self, client):
        pedidas: list[str] = []
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.describir",
                   new=self._describir([{"id": 59, "nombre": "RUNAP", "tipo_geometria": "Polygon"}])), \
             patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=self._consultar(pedidas)):
            resp = client.post("/api/v1/discovery/load", json={"item": _sample_item(service_url=self.RAIZ).to_dict()})
        assert resp.status_code == 200, resp.text
        assert pedidas == [f"{self.RAIZ}/59"]

    def test_con_varias_capas_y_ninguna_elegida_no_adivina(self, client):
        pedidas: list[str] = []
        capas = [{"id": 0, "nombre": "Puntos", "tipo_geometria": "Point"},
                 {"id": 3, "nombre": "Vías", "tipo_geometria": "Polyline"}]
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.describir", new=self._describir(capas)), \
             patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=self._consultar(pedidas)):
            resp = client.post("/api/v1/discovery/load", json={"item": _sample_item(service_url=self.RAIZ).to_dict()})
        assert resp.status_code == 409 and "2 capas" in resp.json()["detail"]
        assert pedidas == []

    def test_la_capa_elegida_se_carga_y_da_nombre(self, client):
        pedidas: list[str] = []
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=self._consultar(pedidas)):
            resp = client.post("/api/v1/discovery/load", json={
                "item": _sample_item(service_url=self.RAIZ, title="Cota").to_dict(),
                "layer_id": 3, "layer_name": "Vías"})
        assert resp.status_code == 200, resp.text
        assert pedidas == [f"{self.RAIZ}/3"] and resp.json()["name"] == "Cota · Vías"

    def test_los_hechos_del_item_llegan_a_la_tarjeta(self, client):
        async def fake_discover(self, query, hints=None):
            return DiscoveryResponse(items=[_sample_item(credits="Secretaría Distrital de Movilidad", views=60010,
                                                         completeness=98, single_layer=True,
                                                         sources=["arcgis_online"])], intent="topic_focused")
        with patch("geo_copilot.api.routes.discovery.DiscoveryAgent.discover", new=fake_discover):
            it = client.post("/api/v1/discovery/search", json={"query": "malla vial"}).json()["items"][0]
        assert (it["credits"], it["views"], it["completeness"], it["single_layer"], it["sources"]) == (
            "Secretaría Distrital de Movilidad", 60010, 98, True, ["arcgis_online"])



class TestCapaCompleta:
    """El panel y las tarjetas del chat pedían 2000 elementos: el usuario veía una muestra."""

    def test_sin_limite_pide_la_capa_completa_si_hay_workspace(self, client, monkeypatch):
        from geo_copilot.api.routes import discovery as rutas
        from geo_copilot.core.config import get_settings

        pedidos: list[int] = []

        async def consultar(url, max_features=None, **_):
            pedidos.append(max_features)
            return ({"type": "FeatureCollection", "features": []}, {"total_en_servicio": 0})

        monkeypatch.setattr(rutas, "_hay_workspace", lambda *_: True)
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=consultar):
            client.post("/api/v1/discovery/load", json={"item": _sample_item().to_dict(), "session_id": "s-1"})
        assert pedidos == [get_settings().max_external_features]

    def test_sin_workspace_trae_lo_que_cabe_inline_y_dice_que_es_muestra(self, client, monkeypatch):
        from geo_copilot.api.routes import discovery as rutas
        from geo_copilot.core.config import get_settings

        pedidos: list[int] = []

        async def consultar(url, max_features=None, **_):
            pedidos.append(max_features)
            fc = {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74, 4]}, "properties": {}}]}
            return fc, {"total_en_servicio": 9000, "completo": False}

        monkeypatch.setattr(rutas, "_hay_workspace", lambda *_: False)
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=consultar):
            r = client.post("/api/v1/discovery/load", json={"item": _sample_item().to_dict()})
        assert pedidos == [get_settings().workspace_inline_max_features]
        assert r.json()["total_available"] == 9000 and r.json()["feature_count"] == 1

    def test_una_capa_grande_responde_teselas_y_no_el_geojson(self, client, monkeypatch):
        from geo_copilot.api.routes import discovery as rutas

        tiles = {"url": "/api/v1/tiles/ws/s-1/ds_aaaaaaaaaaaaaaaa/{z}/{x}/{y}.pbf", "feature_count": 136956}
        muestra = {"type": "FeatureCollection", "features": []}

        async def al_ws(*_a, **_k):
            return {"id": "ds_aaaaaaaaaaaaaaaa"}, tiles, None, muestra

        async def consultar(url, max_features=None, **_):
            feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74, 4]}, "properties": {}}] * 3
            return {"type": "FeatureCollection", "features": feats}, {"total_en_servicio": 3}

        monkeypatch.setattr(rutas, "_hay_workspace", lambda *_: True)
        monkeypatch.setattr(rutas, "_al_workspace", al_ws)
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=consultar):
            body = client.post("/api/v1/discovery/load",
                               json={"item": _sample_item().to_dict(), "session_id": "s-1"}).json()
        assert body["geojson"] is None and body["tiles"] == tiles
        assert body["dataset_id"] == "ds_aaaaaaaaaaaaaaaa" and body["feature_count"] == 3

    def test_completa_aunque_un_elemento_no_tenga_geometria_no_se_llama_muestra(self, client, monkeypatch):
        """V5: la malla vial trajo 136.956 de 136.957 (uno sin geometría) y decía «es una muestra»."""
        from geo_copilot.api.routes import discovery as rutas

        async def consultar(url, max_features=None, **_):
            fc = {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74, 4]}, "properties": {}}]}
            return fc, {"total_en_servicio": 2, "completo": True}

        monkeypatch.setattr(rutas, "_hay_workspace", lambda *_: False)
        with patch("geo_copilot.agents.data_agent.servicio_arcgis.consultar_capa", new=consultar):
            r = client.post("/api/v1/discovery/load", json={"item": _sample_item().to_dict()})
        assert r.json()["total_available"] is None
