"""S3.1 — geo_mcp_kit: la base común de los servidores GeoMCP propios."""

from __future__ import annotations

import time

import httpx
import pytest
from geo_mcp_kit import (
    ExtraRoute,
    GeoMcpAuth,
    KeyRing,
    RateLimiter,
    ToolRunner,
    feature_collection,
    geo_meta,
    geo_result,
    respond_json,
    table,
)

SCOPES = {"t_leer": "x:read", "t_calcular": "x:compute"}


def _ring() -> KeyRing:
    return KeyRing(
        [{"name": "app", "key": "k-app", "scopes": ["x:read", "x:compute"], "rate_limit_per_min": 3},
         {"name": "ro", "key": "k-ro", "scopes": ["x:read"]}],
        known_scopes={"x:read", "x:compute"}, tool_scopes=SCOPES,
    )


def test_keyring_fail_closed_y_mapa_validado():
    ring = _ring()
    app, ro = ring.verify("k-app"), ring.verify("k-ro")
    assert app.allows_tool("t_calcular") and ro.allows_tool("t_leer")
    assert not ro.allows_tool("t_calcular") and not app.allows_tool("t_que_no_existe")
    assert ring.verify("otra") is None and ring.verify(None) is None
    with pytest.raises(ValueError, match="no declarados"):
        KeyRing([], known_scopes={"a"}, tool_scopes={"t": "b"})
    with pytest.raises(ValueError, match="desconocidos"):
        KeyRing([{"name": "n", "key": "k", "scopes": ["zz"]}], known_scopes={"a"}, tool_scopes={})


async def _echo(scope, receive, send):
    body = (await receive()).get("body", b"")
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body or b"{}"})


def _app(**kw):
    async def tesela(scope, send, params, key):
        await respond_json(send, 200, {"tesela": params, "clave": key.name})

    ruta = ExtraRoute(lambda p: p[len("/tiles/"):] if p.startswith("/tiles/") else None,
                      tesela, requires_tool="t_calcular", weight=0.5)
    return GeoMcpAuth(_echo, _ring(), RateLimiter(), service="x", routes=(ruta,),
                      metrics=lambda: {"hits": 1}, **kw)


def _call(tool):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {}}}


@pytest.mark.asyncio
async def test_capas_de_auth_del_servidor():
    t = httpx.ASGITransport(app=_app(max_body_bytes=500))
    async with httpx.AsyncClient(transport=t, base_url="http://s") as c:
        assert (await c.get("/health")).json() == {"status": "healthy", "service": "x"}
        assert (await c.post("/mcp", json={})).status_code == 401
        assert (await c.get("/metrics")).status_code == 401
        ro = {"authorization": "Bearer k-ro"}
        app = {"authorization": "Bearer k-app"}
        assert (await c.get("/metrics", headers=app)).json()["metrics"] == {"hits": 1}
        # tools/call sin scope → 403; con scope, el cuerpo llega intacto al SDK
        assert (await c.post("/mcp", json=_call("t_calcular"), headers=ro)).status_code == 403
        r = await c.post("/mcp", json=_call("t_leer"), headers=ro)
        assert r.status_code == 200 and r.json()["params"]["name"] == "t_leer"
        # ruta extra hereda el scope de su tool
        assert (await c.get("/tiles/1/2/3", headers=ro)).status_code == 403
        assert (await c.get("/tiles/1/2/3", headers=app)).json() == {"tesela": "1/2/3", "clave": "app"}
        # cuerpo enorme
        assert (await c.post("/mcp", content=b"x" * 600, headers=ro)).status_code == 413


@pytest.mark.asyncio
async def test_rate_limit_con_peso_por_ruta():
    t = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=t, base_url="http://s") as c:
        app = {"authorization": "Bearer k-app"}   # 3 por minuto
        codigos = [(await c.get("/tiles/a", headers=app)).status_code for _ in range(6)]  # peso 0.5
        assert codigos == [200] * 6
        assert (await c.post("/mcp", json={}, headers=app)).status_code == 429


def test_tool_runner_timeout_y_errores_honestos():
    class MiError(Exception):
        pass

    def lenta():
        time.sleep(1)
        return {}

    def falla_esperada():
        raise MiError("escena sin datos")

    def falla_rara():
        raise KeyError("x")

    r = ToolRunner(timeout_s=5, service="x", expected_errors=(MiError,))
    assert r.run(lambda a: {"v": a}, 2) == {"v": 2}
    assert "tiempo máximo de 0 s" in r.run(lenta, timeout_s=0.1)["error"]
    assert r.run(falla_esperada) == {"error": "escena sin datos"}
    assert r.run(falla_rara) == {"error": "Fallo interno del servicio x: KeyError"}


def test_contrato_geomcp():
    meta = geo_meta(inputs={"aoi": ["geometry", "layer_ref"]}, outputs=["raster_tiles"], cost="high")
    assert meta["geo"]["inputs"]["aoi"]["accepts"] == ["geometry", "layer_ref"]
    fc = {"type": "FeatureCollection", "features": []}
    gr = geo_result([feature_collection("x", fc, crs="EPSG:4326")], facts={"n": 0})
    assert gr["geo_result"] == "1" and gr["facts"] == {"n": 0}
    # regla dura 1: CRS declarado
    with pytest.raises(ValueError, match="sin CRS"):
        geo_result([feature_collection("x", fc, crs="")])
    with pytest.raises(ValueError, match="sin CRS"):
        geo_result([table(["g"], [], geometry={"column": "g", "encoding": "wkb"})])


@pytest.mark.asyncio
async def test_despues_del_cuerpo_el_sdk_recibe_el_receive_real_y_no_gira():
    """Un GET /mcp abierto (canal de eventos del puente mcp-remote de Claude Desktop) dejaba
    arcgis-mcp al 100 % de CPU sin responder a nadie: tras el cuerpo, el envoltorio devolvía
    «cuerpo vacío» al instante para siempre, y el SDK llama a receive() para esperar la
    desconexión. Ahora la segunda llamada es el receive REAL (aquí: la desconexión)."""
    vistos: list[dict] = []

    async def app(scope, receive, send):
        vistos.append(await receive())
        vistos.append(await receive())  # el SDK esperando a que el cliente se desconecte
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    auth = GeoMcpAuth(app, _ring(), RateLimiter(), service="x")
    entrantes = [{"type": "http.request", "body": b"", "more_body": False}, {"type": "http.disconnect"}]

    async def receive():
        return entrantes.pop(0)

    async def send(_m):
        pass

    scope = {"type": "http", "method": "GET", "path": "/mcp", "headers": [(b"authorization", b"Bearer k-app")]}
    await auth(scope, receive, send)
    assert [m["type"] for m in vistos] == ["http.request", "http.disconnect"]


def test_un_fallo_del_proveedor_externo_no_se_presenta_como_interno():
    """F7 (T7.6): el 504 del token SAS de Planetary Computer salía como «Fallo interno del servicio
    imagery: HTTPStatusError»."""
    import httpx
    from geo_mcp_kit.runner import ToolRunner

    r = ToolRunner(timeout_s=5, service="imagery")
    peticion = httpx.Request("GET", "https://planetarycomputer.microsoft.com/api/sas/v1/token/x")

    def caido():
        httpx.Response(504, request=peticion).raise_for_status()

    def sin_red():
        raise httpx.ConnectTimeout("timed out", request=peticion)

    def prohibido():
        httpx.Response(403, request=peticion).raise_for_status()

    def nuestro():
        raise KeyError("bug")

    e = r.run(caido)["error"]
    assert "planetarycomputer.microsoft.com respondió 504" in e and "no es un fallo de imagery" in e
    assert "reintenta" in e
    assert "ConnectTimeout" in r.run(sin_red)["error"] and "reintenta" in r.run(sin_red)["error"]
    assert "403" in r.run(prohibido)["error"] and "reintenta" not in r.run(prohibido)["error"]
    assert r.run(nuestro)["error"] == "Fallo interno del servicio imagery: KeyError"
