"""F6 (S6.2, E6.3) — conexiones MCP de cada organización: cifradas, acotadas en red y solo suyas.

El recorrido de punta a punta usa un servidor MCP REAL (hello-geo en proceso): una
administradora de «acme» lo da de alta con su credencial desde la API; los de acme lo usan;
para «beta» no existe; la credencial no aparece en ninguna respuesta ni en los logs.
"""

from __future__ import annotations

import base64
import logging
import os

import httpx
import pytest

from geo_copilot.platform.capabilities import Capability, ToolOutcome, registry
from geo_copilot.platform.conexiones.cifrado import CifradoNoConfigurado, Cifrador, SecretoIlegible
from geo_copilot.platform.conexiones.red import TransporteFijado, UrlNoPermitida, validar_url
from geo_copilot.platform.conexiones.store import ConexionesEnMemoria, ConexionExiste
from geo_copilot.platform.identidad.principal import Principal, fijar_principal, restaurar_principal
from geo_copilot.platform.mcp.config import ServerConfig
from tests.mcp_helpers import CLAVE

KEK = base64.b64encode(os.urandom(32)).decode()
CARLA = Principal(sub="carla", org_id="acme", roles=frozenset({"admin"}), nombre="Carla")
ANA = Principal(sub="ana", org_id="acme", roles=frozenset({"analyst"}), nombre="Ana")
DIEGO = Principal(sub="diego", org_id="beta", roles=frozenset({"admin"}), nombre="Diego")


# ---------------------------------------------------------------------------
# Cifrado de sobre
# ---------------------------------------------------------------------------


def test_el_secreto_se_cifra_y_solo_se_descifra_en_su_fila():
    c = Cifrador.desde_entorno({"SECRETS_KEK": KEK})
    sobre = c.cifrar("sk-super-secreto", org_id="acme", conexion_id="almacen")
    assert b"sk-super-secreto" not in sobre and sobre[:2] == b"v1"
    assert c.descifrar(sobre, org_id="acme", conexion_id="almacen") == "sk-super-secreto"
    # copiado a otra conexión u otra organización: ilegible
    for org, cid in (("beta", "almacen"), ("acme", "otra")):
        with pytest.raises(SecretoIlegible):
            c.descifrar(sobre, org_id=org, conexion_id=cid)
    # alterado: ilegible
    dañado = sobre[:-1] + bytes([sobre[-1] ^ 1])
    with pytest.raises(SecretoIlegible):
        c.descifrar(dañado, org_id="acme", conexion_id="almacen")
    # dos cifrados del mismo secreto no se parecen (DEK y nonces nuevos)
    assert c.cifrar("x", org_id="a", conexion_id="b") != c.cifrar("x", org_id="a", conexion_id="b")


def test_rotacion_de_la_clave_maestra():
    vieja = base64.b64encode(os.urandom(32)).decode()
    antes = Cifrador.desde_entorno({"SECRETS_KEK": vieja})
    sobre = antes.cifrar("s", org_id="o", conexion_id="c")
    ahora = Cifrador.desde_entorno({"SECRETS_KEK": KEK, "SECRETS_KEK_ANTERIORES": vieja})
    assert ahora.descifrar(sobre, org_id="o", conexion_id="c") == "s"
    nuevo = ahora.recifrar(sobre, org_id="o", conexion_id="c")
    solo_nueva = Cifrador.desde_entorno({"SECRETS_KEK": KEK})
    assert solo_nueva.descifrar(nuevo, org_id="o", conexion_id="c") == "s"
    with pytest.raises(SecretoIlegible, match="clave maestra"):
        solo_nueva.descifrar(sobre, org_id="o", conexion_id="c")


def test_sin_clave_maestra_o_con_una_mala_no_hay_cifrado():
    with pytest.raises(CifradoNoConfigurado):
        Cifrador.desde_entorno({})
    with pytest.raises(CifradoNoConfigurado, match="32 bytes"):
        Cifrador.desde_entorno({"SECRETS_KEK": base64.b64encode(b"corta").decode()})


# ---------------------------------------------------------------------------
# Red: a dónde puede ir una conexión de organización
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("url, motivo", [
    ("http://example.com/mcp", "https"),
    ("https://127.0.0.1/mcp", "interna"),
    ("https://localhost/mcp", "interna"),
    ("https://169.254.169.254/latest", "interna"),
    ("https://10.1.2.3/mcp", "interna"),
    ("https://user:pw@example.com/mcp", "credenciales"),
])
async def test_urls_que_una_organizacion_no_puede_usar(url, motivo):
    with pytest.raises(UrlNoPermitida, match=motivo):
        await validar_url(url, frozenset())


@pytest.mark.asyncio
async def test_los_hosts_permitidos_por_la_plataforma_pasan():
    await validar_url("http://almacen-demo:9500/mcp", frozenset({"almacen-demo"}))


@pytest.mark.asyncio
async def test_el_transporte_conecta_a_la_ip_validada_con_el_nombre_en_host_y_sni(monkeypatch):
    """Anti DNS rebinding: la IP con la que se valida es a la que se conecta."""
    from geo_copilot.platform.conexiones import red

    vistas: list[httpx.Request] = []

    class Interno(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            vistas.append(request)
            return httpx.Response(200)

    async def resolver(host):
        return "93.184.216.34"

    monkeypatch.setattr(red, "resolver_publica", resolver)
    t = TransporteFijado(frozenset())
    t._interno = Interno()
    async with httpx.AsyncClient(transport=t) as http:
        await http.get("https://servicio.example.org/mcp")
        with pytest.raises(UrlNoPermitida):
            await http.get("https://10.0.0.1/mcp")  # IP interna literal
        with pytest.raises(UrlNoPermitida):
            await http.get("http://servicio.example.org/mcp")
    (r,) = vistas
    assert r.url.host == "93.184.216.34" and r.headers["Host"] == "servicio.example.org"
    assert r.extensions["sni_hostname"] == "servicio.example.org"


# ---------------------------------------------------------------------------
# Registro por organización
# ---------------------------------------------------------------------------


def _cap(nombre: str, org: str | None) -> Capability:
    async def ej(_g, _w, _a):
        return ToolOutcome("ok", True)

    return Capability(id=f"x.{nombre}", tool_name=nombre, description="d", parameters={"type": "object"},
                      executor=ej, blurb="b", org_id=org)


def test_una_herramienta_de_una_organizacion_no_existe_para_otra():
    registry().register(_cap("demo__t", "acme"), replace=True)
    try:
        for p, visible in ((ANA, True), (DIEGO, False), (None, False)):
            t = fijar_principal(p)
            try:
                assert (registry().get("demo__t") is not None) is visible
                assert ("demo__t" in registry().names()) is visible
            finally:
                restaurar_principal(t)
        # y no puede tapar una de la plataforma
        registry().register(_cap("plat__t", None), replace=True)
        with pytest.raises(ValueError, match="plataforma"):
            registry().register(_cap("plat__t", "acme"))
    finally:
        registry().unregister("demo__t", org_id="acme")
        registry().unregister("plat__t")


# ---------------------------------------------------------------------------
# Almacén
# ---------------------------------------------------------------------------


def _cfg(cid: str = "demo", url: str = "https://mcp.example.org/mcp") -> ServerConfig:
    return ServerConfig.model_validate({"id": cid, "url": url, "auth": {"type": "bearer"},
                                        "tools": {"allow": ["*"]}, "red_restringida": True})


@pytest.mark.asyncio
async def test_el_almacen_nunca_devuelve_la_credencial_salvo_al_hub():
    s = ConexionesEnMemoria(Cifrador.desde_entorno({"SECRETS_KEK": KEK}))
    c = await s.crear("acme", _cfg(), "sk-123", "carla")
    assert c.publica()["tiene_credencial"] is True and "sk-123" not in repr(c.publica())
    with pytest.raises(ConexionExiste):
        await s.crear("acme", _cfg(), None, "carla")
    assert await s.listar("beta") == []
    (cfg,) = await s.configs("acme")
    assert cfg.auth.resolver() == "sk-123" and cfg.red_restringida
    assert "sk-123" not in repr(cfg) and "sk-123" not in cfg.model_dump_json()
    # sin clave maestra, una credencial no se puede guardar
    with pytest.raises(CifradoNoConfigurado):
        await ConexionesEnMemoria(None).crear("acme", _cfg("otra"), "sk", "carla")


# ---------------------------------------------------------------------------
# De punta a punta: alta desde la API con un servidor MCP real (E6.3)
# ---------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch, hello_mcp_url):
    from fastapi.testclient import TestClient

    from geo_copilot.api import app as app_module
    from geo_copilot.api.auth import require_principal
    from geo_copilot.platform.conexiones import hubs as hubs_mod
    from geo_copilot.platform.mcp import hub as hub_mod

    monkeypatch.setenv("SECRETS_KEK", KEK)
    monkeypatch.setenv("MCP_HOSTS_PERMITIDOS", "127.0.0.1")  # el servidor de prueba, en local
    quien = {"p": CARLA}

    async def principal() -> Principal:
        fijar_principal(quien["p"])
        await hubs_mod.hubs_actuales().asegurar(quien["p"].org_id)
        return quien["p"]

    fastapi_app = app_module.create_app()
    fastapi_app.dependency_overrides[require_principal] = principal
    with TestClient(fastapi_app) as c:
        hubs_mod.instalar(hubs_mod.HubsDeOrganizaciones(ConexionesEnMemoria(Cifrador.desde_entorno())))
        yield c, quien, hello_mcp_url
    for org in hub_mod.hubs_org():
        hub_mod.instalar_hub_org(org, None)


def test_una_admin_da_de_alta_una_conexion_y_solo_su_organizacion_la_usa(api, caplog):
    c, quien, url = api
    caplog.set_level(logging.DEBUG)
    # un analista no puede darla de alta
    quien["p"] = ANA
    assert c.post("/api/v1/connections", json={"id": "demo", "url": url, "credencial": CLAVE}).status_code == 403
    # la administradora sí; la respuesta dice que hay credencial, nunca cuál
    quien["p"] = CARLA
    r = c.post("/api/v1/connections", json={"id": "demo", "url": url, "credencial": CLAVE,
                                            "description": "Círculos de prueba"})
    assert r.status_code == 201, r.text
    assert r.json()["tiene_credencial"] is True and r.json()["estado"]["estado"] == "disponible"
    listado = c.get("/api/v1/connections")
    assert [x["id"] for x in listado.json()["de_la_organizacion"]] == ["demo"]
    # la usa un analista de acme (misma capacidad que el agente)
    quien["p"] = ANA
    tools = c.get("/api/v1/connections/tools").json()["tools"]
    assert any(t["herramienta"] == "demo__hello_circle" for t in tools)
    # un tercero NO confiable: su «readOnlyHint» no baja el riesgo; el agente pide aprobación
    assert next(t for t in tools if t["herramienta"] == "demo__hello_circle")["riesgo"] == "external_egress"
    run = c.post("/api/v1/connections/demo/tools/hello_about/run", json={"session_id": "s-ana", "arguments": {}})
    assert run.status_code == 200 and run.json()["success"], run.text
    # para beta no existe
    quien["p"] = DIEGO
    assert all(t["herramienta"] != "demo__hello_circle" for t in c.get("/api/v1/connections/tools").json()["tools"])
    assert c.get("/api/v1/connections").json()["de_la_organizacion"] == []
    assert c.post("/api/v1/connections/demo/tools/hello_about/run",
                  json={"session_id": "s-diego", "arguments": {}}).status_code == 404
    assert c.delete("/api/v1/connections/demo").status_code == 404
    # la credencial no aparece en ninguna respuesta ni en los logs
    for resp in (r, listado):
        assert CLAVE not in resp.text
    assert CLAVE not in caplog.text
    # baja: deja de existir también para acme
    quien["p"] = CARLA
    assert c.delete("/api/v1/connections/demo").status_code == 200
    quien["p"] = ANA
    assert all(t["herramienta"] != "demo__hello_circle" for t in c.get("/api/v1/connections/tools").json()["tools"])


def test_una_url_interna_no_se_puede_dar_de_alta(api):
    c, quien, _url = api
    r = c.post("/api/v1/connections", json={"id": "espia", "url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400 and "no permitida" in r.json()["detail"]
    r = c.post("/api/v1/connections", json={"id": "bd", "url": "https://10.0.0.5:5432/"})
    assert r.status_code == 400
