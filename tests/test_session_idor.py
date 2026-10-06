"""R0.11 (auditoría 2026-07-26, AUD-20/AUD-23) — IDOR de sesión.

`GET /session/` devolvía TODAS las sesiones vivas con su `session_id`, y ese id
era el único discriminador de propiedad del HITL (approval.py y el handshake
del WebSocket). Enumerarlo bastaba para abrir el WebSocket de otro usuario,
leer su SQL en claro y responder a sus aprobaciones. Y `POST /session/` con un
id existente lo DESTRUÍA (usaba create_session, no get_or_create).

Verificado contra el stack en vivo antes del fix: el listado devolvía los dos
ids creados, y el POST con id ajeno respondía 201.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


class TestNoSePuedenEnumerarSesiones:
    def test_el_listado_no_expone_ningun_session_id(self, client):
        a = client.post("/api/v1/session/").json()["session_id"]
        b = client.post("/api/v1/session/").json()["session_id"]

        cuerpo = client.get("/api/v1/session/")
        assert cuerpo.status_code == 200
        crudo = cuerpo.text
        assert a not in crudo, "el id de una sesión no puede aparecer en el listado"
        assert b not in crudo
        assert cuerpo.json()["sessions"] == []

    def test_el_recuento_sigue_disponible(self, client):
        antes = client.get("/api/v1/session/").json()["total"]
        client.post("/api/v1/session/")
        assert client.get("/api/v1/session/").json()["total"] == antes + 1


class TestNoSePuedeSecuestrarUnaSesion:
    def test_crear_con_un_id_existente_da_409(self, client):
        ajeno = client.post("/api/v1/session/").json()["session_id"]

        r = client.post("/api/v1/session/", json={"session_id": ajeno})
        assert r.status_code == 409, "antes devolvía 201 y vaciaba la conversación"

    def test_la_conversacion_de_la_victima_sobrevive_al_intento(self, client):
        ajeno = client.post("/api/v1/session/").json()["session_id"]
        client.post("/api/v1/session/", json={"session_id": ajeno})

        r = client.get(f"/api/v1/session/{ajeno}")
        assert r.status_code == 200
        assert r.json()["session_id"] == ajeno

    def test_crear_una_sesion_nueva_sigue_funcionando(self, client):
        r = client.post("/api/v1/session/")
        assert r.status_code == 201
        assert r.json()["session_id"]
