"""S3.5 — proxy genérico de teselas MCP: solo prefijos declarados, credencial inyectada."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app
from geo_copilot.api.routes import proxy
from geo_copilot.platform.mcp import hub as hub_mod
from geo_copilot.platform.mcp.config import ServerConfig

PNG = b"\x89PNG\r\n\x1a\nxxxx"


@pytest.fixture
def entorno(monkeypatch):
    monkeypatch.setenv("IMG_KEY_TEST", "secreto-del-servidor")
    cfg = ServerConfig.model_validate({
        "id": "imagery", "url": "http://imagery-mcp:9100/mcp", "conformance": "G2",
        "auth": {"type": "bearer", "secret_ref": "env:IMG_KEY_TEST"},
        "tools": {"allow": ["imagery_*"]}, "tiles": {"prefixes": ["/tiles/", "/tiles-rgb/"]},
    })
    monkeypatch.setattr(hub_mod, "_hub", SimpleNamespace(conexiones={"imagery": SimpleNamespace(cfg=cfg)}))
    pedidas: list[httpx.Request] = []

    def upstream(req: httpx.Request) -> httpx.Response:
        pedidas.append(req)
        if "falla" in req.url.path:
            return httpx.Response(500)
        if "json" in req.url.path:
            return httpx.Response(200, json={"no": "soy tesela"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    monkeypatch.setattr(proxy, "_cliente_mcp_proxy",
                        lambda cfg=None: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    return SimpleNamespace(http=TestClient(create_app()), pedidas=pedidas)


def test_reemite_con_la_credencial_del_servidor(entorno):
    r = entorno.http.get("/api/v1/proxy/mcp/imagery/tiles/S2A_X/12/1200/1990.png?rescale=0.1,0.8")
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    req = entorno.pedidas[0]
    assert str(req.url) == "http://imagery-mcp:9100/tiles/S2A_X/12/1200/1990.png?rescale=0.1,0.8"
    assert req.headers["authorization"] == "Bearer secreto-del-servidor"
    assert "secreto" not in r.text  # la credencial no vuelve al navegador


@pytest.mark.parametrize("ruta", [
    "/api/v1/proxy/mcp/imagery/mcp",                         # el endpoint MCP no es una tesela
    "/api/v1/proxy/mcp/imagery/metrics",                     # fuera de los prefijos
    "/api/v1/proxy/mcp/imagery/tiles/../metrics",            # traversal
    "/api/v1/proxy/mcp/otro/tiles/a/1/1/1.png",              # servidor no registrado
])
def test_solo_prefijos_declarados_de_servidores_registrados(entorno, ruta):
    assert entorno.http.get(ruta).status_code == 404
    assert entorno.pedidas == []


def test_respuestas_que_no_son_teselas_no_pasan(entorno):
    assert entorno.http.get("/api/v1/proxy/mcp/imagery/tiles/json/1/1/1.png").status_code == 502
    assert entorno.http.get("/api/v1/proxy/mcp/imagery/tiles/falla/1/1/1.png").status_code == 502


def test_query_rara_se_rechaza(entorno):
    assert entorno.http.get("/api/v1/proxy/mcp/imagery/tiles/a/1/1/1.png?x=<script>").status_code == 400
