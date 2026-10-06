"""T2.0a — el workspace es el almacenamiento de las capas: estado por referencia.

El cliente manda las capas respaldadas por el workspace SOLO con su
`dataset_id`; el backend lee la geometría del workspace de la sesión. La sesión
recuerda la capa del turno anterior por id, sin copiar la geometría.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api import dependencies as deps
from geo_copilot.api.app import create_app
from geo_copilot.orchestrator.conversation import ConversationContext, ConversationManager
from geo_copilot.platform.workspace import WorkspaceError

FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74, 4.6]},
     "properties": {"lotcodigo": "004500"}},
]}


def _ref(dataset_id: str, n: int = 1) -> dict:
    """Un LayerRef del contrato, como lo guarda el workspace."""
    return {"id": dataset_id, "name": "Lotes", "kind": "vector", "provider": "core", "crs": "EPSG:4326",
            "storage": {"kind": "workspace-table", "schema_name": "ws_prueba", "table": "d_prueba"},
            "provenance": {"capability": "core.query_data", "produced_at": "2026-09-25T00:00:00Z"},
            "feature_count": n}


class StoreFalso:
    """Workspace de una sola sesión: `sess-a` tiene el dataset `ds_a`."""

    def __init__(self):
        self.ingest_features = AsyncMock(return_value=MagicMock(
            model_dump=MagicMock(return_value=_ref("ds_nuevo"))))
        self.pedidos: list[tuple[str, str]] = []

    async def get(self, ws, dataset_id):
        if (ws, dataset_id) == ("sess-a", "ds_a"):
            return SimpleNamespace(
                feature_count=len(FC["features"]),
                model_dump=lambda mode=None: _ref("ds_a"),
            )
        if (ws, dataset_id) == ("sess-a", "ds_grande"):
            return SimpleNamespace(feature_count=10**6)
        return None

    async def to_geojson(self, ws, dataset_id, *, limit=10_000):
        self.pedidos.append((ws, dataset_id))
        if (ws, dataset_id) == ("sess-a", "ds_a"):
            return FC
        raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión")


@pytest.fixture
def entorno(monkeypatch):
    store = StoreFalso()
    monkeypatch.setattr(deps, "get_app_state", lambda: SimpleNamespace(dataset_store=store))
    monkeypatch.setattr("geo_copilot.api.websocket.send_result", AsyncMock())
    monkeypatch.setattr("geo_copilot.api.websocket.send_status", AsyncMock())

    graph = MagicMock()
    graph.process = AsyncMock(return_value={
        "success": True, "message": "listo", "intent": "query_data",
        "geojson": FC, "layer_name": "Lotes", "data": {"results": []},
    })
    app = create_app()
    manager = ConversationManager()
    app.dependency_overrides[deps.get_conversation_manager] = lambda: manager
    app.dependency_overrides[deps.get_agent_graph] = lambda: graph
    return SimpleNamespace(client=TestClient(app), manager=manager, graph=graph, store=store)


def _capa(resp_o_id):
    """La capa del turno en la respuesta (artefactos del contrato), o una capa para map_context."""
    if not isinstance(resp_o_id, str):
        return next(a["layer"] for a in resp_o_id.json()["artifacts"] if a["kind"] == "layer")
    return _capa_de_mapa(resp_o_id)


def _capa_de_mapa(dataset_id: str) -> dict:
    return {"id": "layer-1", "name": "Lotes", "is_active": True, "dataset_id": dataset_id}


def _consulta(e, sesion: str, capas: list[dict]):
    e.manager.create_session(sesion)
    return e.client.post("/api/v1/query/", json={
        "query": "hazle un buffer de 100 m", "session_id": sesion,
        "map_context": {"layers": capas},
    })


def test_la_capa_por_id_se_lee_del_workspace_de_la_sesion(entorno):
    resp = _consulta(entorno, "sess-a", [_capa("ds_a")])
    assert resp.status_code == 200, resp.text
    kwargs = entorno.graph.process.call_args.kwargs
    assert kwargs["map_layers"] == {"layer-1": {"data": FC, "name": "Lotes"}}
    # la capa activa sigue siendo la autoritativa para operar (A4a)
    assert kwargs["external_geojson"] == FC
    assert ("sess-a", "ds_a") in entorno.store.pedidos


def test_el_id_de_otra_sesion_no_existe(entorno):
    resp = _consulta(entorno, "sess-b", [_capa("ds_a")])
    assert resp.status_code == 200, resp.text
    kwargs = entorno.graph.process.call_args.kwargs
    assert not kwargs.get("map_layers")
    assert kwargs.get("external_geojson") in (None, {})


def test_la_sesion_recuerda_la_capa_por_referencia(entorno):
    resp = _consulta(entorno, "sess-a", [])
    assert resp.status_code == 200, resp.text
    assert _capa(resp)["id"] == "ds_nuevo"
    estado = entorno.manager.get_session("sess-a").state
    assert estado.last_dataset_id == "ds_nuevo"
    assert estado.last_geojson is None  # la geometría no se copia a la sesión


def test_el_turno_siguiente_hereda_la_capa_desde_el_workspace(entorno):
    ctx = entorno.manager.create_session("sess-a")
    ctx.update_state(last_dataset_id="ds_a")
    entorno.client.post("/api/v1/query/", json={"query": "¿cuántos son?", "session_id": "sess-a"})
    assert entorno.graph.process.call_args.kwargs["previous_geojson"] == FC


def test_last_dataset_id_sobrevive_la_serializacion():
    ctx = ConversationContext(session_id="s")
    ctx.update_state(last_dataset_id="ds_9")
    assert ConversationContext.from_dict(ctx.to_dict()).state.last_dataset_id == "ds_9"


def test_una_capa_mas_grande_que_el_tope_no_se_hidrata_truncada(entorno):
    """Hidratar 10k de 1M daría un análisis sobre una parte sin decirlo."""
    resp = _consulta(entorno, "sess-a", [_capa("ds_grande")])
    assert resp.status_code == 200, resp.text
    assert not entorno.graph.process.call_args.kwargs.get("map_layers")
    assert ("sess-a", "ds_grande") not in entorno.store.pedidos


def test_un_reestilo_no_duplica_la_capa_en_el_workspace(entorno):
    """El turno devuelve la MISMA capa que llegó por id: se reutiliza su dataset."""
    resp = _consulta(entorno, "sess-a", [_capa("ds_a")])
    assert resp.status_code == 200, resp.text
    assert _capa(resp)["id"] == "ds_a"
    entorno.store.ingest_features.assert_not_called()


def test_una_capa_nueva_si_se_materializa(entorno):
    otra = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-75, 6]}, "properties": {}},
    ]}
    entorno.graph.process.return_value = {**entorno.graph.process.return_value, "geojson": otra}
    resp = _consulta(entorno, "sess-a", [_capa("ds_a")])
    assert _capa(resp)["id"] == "ds_nuevo"
    entorno.store.ingest_features.assert_awaited_once()


def test_un_externo_grande_queda_en_la_sesion_por_referencia(entorno, monkeypatch):
    from geo_copilot.api.routes import query as q

    monkeypatch.setattr(q.get_settings(), "max_geojson_features", 100, raising=False)
    grande = {"type": "FeatureCollection", "features": [FC["features"][0]] * 101}
    entorno.graph.process.return_value = {
        **entorno.graph.process.return_value,
        "geojson": grande, "external_geojson": grande, "external_source_name": "IGAC",
    }
    resp = _consulta(entorno, "sess-a", [])
    assert resp.status_code == 200, resp.text
    ctx = entorno.manager.get_session("sess-a")
    assert ctx.get_variable("external_geojson") is None
    assert ctx.state.last_geojson is None and ctx.state.last_dataset_id == "ds_nuevo"
    # y el turno siguiente no revienta la validación (S4) por la capa grande
    resp2 = entorno.client.post("/api/v1/query/", json={"query": "¿y ahora?", "session_id": "sess-a"})
    assert resp2.status_code == 200, resp2.text


def test_el_tope_de_fuentes_externas_sube_con_el_workspace():
    from geo_copilot.core.config import Settings

    # la capa completa (paginada) por defecto, chat, panel y BD con el mismo tope
    assert Settings.model_fields["max_external_features"].default == 200_000
    assert Settings.model_fields["sql_result_limit"].default == 200_000
