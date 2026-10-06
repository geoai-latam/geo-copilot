"""F6 (S6.3) — propagación de identidad a los MCP: token exchange y tokens de usuario en el kit.

Un servidor con `auth: {type: token_exchange}` recibe, en cada llamada de una persona, un token
de ESA persona para él (no la clave de servicio). El kit lo verifica por JWKS y le da los scopes
de sus roles; la herramienta sabe quién llama.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from geo_copilot.platform.identidad import intercambio
from geo_copilot.platform.identidad.principal import Principal, fijar_principal, restaurar_principal
from geo_copilot.platform.mcp.config import ServerConfig

ISS = "https://idp.test/realms/geo"
_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# URL de token explícita (como OIDC_TOKEN_URL_INTERNA en el compose de desarrollo): aquí se prueba
# el intercambio; sin ella sale del descubrimiento, que cubre tests/test_f7_auditoria_oidc.py.
AJUSTES = SimpleNamespace(oidc_issuer=ISS, oidc_token_url=f"{ISS}/protocol/openid-connect/token",
                          oidc_api_client_id="geo-copilot-api", oidc_api_client_secret=SecretStr("s3cr3t"))


def _token(aud="hello-geo", roles=("analyst",), **extra) -> str:
    ahora = int(time.time())
    return jwt.encode({"iss": ISS, "aud": aud, "sub": "u-ana", "preferred_username": "ana", "org": "acme",
                       "iat": ahora, "exp": ahora + 300, "realm_access": {"roles": list(roles)}, **extra},
                      _CLAVE, algorithm="RS256")


@pytest.fixture(autouse=True)
def _cache_limpia():
    intercambio._cache.clear()
    yield
    intercambio._cache.clear()


# ---------------------------------------------------------------------------
# El intercambio con el proveedor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_intercambio_pide_al_proveedor_un_token_del_usuario_para_la_audiencia():
    pedidas: list[httpx.Request] = []

    def proveedor(req: httpx.Request) -> httpx.Response:
        pedidas.append(req)
        return httpx.Response(200, json={"access_token": "tok-para-hello", "expires_in": 300})

    async with httpx.AsyncClient(transport=httpx.MockTransport(proveedor)) as http:
        t1 = await intercambio.intercambiar("tok-de-ana", "hello-geo", settings=AJUSTES, http=http)
        t2 = await intercambio.intercambiar("tok-de-ana", "hello-geo", settings=AJUSTES, http=http)
    assert t1 == t2 == "tok-para-hello" and len(pedidas) == 1  # el segundo, de la caché
    (r,) = pedidas
    assert str(r.url) == f"{ISS}/protocol/openid-connect/token"
    form = dict(httpx.QueryParams(r.content.decode()))
    assert form == {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": "tok-de-ana",
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "requested_token_type": "urn:ietf:params:oauth:token-type:access_token", "audience": "hello-geo"}
    assert r.headers["Authorization"].startswith("Basic ")  # la API se autentica como cliente


@pytest.mark.asyncio
async def test_si_el_proveedor_rechaza_el_intercambio_se_dice_por_que():
    def proveedor(req):
        return httpx.Response(400, json={"error": "invalid_request", "error_description": "audience not available"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(proveedor)) as http:
        with pytest.raises(intercambio.IntercambioFallido, match="audience not available"):
            await intercambio.intercambiar("t", "otra", settings=AJUSTES, http=http)


# ---------------------------------------------------------------------------
# Qué token lleva cada llamada
# ---------------------------------------------------------------------------

CFG = ServerConfig.model_validate({"id": "hello", "url": "http://hello-geo:9200/mcp", "tools": {"allow": ["*"]},
                                   "auth": {"type": "token_exchange", "audience": "hello-geo",
                                            "secret_ref": "env:HELLO_TX_TEST_KEY"}})


@pytest.mark.asyncio
async def test_una_persona_llama_con_su_token_el_sistema_con_la_clave_de_servicio(monkeypatch):
    from geo_copilot.platform.mcp.connection import ServidorNoDisponible

    monkeypatch.setenv("HELLO_TX_TEST_KEY", "clave-servicio")

    async def falso(token, aud, **_):
        return f"intercambiado({token},{aud})"

    monkeypatch.setattr(intercambio, "intercambiar", falso)
    casos = [
        (None, "clave-servicio"),  # el sistema descubriendo tools
        (Principal(sub="svc", org_id="o", roles=frozenset({"admin"}), via="api_key"), "clave-servicio"),
        (Principal(sub="ana", org_id="acme", roles=frozenset({"analyst"}), via="oidc", token="T"),
         "intercambiado(T,hello-geo)"),
    ]
    for p, esperado in casos:
        t = fijar_principal(p)
        try:
            assert await intercambio.token_para(CFG) == esperado
        finally:
            restaurar_principal(t)
    # una persona sin su token en este canal: error, NUNCA la clave de servicio
    t = fijar_principal(Principal(sub="ana", org_id="acme", roles=frozenset({"analyst"}), via="oidc"))
    try:
        with pytest.raises(ServidorNoDisponible, match="identidad del usuario"):
            await intercambio.token_para(CFG)
    finally:
        restaurar_principal(t)


# ---------------------------------------------------------------------------
# El kit: tokens de usuario verificados por JWKS
# ---------------------------------------------------------------------------


def _verificador():
    from geo_mcp_kit import VerificadorJwt

    class Jwks:
        def get_signing_key_from_jwt(self, _t):
            return SimpleNamespace(key=_CLAVE.public_key())

    return VerificadorJwt(issuer=ISS, audience="hello-geo", jwks_url="x", jwks_client=Jwks(),
                          roles_a_scopes={"analyst": ["hello:use"], "admin": ["hello:use"]},
                          tool_scopes={"hello_about": "hello:use"}, known_scopes={"hello:use"})


@pytest.mark.asyncio
async def test_el_kit_da_los_scopes_de_los_roles_del_usuario():
    v = _verificador()
    ana = await v.verificar(_token())
    assert ana is not None and (ana.sub, ana.org, ana.name) == ("u-ana", "acme", "usuario:ana")
    assert ana.allows_tool("hello_about")
    visor = await v.verificar(_token(roles=("viewer",)))
    assert visor is not None and not visor.allows_tool("hello_about")  # su rol no da el scope
    assert await v.verificar(_token(aud="otra-api")) is None  # un token para otra audiencia no vale
    assert await v.verificar("clave-que-no-es-jwt") is None


def test_roles_con_scopes_no_declarados_no_arrancan():
    from geo_mcp_kit import VerificadorJwt

    with pytest.raises(ValueError, match="no declarados"):
        VerificadorJwt(issuer=ISS, audience="a", jwks_url="x", jwks_client=object(),
                       roles_a_scopes={"analyst": ["todo:poder"]}, tool_scopes={}, known_scopes={"hello:use"})


def test_el_kit_deja_pasar_al_usuario_con_el_scope_de_su_rol_y_la_tool_sabe_quien_es():
    """GeoMcpAuth con verificador JWT: 401 sin credencial, 403 si su rol no da el scope, y la
    herramienta ve al usuario (sub, organización) en `llamante()`."""
    from geo_mcp_kit import GeoMcpAuth, KeyRing, RateLimiter, llamante
    from starlette.testclient import TestClient

    async def app(scope, receive, send):  # la «tool»: responde quién la llama
        if scope["type"] == "lifespan":
            while (await receive())["type"] != "lifespan.shutdown":
                await send({"type": "lifespan.startup.complete"})
            await send({"type": "lifespan.shutdown.complete"})
            return
        quien = llamante()
        cuerpo = f"{quien.name}|{quien.sub}|{quien.org}".encode()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": cuerpo})

    keys = KeyRing([{"name": "svc", "key": "k-svc", "scopes": ["hello:use"]}], known_scopes={"hello:use"},
                   tool_scopes={"hello_about": "hello:use"})
    auth = GeoMcpAuth(app, keys, RateLimiter(), service="t", jwt=_verificador())
    llamada = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "hello_about", "arguments": {}}}
    with TestClient(auth) as c:
        assert c.post("/mcp", json=llamada).status_code == 401
        r = c.post("/mcp", json=llamada, headers={"Authorization": f"Bearer {_token()}"})
        assert r.status_code == 200 and r.text == "usuario:ana|u-ana|acme"
        r = c.post("/mcp", json=llamada, headers={"Authorization": f"Bearer {_token(roles=('viewer',))}"})
        assert r.status_code == 403  # su rol no le da el scope de la tool
        r = c.post("/mcp", json=llamada, headers={"Authorization": "Bearer k-svc"})
        assert r.status_code == 200 and r.text.startswith("svc|None")


def test_hello_about_nombra_al_usuario_o_a_la_clave_de_servicio():
    import importlib

    from geo_mcp_kit.auth import ApiKey
    from geo_mcp_kit.oidc import fijar_llamante

    import tests.mcp_helpers  # pone services/hello_geo en sys.path (antes, solo en la suite completa)

    hello = importlib.import_module("hello_geo.server")
    fijar_llamante(ApiKey(name="usuario:ana", key_hash="", scopes=frozenset(), sub="u-ana", org="acme"))
    assert "usuario «ana» de la organización «acme»" in hello.hello_about()
    fijar_llamante(ApiKey(name="svc", key_hash="", scopes=frozenset()))
    assert "clave de servicio «svc»" in hello.hello_about()
