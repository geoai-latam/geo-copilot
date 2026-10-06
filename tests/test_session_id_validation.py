"""Regresión S2: validación uniforme de session_id en /session/*.

Antes solo ``get_history`` validaba el formato; el resto aceptaba ids
arbitrarios. (No implementa ownership real — el modelo es single-tenant;
ver nota en session.py.)
"""

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


_BAD_IDS = ["../etc/passwd", "a b", "drop;table", "x" * 101, "weird$id"]


@pytest.mark.parametrize("bad", _BAD_IDS)
def test_get_session_rejects_bad_id(client, bad):
    resp = client.get(f"/api/v1/session/{bad}")
    # FastAPI puede devolver 404 para algunos paths con '/', pero los
    # formatos inválidos sin '/' deben dar 400 de nuestra validación.
    if "/" not in bad:
        assert resp.status_code == 400


@pytest.mark.parametrize("bad", ["a b", "weird$id", "x" * 101])
def test_delete_session_rejects_bad_id(client, bad):
    resp = client.request("DELETE", f"/api/v1/session/{bad}")
    assert resp.status_code == 400


@pytest.mark.parametrize("bad", ["a b", "weird$id"])
def test_create_session_rejects_bad_custom_id(client, bad):
    resp = client.post("/api/v1/session/", json={"session_id": bad})
    assert resp.status_code == 400


def test_valid_id_still_works(client):
    resp = client.post("/api/v1/session/", json={"session_id": "ok-session_1"})
    assert resp.status_code == 201
    assert client.get("/api/v1/session/ok-session_1").status_code == 200
