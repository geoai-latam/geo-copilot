"""
Tests para la API REST de GEO_COPILOT.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app
from geo_copilot.api.dependencies import get_app_state, reset_app_state
from geo_copilot.api.models import (
    ApprovalAction,
    ApprovalRequest,
    QueryRequest,
    QueryStatus,
)

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def reset_state():
    """Reset app state before each test."""
    reset_app_state()
    yield
    reset_app_state()


@pytest.fixture
def app():
    """Create test application."""
    return create_app()


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_orchestrator():
    """Create mock orchestrator."""
    mock = AsyncMock()
    mock.process_query = AsyncMock(return_value={
        "status": "completed",
        "intent": "search_data",
        "confidence": 0.85,
        "message": "Encontré 5 conjuntos de datos.",
        "requires_approval": False,
        "results": {"data": []},
    })
    mock.get_available_workflows = MagicMock(return_value=[
        {"name": "Test Workflow", "description": "Test"}
    ])
    mock.get_supported_intents = MagicMock(return_value=[
        {"intent": "search_data", "description": "Search data"}
    ])
    return mock


# =============================================================================
# Health Check Tests
# =============================================================================

class TestHealthCheck:
    """Tests for health check endpoint."""

    def test_health_check(self, client):
        """Health check reports component state correctly.

        Fase 3 / API-4: the endpoint now returns 200 only when both the
        database and the LLM client are available; 503 otherwise. In the
        test environment neither is configured, so we expect 503 and a
        ``degraded`` status. The body shape is the same in both cases.
        """
        response = client.get("/health")
        # Accept either status; we care about the body shape and the
        # contract that 503 ↔ degraded, 200 ↔ healthy.
        assert response.status_code in (200, 503)
        data = response.json()
        assert "version" in data
        assert "components" in data
        if response.status_code == 200:
            assert data["status"] == "healthy"
        else:
            assert data["status"] == "degraded"
            # The two components we mark as critical:
            assert data["components"].get("database") == "unavailable"

    def test_root_endpoint(self, client):
        """Test root endpoint returns API info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "GEO_COPILOT API"
        assert "version" in data


# =============================================================================
# Session Management Tests
# =============================================================================

class TestSessionEndpoints:
    """Tests for session management endpoints."""

    def test_create_session(self, client):
        """Test creating a new session."""
        response = client.post("/api/v1/session/")
        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert "created_at" in data
        assert data["message_count"] == 0

    def test_create_session_with_custom_id(self, client):
        """Test creating session with custom ID."""
        response = client.post(
            "/api/v1/session/",
            json={"session_id": "custom-session-123"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] == "custom-session-123"

    def test_create_session_with_preferences(self, client):
        """Test creating session with preferences."""
        response = client.post(
            "/api/v1/session/",
            json={
                "preferences": {
                    "language": "en",
                    "max_results": 500
                }
            }
        )
        assert response.status_code == 201
        data = response.json()
        assert data["preferences"]["language"] == "en"
        assert data["preferences"]["max_results"] == 500

    def test_get_session(self, client):
        """Test getting session details."""
        # Create session first
        create_response = client.post("/api/v1/session/")
        session_id = create_response.json()["session_id"]

        # Get session
        response = client.get(f"/api/v1/session/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id

    def test_get_nonexistent_session(self, client):
        """Test getting nonexistent session returns 404."""
        response = client.get("/api/v1/session/nonexistent-id")
        assert response.status_code == 404

    def test_list_sessions(self, client):
        """Test listing all sessions."""
        # Create some sessions
        client.post("/api/v1/session/")
        client.post("/api/v1/session/")

        response = client.get("/api/v1/session/")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert data["total"] >= 2

    def test_update_preferences(self, client):
        """Test updating session preferences."""
        # Create session
        create_response = client.post("/api/v1/session/")
        session_id = create_response.json()["session_id"]

        # Update preferences
        response = client.patch(
            f"/api/v1/session/{session_id}/preferences",
            json={"language": "en", "max_results": 200}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["preferences"]["language"] == "en"
        assert data["preferences"]["max_results"] == 200

    def test_delete_session(self, client):
        """Test deleting a session."""
        # Create session
        create_response = client.post("/api/v1/session/")
        session_id = create_response.json()["session_id"]

        # Delete session
        response = client.delete(f"/api/v1/session/{session_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = client.get(f"/api/v1/session/{session_id}")
        assert get_response.status_code == 404

    def test_reset_session(self, client):
        """Test resetting a session."""
        # Create session
        create_response = client.post("/api/v1/session/")
        session_id = create_response.json()["session_id"]

        # Reset session
        response = client.post(f"/api/v1/session/{session_id}/reset")
        assert response.status_code == 200
        data = response.json()
        assert data["message_count"] == 0

    def test_get_history_empty(self, client):
        """Test getting empty history."""
        # Create session
        create_response = client.post("/api/v1/session/")
        session_id = create_response.json()["session_id"]

        # Get history
        response = client.get(f"/api/v1/session/{session_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data == []


# =============================================================================
# Query Endpoint Tests
# =============================================================================

class TestQueryEndpoints:
    """Tests for query processing endpoints."""

    # NOTE: los siguientes tests de POST /query/ dependen del LLM real
    # (azure/openai/anthropic) porque el TestClient instancia el lifespan
    # completo de FastAPI. Como la respuesta del LLM es no-determinística
    # (puede clasificar "Lista las entidades" como query_data en vez de
    # direct_response, p.ej.), el test cuelga 120s hasta TimeoutError. Los
    # marcamos integration y los activamos solo en runs con ``-m
    # integration``. Para volverlos unit habría que mockear el LLM al
    # nivel del app state (no trivial con TestClient).

    @pytest.mark.integration
    def test_process_query_basic(self, client):
        """Test processing a basic query (integration — usa LLM real)."""
        response = client.post(
            "/api/v1/query/",
            json={"query": "¿Qué datos hay disponibles?"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "query_id" in data
        assert "session_id" in data
        assert "status" in data

    @pytest.mark.integration
    def test_process_query_with_session(self, client):
        """Test processing query with existing session (integration — usa LLM real)."""
        # Create session
        create_response = client.post("/api/v1/session/")
        session_id = create_response.json()["session_id"]

        # Process query
        response = client.post(
            "/api/v1/query/",
            json={
                "query": "Lista las entidades disponibles",
                "session_id": session_id
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id

    @pytest.mark.integration
    def test_process_query_with_parameters(self, client):
        """Test processing query con parámetros (integration).

        Esta query ("Busca datos de escuelas") es interpretada por el Router
        como ``intent=search_external`` y dispara una llamada REAL al
        ArcGIS Hub. Sin mock del LLM + connector el test cuelga hasta el
        timeout duro (``settings.total_execution_timeout`` = 120s) y
        devuelve 504. Lo marcamos integration para que el CI rápido lo
        salte y permanezca ejecutable en runs full con ``--run-integration``.
        """
        response = client.post(
            "/api/v1/query/",
            json={
                "query": "Busca datos de escuelas",
                "parameters": {"max_results": 50}
            }
        )
        assert response.status_code == 200

    def test_process_empty_query(self, client):
        """Test processing empty query returns error."""
        response = client.post(
            "/api/v1/query/",
            json={"query": ""}
        )
        assert response.status_code == 422  # Validation error

    def test_cancel_query_returns_501_until_implemented(self, client):
        """Cancel endpoint devuelve 501 hasta que se implemente.

        Antes devolvía 200 + ``{cancelled: true}`` aunque NO cancelaba —
        un cliente no podía distinguir éxito real de placebo. Cambiado a
        501 (Not Implemented) en 2026-05-30. Cuando se implemente la
        cancelación real, este test debe actualizarse para verificar el
        comportamiento end-to-end.
        """
        response = client.post("/api/v1/query/test-query-id/cancel")
        assert response.status_code == 501
        assert "no implementada" in response.json()["detail"].lower()


# =============================================================================
# Approval Endpoint Tests
# =============================================================================

class TestApprovalEndpoints:
    """Tests for HITL approval endpoints."""

    def test_list_pending_approvals_empty(self, client):
        """Listing pending approvals filtered by session_id returns an empty list.

        Fase 1 / SEC-3: ``/approval/pending`` now requires the caller to
        declare which session they own. Without that filter, anyone could
        enumerate every pending approval_id in the system.
        """
        response = client.get("/api/v1/approval/pending?session_id=test-session")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_nonexistent_approval(self, client):
        """Test getting nonexistent approval returns 404 (con session_id, ahora requerido)."""
        response = client.get("/api/v1/approval/nonexistent-id?session_id=sess-x")
        assert response.status_code == 404

    def test_submit_approval_nonexistent(self, client):
        """Submitting an approval for an unknown ID returns 404.

        Fase 1 / SEC-3: ``ApprovalRequest`` now requires ``session_id``.
        """
        response = client.post(
            "/api/v1/approval/nonexistent-id",
            json={"action": "approve", "session_id": "test-session"},
        )
        assert response.status_code == 404

    def test_approval_history_empty(self, client):
        """Test getting approval history for session."""
        response = client.get("/api/v1/approval/history/test-session")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


# =============================================================================
# Metadata Endpoint Tests
# =============================================================================

class TestMetadataEndpoints:
    """Tests for metadata endpoints."""

    def test_list_entities(self, client):
        """Test listing entities."""
        response = client.get("/api/v1/metadata/entities")
        assert response.status_code == 200
        data = response.json()
        assert "entities" in data
        assert "total" in data

    # NOTE: ``/metadata/workflows`` and ``/metadata/intents`` were removed
    # when the metadata route was refactored to expose only entities,
    # categories and search. The matching tests were deleted as part of the
    # Fase 0 desbloqueo cleanup.

    def test_list_categories(self, client):
        """Test listing categories."""
        response = client.get("/api/v1/metadata/categories")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_search_entities_empty(self, client):
        """Test searching entities with no results."""
        response = client.get("/api/v1/metadata/search?q=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_entity_not_found(self, client):
        """Requesting a non-existent entity returns 404 (proper REST semantics)."""
        response = client.get("/api/v1/metadata/entities/nonexistent")
        assert response.status_code == 404
        data = response.json()
        # FastAPI's HTTPException puts the message under "detail"
        assert "detail" in data
        assert "nonexistent" in data["detail"]


# =============================================================================
# API Models Tests
# =============================================================================

class TestAPIModels:
    """Tests for API Pydantic models."""

    def test_query_request_validation(self):
        """Test QueryRequest validation."""
        request = QueryRequest(query="Test query")
        assert request.query == "Test query"
        assert request.session_id is None
        assert request.parameters == {}

    def test_query_request_with_session(self):
        """Test QueryRequest with session ID."""
        request = QueryRequest(
            query="Test query",
            session_id="abc123",
            parameters={"key": "value"}
        )
        assert request.session_id == "abc123"
        assert request.parameters["key"] == "value"

    def test_approval_request_approve(self):
        """ApprovalRequest construye correctamente la acción ``approve``."""
        request = ApprovalRequest(action=ApprovalAction.APPROVE, session_id="s1")
        assert request.action == ApprovalAction.APPROVE
        assert request.session_id == "s1"

    def test_approval_request_reject(self):
        """ApprovalRequest construye correctamente la acción ``reject``."""
        request = ApprovalRequest(
            action=ApprovalAction.REJECT,
            session_id="s1",
            reason="Contains dangerous operations",
        )
        assert request.action == ApprovalAction.REJECT
        assert request.reason == "Contains dangerous operations"

    def test_approval_request_modify(self):
        """ApprovalRequest construye correctamente la acción ``modify``."""
        request = ApprovalRequest(
            action=ApprovalAction.MODIFY,
            session_id="s1",
            modified_content="SELECT * FROM table LIMIT 100",
        )
        assert request.action == ApprovalAction.MODIFY
        assert "LIMIT 100" in request.modified_content


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.integration
class TestIntegration:
    """Integration tests for full workflows.

    Marcados @integration: ejercitan el orquestador REAL (router→LLM→HITL) vía
    el TestClient; sin un backend/LLM controlado y sin resolver el HITL
    bloqueante, ``test_multiple_queries_same_session`` se colgaba en el run de
    unidad. Se deseleccionan por defecto (``-m "not integration"``) y corren a
    demanda con el stack levantado.
    """

    def test_full_session_workflow(self, client):
        """Test complete session workflow."""
        # 1. Create session
        session_response = client.post(
            "/api/v1/session/",
            json={"preferences": {"language": "es"}}
        )
        assert session_response.status_code == 201
        session_id = session_response.json()["session_id"]

        # 2. Process query
        query_response = client.post(
            "/api/v1/query/",
            json={
                "query": "¿Qué datos hay disponibles?",
                "session_id": session_id
            }
        )
        assert query_response.status_code == 200

        # 3. Get session history
        history_response = client.get(f"/api/v1/session/{session_id}/history")
        assert history_response.status_code == 200

        # 4. Update preferences
        prefs_response = client.patch(
            f"/api/v1/session/{session_id}/preferences",
            json={"max_results": 500}
        )
        assert prefs_response.status_code == 200

        # 5. Verify session state
        session_get = client.get(f"/api/v1/session/{session_id}")
        assert session_get.status_code == 200
        assert session_get.json()["preferences"]["max_results"] == 500

        # 6. Delete session
        delete_response = client.delete(f"/api/v1/session/{session_id}")
        assert delete_response.status_code == 204

    def test_multiple_queries_same_session(self, client):
        """Test multiple queries in same session maintain context."""
        # Create session
        session_response = client.post("/api/v1/session/")
        session_id = session_response.json()["session_id"]

        # First query
        client.post(
            "/api/v1/query/",
            json={"query": "Busca escuelas", "session_id": session_id}
        )

        # Second query
        client.post(
            "/api/v1/query/",
            json={"query": "Ahora muéstralas en un mapa", "session_id": session_id}
        )

        # Check history
        history = client.get(f"/api/v1/session/{session_id}/history")
        assert history.status_code == 200
        # History should have messages from both queries
        messages = history.json()
        assert len(messages) >= 2


# =============================================================================
# WebSocket Tests (basic)
# =============================================================================

def _ws(client, session_id: str) -> str:
    """Crear la sesión por REST (como el frontend) y devolver la ruta del socket.

    S0.3: el WebSocket ya no auto-crea sesiones; conectar a una inexistente
    cierra con 4404.
    """
    resp = client.post("/api/v1/session/", json={"session_id": session_id})
    assert resp.status_code == 201, resp.text
    return f"/ws/{session_id}"


class TestWebSocket:
    """Basic WebSocket tests."""

    def test_websocket_connect(self, client):
        """Test WebSocket connection."""
        with client.websocket_connect(_ws(client, "test-session")) as websocket:
            # Should receive connection confirmation
            data = websocket.receive_json()
            assert data["type"] == "status"
            assert data["data"]["status"] == "connected"

    def test_websocket_ping_pong(self, client):
        """Test WebSocket ping/pong."""
        with client.websocket_connect(_ws(client, "test-session")) as websocket:
            # Receive connection message
            websocket.receive_json()

            # Send ping
            websocket.send_json({"type": "ping", "data": {}})

            # Receive pong
            data = websocket.receive_json()
            assert data["type"] == "pong"

    def test_websocket_invalid_json(self, client):
        """Test WebSocket handles invalid JSON."""
        with client.websocket_connect(_ws(client, "test-session")) as websocket:
            # Receive connection message
            websocket.receive_json()

            # Send invalid JSON
            websocket.send_text("not valid json")

            # Should receive error
            data = websocket.receive_json()
            assert data["type"] == "error"

    def test_websocket_unknown_message_type(self, client):
        """Test WebSocket handles unknown message types."""
        with client.websocket_connect(_ws(client, "test-session")) as websocket:
            # Receive connection message
            websocket.receive_json()

            # Send unknown type
            websocket.send_json({"type": "unknown_type", "data": {}})

            # Should receive error
            data = websocket.receive_json()
            assert data["type"] == "error"

    # =========================================================================
    # Tests de secuencia E2E (auditoría 2026-05-24)
    # Antes solo se verificaba 1 mensaje aislado por test. Estos tests
    # ejercitan secuencias reales que el frontend recibe en producción.
    # =========================================================================

    def test_websocket_connection_message_has_expected_shape(self, client):
        """El mensaje inicial de conexión debe tener `type`, `data.status`
        y `data.session_id`. El frontend lee esos campos exactos para
        marcar la sesión como conectada en `sessionStore`."""
        with client.websocket_connect(_ws(client, "test-shape-session")) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "status"
            assert isinstance(msg.get("data"), dict)
            assert msg["data"].get("status") == "connected"
            # session_id debe estar para que el cliente confirme binding.
            assert msg["data"].get("session_id"), (
                "frontend espera session_id en data para confirmar conexión"
            )

    def test_websocket_ping_pong_preserves_payload_shape(self, client):
        """El pong DEBE tener shape `{type: 'pong', data: ...}`. Si el
        backend devuelve solo "pong" como string, el frontend rompe al
        parsear."""
        with client.websocket_connect(_ws(client, "test-pong-session")) as ws:
            ws.receive_json()  # mensaje de conexión
            ws.send_json({"type": "ping", "data": {}})
            pong = ws.receive_json()
            assert pong["type"] == "pong"
            assert "data" in pong, "pong debe tener campo data (puede ser {})"

    def test_websocket_multiple_pings_get_pongs_in_order(self, client):
        """5 pings consecutivos → 5 pongs en orden. Verifica que el manejo
        de mensajes NO mezcla respuestas entre clientes."""
        with client.websocket_connect(_ws(client, "multi-ping-session")) as ws:
            ws.receive_json()  # conexión

            for i in range(5):
                ws.send_json({"type": "ping", "data": {"seq": i}})
                response = ws.receive_json()
                assert response["type"] == "pong", (
                    f"Mensaje #{i} debió ser pong, fue {response['type']}"
                )

    def test_websocket_two_clients_isolated(self, client):
        """Dos sesiones simultáneas no se mezclan: cada una recibe solo
        sus propios pongs.

        Antes (auditoría): NO había ningún test multi-cliente. Bugs como
        "el broadcast llega a la sesión equivocada" pasarían silenciosos.
        """
        with client.websocket_connect(_ws(client, "session-A")) as ws_a:
            ws_a.receive_json()  # connect
            with client.websocket_connect(_ws(client, "session-B")) as ws_b:
                ws_b.receive_json()  # connect

                # A pingea, A recibe pong.
                ws_a.send_json({"type": "ping", "data": {"from": "A"}})
                pong_a = ws_a.receive_json()
                assert pong_a["type"] == "pong"

                # B pingea, B recibe pong.
                ws_b.send_json({"type": "ping", "data": {"from": "B"}})
                pong_b = ws_b.receive_json()
                assert pong_b["type"] == "pong"


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Tests for error handling."""

    def test_invalid_json_body(self, client):
        """Test handling of invalid JSON in request body."""
        response = client.post(
            "/api/v1/query/",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422

    def test_missing_required_field(self, client):
        """Test handling of missing required fields."""
        response = client.post(
            "/api/v1/query/",
            json={}  # Missing 'query' field
        )
        assert response.status_code == 422

    def test_invalid_endpoint(self, client):
        """Test 404 for invalid endpoints."""
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404


class TestWebSocketConsultaNoBloquea:
    """Una consulta por el WS no bloquea el socket: su aprobación (o «Detener») llega mientras corre.

    El bucle esperaba la consulta antes de volver a leer: la aprobación que el cliente mandaba por el
    MISMO socket nunca se leía y el turno quedaba colgado hasta el plazo (E2E por WS, auditoría F4).
    """

    def test_la_aprobacion_llega_mientras_la_consulta_corre(self, client, monkeypatch):
        import asyncio
        import time

        from geo_copilot.api import websocket as ws

        vista: list[str] = []

        async def consulta_que_espera_su_aprobacion(session_id, data, app_state):
            limite = time.monotonic() + 3
            while not vista and time.monotonic() < limite:
                await asyncio.sleep(0.02)
            await ws._enviar(session_id, ws.WSMessageType.RESULT, {"vio_aprobacion": bool(vista)})

        async def aprobacion(session_id, data, app_state):
            vista.append(data.get("approval_id"))

        monkeypatch.setattr(ws, "handle_query", consulta_que_espera_su_aprobacion)
        monkeypatch.setattr(ws, "handle_approval", aprobacion)
        with client.websocket_connect(_ws(client, "ws-no-bloquea")) as socket:
            socket.receive_json()  # connected
            socket.send_json({"type": "query", "data": {"query": "¿cuántos lotes?"}})
            socket.send_json({"type": "approval", "data": {"approval_id": "a1", "action": "approve"}})
            resultado = socket.receive_json()
        assert resultado["type"] == "result"
        assert resultado["data"] == {"vio_aprobacion": True}
        assert vista == ["a1"]
