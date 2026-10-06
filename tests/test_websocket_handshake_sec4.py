"""
Tests del handshake SEC-4 del WebSocket — añadidos 2026-05-25 (F2.3).

`test_api.py::TestWebSocket` solo conectaba en modo dev (sin api_key, sin
Origin). Estos tests verifican el contrato de seguridad del handshake:

1. Origin no permitido → close 4403 antes de aceptar.
2. Sin token (cuando api_key configurada) → close 4403.
3. Token incorrecto → close 4403.
4. Connection con Origin allowlisted Y token correcto → success.

Regresiones de SEC-4 son CRÍTICAS: dejarían el WebSocket abierto a
cualquier dominio cross-origin que conozca un session_id válido.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from geo_copilot.api.app import app
from geo_copilot.api.dependencies import get_app_state
from geo_copilot.core.config import get_settings


@pytest.fixture
def client():
    """TestClient con lifespan (inicializa app state).

    S0.3: el WebSocket ya no auto-crea sesiones, así que las que usan estos
    tests se crean antes, como hace el frontend con `POST /session/`.
    """
    with TestClient(app) as c:
        manager = get_app_state().conversation_manager
        for sid in ("test-good-origin", "test-no-origin", "test-good-token",
                    "test-dev-mode"):
            manager.get_or_create_session(sid)
        yield c


@pytest.fixture
def settings_with_api_key(monkeypatch):
    """Configurar `api_key` y `cors_origins` para tests de auth.

    Las variables `settings` están cacheadas via @lru_cache, así que
    sobreescribimos directamente los atributos del objeto.
    """
    from pydantic import SecretStr
    s = get_settings()
    monkeypatch.setattr(s, "api_key", SecretStr("test-key-12345"))
    monkeypatch.setattr(s, "cors_origins", ["http://localhost:3000"])
    monkeypatch.setattr(s, "cors_allow_all", False)
    return s


# =============================================================================
# Tests de Origin (sin api_key configurada)
# =============================================================================
def test_websocket_rejects_disallowed_origin(client, monkeypatch):
    """Con cors_origins restrictivo, un Origin no listado debe ser rechazado
    con close 4403 ANTES de aceptar la conexión."""
    s = get_settings()
    monkeypatch.setattr(s, "cors_origins", ["http://localhost:3000"])
    monkeypatch.setattr(s, "cors_allow_all", False)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/test-bad-origin",
            headers={"Origin": "https://evil.com"},
        ) as ws:
            ws.receive_json()  # no debería llegar acá

    assert exc_info.value.code == 4403, (
        f"WS debió cerrarse con 4403 (Forbidden), "
        f"se cerró con {exc_info.value.code}"
    )


def test_websocket_accepts_allowed_origin(client, monkeypatch):
    """Con Origin en cors_origins, sin api_key → conexión OK."""
    s = get_settings()
    monkeypatch.setattr(s, "cors_origins", ["http://localhost:3000"])
    monkeypatch.setattr(s, "cors_allow_all", False)
    monkeypatch.setattr(s, "api_key", None)  # sin api_key → no se exige token

    with client.websocket_connect(
        "/ws/test-good-origin",
        headers={"Origin": "http://localhost:3000"},
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status"
        assert msg["data"]["status"] == "connected"


def test_websocket_no_origin_header_in_cors_allow_all_mode(client, monkeypatch):
    """Sin header Origin (cliente no-browser, ej. python-websockets),
    se acepta solo si cors_allow_all está en True."""
    s = get_settings()
    monkeypatch.setattr(s, "cors_allow_all", True)
    monkeypatch.setattr(s, "api_key", None)

    # No mandamos Origin header.
    with client.websocket_connect("/ws/test-no-origin") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status"


# =============================================================================
# Tests de token (api_key configurada)
# =============================================================================
def test_websocket_rejects_missing_token_when_api_key_set(
    client, settings_with_api_key,
):
    """Con api_key configurada, conectar sin `?token=` → 4403."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/test-no-token",
            headers={"Origin": "http://localhost:3000"},
        ) as ws:
            ws.receive_json()

    assert exc_info.value.code == 4403


def test_websocket_rejects_wrong_token(client, settings_with_api_key):
    """Token incorrecto → 4403."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/test-wrong-token?token=NOT-THE-RIGHT-KEY",
            headers={"Origin": "http://localhost:3000"},
        ) as ws:
            ws.receive_json()

    assert exc_info.value.code == 4403


def test_websocket_accepts_correct_token(client, settings_with_api_key):
    """Token correcto + Origin OK → conexión exitosa."""
    with client.websocket_connect(
        "/ws/test-good-token?token=test-key-12345",
        headers={"Origin": "http://localhost:3000"},
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status"
        assert msg["data"]["status"] == "connected"


def test_websocket_token_is_constant_time_compared(client, settings_with_api_key):
    """Regresión: el match de token DEBE ser timing-safe (usar
    `hmac.compare_digest`). Si alguien usa `==` plain string, vulnerable
    a timing attacks.

    Este test no mide timing directamente (es difícil en CI), pero verifica
    que tokens "cercanos" al correcto no pasan (mismo prefix, diferente sufijo).
    """
    # Token con primer char correcto pero resto diferente.
    bad_token = "test-key-67890"  # mismo length que "test-key-12345"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/ws/test-timing?token={bad_token}",
            headers={"Origin": "http://localhost:3000"},
        ) as ws:
            ws.receive_json()

    assert exc_info.value.code == 4403


# =============================================================================
# Edge cases
# =============================================================================
def test_websocket_origin_check_skipped_in_cors_allow_all(client, monkeypatch):
    """Si `cors_allow_all=True`, cualquier Origin debe pasar (modo dev)."""
    s = get_settings()
    monkeypatch.setattr(s, "cors_allow_all", True)
    monkeypatch.setattr(s, "api_key", None)

    # Origin "evil.com" debería pasar en modo dev.
    with client.websocket_connect(
        "/ws/test-dev-mode",
        headers={"Origin": "https://anywhere.com"},
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status"


def test_websocket_empty_api_key_is_fail_closed(client, monkeypatch):
    """S1: SecretStr("") está MAL configurada → fail-closed, no dev-mode.

    Antes una api_key vacía saltaba la auth en silencio (vulnerabilidad).
    Ahora se rechaza la conexión (close 4403): si alguien configura una
    key vacía, el sistema deniega en vez de quedar abierto.
    """
    from pydantic import SecretStr
    s = get_settings()
    monkeypatch.setattr(s, "api_key", SecretStr(""))
    monkeypatch.setattr(s, "cors_allow_all", True)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/test-empty-key") as ws:
            ws.receive_json()

    assert exc_info.value.code == 4403
