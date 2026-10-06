"""S3.2 — cliente MCP genérico contra un servidor MCP REAL (hello-geo en proceso).

Protocolo de verdad (streamable HTTP + JSON-RPC del SDK), sin mocks del
transporte: el servidor corre con uvicorn en un puerto libre del test.
"""

from __future__ import annotations

import time

import pytest

from geo_copilot.platform.mcp.config import McpConfig, ServerConfig
from geo_copilot.platform.mcp.connection import (
    McpConnection,
    McpError,
    ServidorNoDisponible,
)
from tests.mcp_helpers import CLAVE, puerto_libre


@pytest.fixture
def servidor(hello_mcp_url):
    return hello_mcp_url


def _cfg(url: str, **kw) -> ServerConfig:
    base = {"id": "hello", "url": url, "auth": {"type": "bearer", "secret_ref": "env:HELLO_TEST_KEY"},
            "conformance": "G1", "tools": {"allow": ["hello_*"]}}
    base.update(kw)
    return ServerConfig.model_validate(base)


@pytest.fixture
def clave(monkeypatch):
    monkeypatch.setenv("HELLO_TEST_KEY", CLAVE)


@pytest.mark.asyncio
async def test_lista_tools_con_meta_geo_y_llama(servidor, clave):
    con = McpConnection(_cfg(servidor))
    tools = {t.name: t for t in await con.list_tools()}
    assert set(tools) == {"hello_circle", "hello_about"}
    assert tools["hello_circle"].meta["geo"]["outputs"] == ["feature_collection"]
    assert con.estado == "disponible" and con.info_servidor["name"] == "hello-geo"

    r = await con.call_tool("hello_circle", {"lon": -74.08, "lat": 4.6, "meters": 300})
    assert not r.isError
    gr = r.structuredContent
    assert gr["geo_result"] == "1" and gr["facts"]["radio_m"] == 300


@pytest.mark.asyncio
async def test_allowlist_se_aplica_en_el_cliente(servidor, clave):
    con = McpConnection(_cfg(servidor, tools={"allow": ["hello_circle"]}))
    assert [t.name for t in await con.list_tools()] == ["hello_circle"]
    with pytest.raises(McpError, match="no está permitida"):
        await con.call_tool("hello_about", {})


@pytest.mark.asyncio
async def test_sin_credencial_el_servidor_no_esta_disponible(servidor, monkeypatch):
    monkeypatch.delenv("HELLO_TEST_KEY", raising=False)
    con = McpConnection(_cfg(servidor))
    with pytest.raises(ServidorNoDisponible, match="credencial"):
        await con.list_tools()
    assert con.estado == "no_disponible"


@pytest.mark.asyncio
async def test_servidor_caido_abre_el_breaker_y_lo_dice(clave):
    con = McpConnection(_cfg(f"http://127.0.0.1:{puerto_libre()}/mcp"))
    for _ in range(3):
        with pytest.raises(ServidorNoDisponible, match="no respondió"):
            await con.list_tools(forzar=True)
    # abierto: ni siquiera intenta, responde al instante con la causa
    t0 = time.monotonic()
    with pytest.raises(ServidorNoDisponible, match="no está disponible ahora mismo"):
        await con.list_tools(forzar=True)
    assert time.monotonic() - t0 < 0.5 and con.estado == "no_disponible"


@pytest.mark.asyncio
async def test_resultado_mas_grande_que_el_maximo_se_rechaza(servidor, clave):
    con = McpConnection(_cfg(servidor, policy={"max_result_mb": 0.001}))
    with pytest.raises(McpError, match="supera el máximo"):
        await con.call_tool("hello_circle", {"lon": 0, "lat": 0, "meters": 100})


def test_config_escritura_siempre_aprueba_y_ids_unicos():
    with pytest.raises(ValueError, match="no negociable"):
        _cfg("http://x/mcp", policy={"hitl": {"write": "auto"}})
    with pytest.raises(ValueError, match="repetidos"):
        McpConfig.model_validate({"servers": [
            {"id": "a", "url": "http://x", "tools": {"allow": ["*"]}},
            {"id": "a", "url": "http://y", "tools": {"allow": ["*"]}},
        ]})
    with pytest.raises(ValueError):
        _cfg("http://x/mcp", tools={"allow": []})  # allowlist obligatoria
