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
    lento = r.run(lenta, timeout_s=0.1)
    assert "tiempo máximo de 0 s" in lento["error"] and lento["codigo"] == "fuente_lenta"
    assert r.run(falla_esperada) == {"error": "escena sin datos", "codigo": "parametro_invalido"}
    assert r.run(falla_rara) == {"error": "Fallo interno del servicio x: KeyError", "codigo": "error_interno"}


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
    # códigos estables (rt/1): caído o sin red se reintenta; otro 4xx es la fuente; lo nuestro, interno
    assert r.run(caido)["codigo"] == "fuente_no_disponible" and r.run(caido)["reintentar_en_s"] == 5
    assert r.run(sin_red)["codigo"] == "fuente_no_disponible"
    assert r.run(prohibido)["codigo"] == "fuente_respuesta_invalida" and "reintentar_en_s" not in r.run(prohibido)
    assert r.run(nuestro)["codigo"] == "error_interno"


def test_una_excepcion_esperada_declara_su_codigo():
    from geo_mcp_kit import ToolRunner

    class Caida(Exception):
        codigo = "fuente_no_disponible"
        reintentar_en_s = 30

    def falla():
        raise Caida("el servicio de la entidad está en mantenimiento")

    r = ToolRunner(timeout_s=5, service="x", expected_errors=(Caida,))
    assert r.run(falla) == {"error": "el servicio de la entidad está en mantenimiento",
                            "codigo": "fuente_no_disponible", "reintentar_en_s": 30}


def test_error_y_es_error():
    from geo_mcp_kit import CODIGOS, compact_result, error, es_error

    e = error("no está", "no_existe")
    assert es_error(e) and es_error({"error": "viejo, sin código"})
    assert not es_error({"error": "x", "facts": {}}) and not es_error({"geo_result": "1"})
    with pytest.raises(ValueError, match="desconocido"):
        error("x", "inventado")
    assert "saldo_agotado" in CODIGOS and len(CODIGOS) == 12
    res = compact_result(e)
    assert res.isError and res.structuredContent == e and res.content[0].text == "no está"


@pytest.mark.parametrize(("vistos", "total", "truncado", "esperado"), [
    (5, 5, None, False), (3, 10, None, True), (0, 0, None, False),
    (7, None, None, False), (2000, None, True, True),
])
def test_completitud(vistos, total, truncado, esperado):
    from geo_mcp_kit import completitud

    c = completitud(vistos, total, truncado=truncado)
    assert c == {"vistos": vistos, "total": total, "truncado": esperado}


def test_completitud_imposible():
    from geo_mcp_kit import completitud

    with pytest.raises(ValueError):
        completitud(6, 5)
    with pytest.raises(ValueError):
        completitud(-1, None)


def test_herramientas_en_hilo_no_bloquean_el_bucle():
    """Una tool síncrona lenta no frena a las demás ni al servidor (Terranova F4.2)."""
    import asyncio

    from geo_mcp_kit import herramientas_en_hilo
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("prueba")

    @mcp.tool()
    def lenta(segundos: float) -> dict:
        time.sleep(segundos)
        return {"durmio": segundos}

    @mcp.tool()
    async def ya_async() -> dict:
        return {"ok": True}

    herramientas_en_hilo(herramientas_en_hilo(mcp))  # idempotente
    assert lenta(0) == {"durmio": 0}  # la función del módulo sigue siendo síncrona

    async def correr() -> tuple[float, list]:
        t = time.monotonic()
        r = await asyncio.gather(*(mcp.call_tool("lenta", {"segundos": 0.5}) for _ in range(4)),
                                 mcp.call_tool("ya_async", {}))
        return time.monotonic() - t, r

    duracion, resultados = asyncio.run(correr())
    assert duracion < 1.5, f"en serie: {duracion:.1f} s"  # 4 × 0,5 s en paralelo, no 2 s
    assert len(resultados) == 5
