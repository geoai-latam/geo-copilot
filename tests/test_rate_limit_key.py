"""Regresión S7: clave de rate-limit por X-Forwarded-For tras proxy.

``get_remote_address`` (la IP del socket) hace que tras un proxy todos
los clientes compartan bucket o el límite se evada. ``client_ip_key``
usa X-Forwarded-For cuando ``trust_proxy_headers`` está activo, y lo
ignora (anti-spoofing) cuando no.

F7 (auditoría): de X-Forwarded-For solo vale la ÚLTIMA entrada, la que escribe
nuestro proxy (nginx la fija a ``$remote_addr``). Las anteriores las manda el
cliente y no son de fiar.
"""

from types import SimpleNamespace

from geo_copilot.api.limiter import client_ip_key
from geo_copilot.core.config import get_settings


def _request(xff=None, client_host="10.1.2.3"):
    headers = {}
    if xff is not None:
        headers["X-Forwarded-For"] = xff
    # slowapi.get_remote_address lee request.client.host
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=client_host),
    )


def test_uses_forwarded_for_when_trusted(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "trust_proxy_headers", True)

    req = _request(xff="203.0.113.9, 70.41.3.18", client_host="10.0.0.1")
    assert client_ip_key(req) == "70.41.3.18"


def test_una_primera_entrada_falsificada_no_cambia_la_clave(monkeypatch):
    """Un cliente que inventa una IP distinta en cada petición no estrena bucket: el proxy anexa
    (o sobrescribe con) la suya y esa es la que cuenta."""
    s = get_settings()
    monkeypatch.setattr(s, "trust_proxy_headers", True)

    claves = {client_ip_key(_request(xff=f"198.51.100.{i}, 70.41.3.18", client_host="10.0.0.1"))
              for i in range(5)}
    assert claves == {"70.41.3.18"}


def test_ignores_forwarded_for_when_not_trusted(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "trust_proxy_headers", False)

    req = _request(xff="203.0.113.9", client_host="10.0.0.1")
    # No se confía en el header → cae a la IP del socket.
    assert client_ip_key(req) == "10.0.0.1"


def test_falls_back_when_no_header(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "trust_proxy_headers", True)

    req = _request(xff=None, client_host="10.0.0.7")
    assert client_ip_key(req) == "10.0.0.7"
