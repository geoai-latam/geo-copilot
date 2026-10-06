"""Regresión S3: SSRF en POST /discovery/load.

``item.service_url`` lo controla el cliente y el backend lo fetchea
server-side. Sin validación, un atacante puede apuntar a servicios
internos / el endpoint de metadata del cloud (169.254.169.254). El
guard bloquea esquemas no-http(s), puertos no estándar e IPs internas.
"""

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app


def _item(service_url: str) -> dict:
    return {
        "id": "x",
        "source": "hub",
        "org": "o",
        "title": "Malicioso",
        "description": "d",
        "service_type": "FeatureServer",
        "service_url": service_url,
    }


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8000/admin",               # localhost
        "http://localhost/internal",                 # localhost por nombre
        "http://10.0.0.5/service/FeatureServer/0",   # red privada
        "file:///etc/passwd",                        # esquema no http
    ],
)
def test_load_rejects_internal_and_bad_urls(client, url):
    resp = client.post("/api/v1/discovery/load", json={"item": _item(url)})
    assert resp.status_code == 400
    assert "no permitida" in resp.json()["detail"].lower()
