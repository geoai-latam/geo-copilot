"""E3.2 — panel de herramientas genérico: formulario desde `input_schema`, misma capacidad que el agente.

Contra hello-geo REAL en proceso: lo que el panel lista y ejecuta es lo que el
servidor declara, sin código del núcleo para ninguna tool concreta.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from geo_copilot.api.app import create_app
from geo_copilot.platform.capabilities import registry
from geo_copilot.platform.mcp import hub as hub_mod
from geo_copilot.platform.mcp.config import McpConfig
from geo_copilot.platform.workspace import context as wsctx
from tests.mcp_helpers import CLAVE


@pytest.fixture
async def http(hello_mcp_url, monkeypatch):
    monkeypatch.setenv("HELLO_TEST_KEY", CLAVE)
    cfg = McpConfig.model_validate({"servers": [{
        "id": "hello", "url": hello_mcp_url, "conformance": "G1",
        "auth": {"type": "bearer", "secret_ref": "env:HELLO_TEST_KEY"},
        "tools": {"allow": ["hello_*"]},
    }]})
    hub = hub_mod.McpHub(cfg)
    await hub.refrescar()
    monkeypatch.setattr(hub_mod, "_hub", hub)
    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_aaaaaaaaaaaaaaaa", "name": "Círculo", "feature_count": 1}
    store = SimpleNamespace(ingest_features=AsyncMock(return_value=ref),
                            to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": []}))
    monkeypatch.setattr(wsctx, "_store", store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield SimpleNamespace(c=c, hub=hub, store=store)
    for n in list(hub.tools):
        registry().unregister(n)


@pytest.mark.asyncio
async def test_lista_las_tools_con_su_esquema(http):
    tools = {t["tool"]: t for t in (await http.c.get("/api/v1/connections/tools")).json()["tools"]}
    assert set(tools) == {"hello_circle", "hello_about"}
    circ = tools["hello_circle"]
    assert circ["server"] == "hello" and circ["estado"] == "disponible"
    assert {"lon", "lat", "meters"} <= set(circ["input_schema"]["properties"])
    assert circ["geo"]["outputs"] == ["feature_collection"] and circ["description"]


@pytest.mark.asyncio
async def test_ejecuta_la_misma_capacidad_y_devuelve_la_forma_de_query(http):
    r = await http.c.post("/api/v1/connections/hello/tools/hello_circle/run", json={
        "session_id": "sess-panel", "arguments": {"lon": -74.08, "lat": 4.6, "meters": 300}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] and body["facts"]["radio_m"] == 300
    assert body["results"]["layer_ref"]["id"] == "ds_aaaaaaaaaaaaaaaa"
    assert body["results"]["geojson"] == {"type": "FeatureCollection", "features": []}
    assert http.store.ingest_features.call_args.args[0] == "sess-panel"  # workspace de ESA sesión


@pytest.mark.asyncio
async def test_argumentos_invalidos_vuelven_como_error_legible(http):
    r = await http.c.post("/api/v1/connections/hello/tools/hello_circle/run", json={
        "session_id": "s", "arguments": {"lon": "no-es-numero", "lat": 4.6, "meters": 300}})
    assert r.status_code == 200 and not r.json()["success"] and r.json()["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ruta", "codigo"), [
    ("/api/v1/connections/hello/tools/no_existe/run", 404),
    ("/api/v1/connections/otro/tools/hello_circle/run", 404),
])
async def test_tool_desconocida_404(http, ruta, codigo):
    assert (await http.c.post(ruta, json={"session_id": "s", "arguments": {}})).status_code == codigo


@pytest.mark.asyncio
async def test_tool_deshabilitada_por_rug_pull_no_se_ejecuta(http):
    http.hub.tools["hello__hello_circle"].habilitada = False
    registry().unregister("hello__hello_circle")
    r = await http.c.post("/api/v1/connections/hello/tools/hello_circle/run",
                          json={"session_id": "s", "arguments": {"lon": 0, "lat": 0, "meters": 1}})
    assert r.status_code == 404
    tools = (await http.c.get("/api/v1/connections/tools")).json()["tools"]
    assert "hello_circle" not in {t["tool"] for t in tools}


@pytest.mark.asyncio
async def test_las_de_escritura_no_se_ejecutan_desde_el_panel(http):
    http.hub.tools["hello__hello_circle"].riesgo = "write"
    r = await http.c.post("/api/v1/connections/hello/tools/hello_circle/run",
                          json={"session_id": "s", "arguments": {"lon": 0, "lat": 0, "meters": 1}})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sesion_con_formato_invalido_422(http):
    r = await http.c.post("/api/v1/connections/hello/tools/hello_circle/run",
                          json={"session_id": "../otra", "arguments": {}})
    assert r.status_code == 422
