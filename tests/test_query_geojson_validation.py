"""Regresión S4: validación del GeoJSON externo en POST /query.

Antes la única validación era código muerto (``hasattr(query_request,
'external_geojson')`` sobre un campo inexistente de ``QueryRequest``).
El GeoJSON externo real entra por variables de sesión y NO se validaba,
saltando la protección DoS de ``max_geojson_features``.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api import dependencies as deps
from geo_copilot.api.app import create_app
from geo_copilot.orchestrator.conversation import ConversationManager


@pytest.fixture
def app_with_session():
    app = create_app()
    manager = ConversationManager()

    def _override_manager():
        return manager

    def _override_graph():
        # No-None para superar el check de "LLM configurado"; el grafo NO
        # debe llegar a ejecutarse si la validación rechaza antes.
        return MagicMock()

    app.dependency_overrides[deps.get_conversation_manager] = _override_manager
    app.dependency_overrides[deps.get_agent_graph] = _override_graph
    return app, manager


def test_invalid_external_geojson_rejected_with_400(app_with_session):
    app, manager = app_with_session
    ctx = manager.create_session("sess-bad-geo")
    # features no es una lista → GeoJSON inválido.
    ctx.set_variable("external_geojson", {"type": "FeatureCollection", "features": "nope"})

    client = TestClient(app)
    resp = client.post(
        "/api/v1/query/",
        json={"query": "haz un buffer", "session_id": "sess-bad-geo"},
    )

    assert resp.status_code == 400
    assert "GeoJSON" in resp.json()["detail"]


def test_oversized_external_geojson_rejected(app_with_session, monkeypatch):
    from geo_copilot.api.routes import query as query_route

    # Bajar el tope para no construir 10k features en el test.
    monkeypatch.setattr(
        query_route.get_settings(), "max_geojson_features", 3, raising=False
    )

    app, manager = app_with_session
    ctx = manager.create_session("sess-big-geo")
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}
        for _ in range(5)
    ]
    ctx.set_variable(
        "external_geojson", {"type": "FeatureCollection", "features": features}
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/query/",
        json={"query": "haz un buffer", "session_id": "sess-big-geo"},
    )

    assert resp.status_code == 400
    assert "features" in resp.json()["detail"].lower()
