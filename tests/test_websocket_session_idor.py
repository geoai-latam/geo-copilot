"""S0.3 (#14): IDOR residual del WebSocket.

Antes, cualquier ``session_id`` del path se auto-creaba y ``connect()`` sustituía
en silencio la conexión viva de esa sesión: con conocer el id se le robaba a
otro cliente el canal de progreso y de aprobaciones HITL. Ahora el socket se
rechaza ANTES de aceptarlo si el id es inválido, la sesión no existe o ya tiene
una conexión viva.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from geo_copilot.api.app import app
from geo_copilot.api.websocket import (
    WS_CLOSE_INVALID_SESSION,
    WS_CLOSE_SESSION_IN_USE,
    WS_CLOSE_UNKNOWN_SESSION,
)
from geo_copilot.core.config import get_settings


@pytest.fixture
def client(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "api_key", None)
    monkeypatch.setattr(s, "cors_allow_all", True)
    with TestClient(app) as c:
        yield c


def _crear(client, session_id: str) -> None:
    resp = client.post("/api/v1/session/", json={"session_id": session_id})
    assert resp.status_code == 201, resp.text


def _codigo_de_cierre(client, path: str) -> int:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(path) as ws:
            ws.receive_json()
    return exc_info.value.code


def test_sesion_inexistente_se_rechaza_y_no_se_crea(client):
    from geo_copilot.api.dependencies import get_app_state

    assert _codigo_de_cierre(client, "/ws/sesion-inventada") == WS_CLOSE_UNKNOWN_SESSION
    # El rechazo no deja la sesión creada como efecto secundario.
    assert not get_app_state().conversation_manager.session_exists("sesion-inventada")


def test_session_id_con_formato_invalido_se_rechaza(client):
    assert _codigo_de_cierre(client, "/ws/" + "a" * 101) == WS_CLOSE_INVALID_SESSION
    assert _codigo_de_cierre(client, "/ws/id$raro") == WS_CLOSE_INVALID_SESSION


def test_no_se_roba_una_conexion_viva(client):
    _crear(client, "sesion-de-alice")
    with client.websocket_connect("/ws/sesion-de-alice") as alice:
        assert alice.receive_json()["data"]["status"] == "connected"

        # Un segundo cliente que conoce el id NO sustituye a Alice.
        assert _codigo_de_cierre(client, "/ws/sesion-de-alice") == WS_CLOSE_SESSION_IN_USE

        # Y Alice sigue recibiendo lo suyo.
        alice.send_json({"type": "ping", "data": {}})
        assert alice.receive_json()["type"] == "pong"


def test_reconectar_tras_cerrar_funciona(client):
    """La reconexión legítima del frontend (tras un corte) sigue funcionando."""
    _crear(client, "sesion-que-reconecta")
    with client.websocket_connect("/ws/sesion-que-reconecta") as ws:
        ws.receive_json()
    with client.websocket_connect("/ws/sesion-que-reconecta") as ws:
        assert ws.receive_json()["data"]["status"] == "connected"
