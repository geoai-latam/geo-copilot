"""F7 (auditoría) — OIDC agnóstico del proveedor: los endpoints salen del descubrimiento.

Sin OIDC_JWKS_URL / OIDC_TOKEN_URL, la API lee `jwks_uri` y `token_endpoint` de
`{issuer}/.well-known/openid-configuration`. Antes derivaba la ruta de Keycloak, que en Entra ID
no existe: toda petición con Bearer daba 401. Se prueba con un proveedor simulado por httpx
(Keycloak, Entra v2.0) y con tokens firmados por una clave RSA de verdad.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from geo_copilot.platform.identidad import descubrimiento, intercambio, oidc
from geo_copilot.platform.identidad.oidc import ProveedorNoDisponible, ValidadorOIDC

_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)

KC_ISS = "https://idp.test/realms/geo"
KC_DOC = {"issuer": KC_ISS, "jwks_uri": f"{KC_ISS}/protocol/openid-connect/certs",
          "token_endpoint": f"{KC_ISS}/protocol/openid-connect/token"}

TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
ENTRA_ISS = f"https://login.microsoftonline.com/{TENANT}/v2.0"
ENTRA_DOC = {"issuer": ENTRA_ISS,
             "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
             "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"}
ENTRA_API = "6e74172b-be56-4843-9ff4-e66a39bb12e3"  # Application (client) ID de la API


class _Proveedor:
    """Un IdP simulado: sirve su documento de descubrimiento y su endpoint de token."""

    def __init__(self, docs: dict[str, Any], *, estado: int = 200, caido: bool = False) -> None:
        self.docs = docs  # issuer -> documento
        self.estado = estado
        self.caido = caido
        self.pedidas: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.pedidas.append(req)
        if self.caido:
            raise httpx.ConnectError("sin ruta", request=req)
        url = str(req.url)
        if req.method == "GET":
            for iss, doc in self.docs.items():
                if url == f"{iss}/.well-known/openid-configuration":
                    return httpx.Response(self.estado, json=doc)
            return httpx.Response(404)
        return httpx.Response(200, json={"access_token": f"tok-de:{url}", "expires_in": 300})

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.pedidas]


class _JwksFalso:
    """Lo que haría PyJWKClient contra la URL con la que se construye: dar la clave pública."""

    creados: list[str] = []

    def __init__(self, url: str, **_kw: Any) -> None:
        _JwksFalso.creados.append(url)

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=_CLAVE.public_key())


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    descubrimiento._cache.clear()
    intercambio._cache.clear()
    _JwksFalso.creados = []
    monkeypatch.setattr(oidc.jwt, "PyJWKClient", _JwksFalso)
    yield
    descubrimiento._cache.clear()
    intercambio._cache.clear()


def _servir(monkeypatch, proveedor: _Proveedor) -> None:
    """Las peticiones que la API hace por su cuenta (descubrimiento) van al proveedor simulado."""
    original = httpx.AsyncClient

    def cliente(*args: Any, **kw: Any) -> httpx.AsyncClient:
        kw["transport"] = httpx.MockTransport(proveedor)
        return original(*args, **kw)

    monkeypatch.setattr(descubrimiento.httpx, "AsyncClient", cliente)


def _token(iss: str, aud: str, **claims: Any) -> str:
    ahora = int(time.time())
    return jwt.encode({"iss": iss, "aud": aud, "sub": "u-ana", "iat": ahora, "exp": ahora + 300, **claims},
                      _CLAVE, algorithm="RS256", headers={"kid": "k1"})


def _ajustes(iss: str, token_url: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(oidc_issuer=iss, oidc_token_url=token_url, oidc_api_client_id="geo-copilot-api",
                           oidc_api_client_secret=SecretStr("s3cr3t"))


# ---------------------------------------------------------------------------
# Validación del token: las claves salen de la `jwks_uri` publicada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keycloak_sin_jwks_url_toma_las_claves_de_su_descubrimiento(monkeypatch):
    idp = _Proveedor({KC_ISS: KC_DOC})
    _servir(monkeypatch, idp)
    v = ValidadorOIDC(issuer=KC_ISS, audience="geo-copilot-api")
    tok = _token(KC_ISS, "geo-copilot-api", org="acme", realm_access={"roles": ["analyst"]})

    p1 = await v.validar(tok)
    p2 = await v.validar(tok)

    assert (p1.org_id, p1.rol) == (p2.org_id, p2.rol) == ("acme", "analyst")
    assert idp.urls == [f"{KC_ISS}/.well-known/openid-configuration"]  # una vez: la segunda, de caché
    assert _JwksFalso.creados == [KC_DOC["jwks_uri"]]


@pytest.mark.asyncio
async def test_entra_v2_funciona_sin_ninguna_ruta_de_keycloak(monkeypatch):
    idp = _Proveedor({ENTRA_ISS: ENTRA_DOC})
    _servir(monkeypatch, idp)
    # Entra: la organización es el inquilino (`tid`) y los roles, los de aplicación (`roles`)
    v = ValidadorOIDC(issuer=ENTRA_ISS, audience=ENTRA_API, org_claim="tid", roles_claim="roles")

    p = await v.validar(_token(ENTRA_ISS, ENTRA_API, tid=TENANT, roles=["admin", "Otro.Rol"], name="Ana"))

    assert (p.org_id, p.rol, p.nombre, p.via) == (TENANT, "admin", "Ana", "oidc")
    assert _JwksFalso.creados == [ENTRA_DOC["jwks_uri"]]
    assert idp.urls == [f"{ENTRA_ISS}/.well-known/openid-configuration"]
    assert not any("openid-connect" in u for u in idp.urls + _JwksFalso.creados)


@pytest.mark.asyncio
async def test_un_emisor_con_barra_final_casa_con_su_iss(monkeypatch):
    # Entra v1.0: `iss` = https://sts.windows.net/<tenant>/ (con barra). Antes se quitaba la barra
    # del emisor configurado y ningún token casaba.
    iss = f"https://sts.windows.net/{TENANT}/"
    v = ValidadorOIDC(issuer=iss, audience="api://geo", org_claim="tid", roles_claim="roles",
                      jwks_client=_JwksFalso("explicita"))
    p = await v.validar(_token(iss, "api://geo", tid=TENANT, roles=["viewer"]))
    assert (p.org_id, p.rol) == (TENANT, "viewer")


@pytest.mark.asyncio
@pytest.mark.parametrize("configurado, del_token, vale", [
    (f"https://sts.windows.net/{TENANT}/", f"https://sts.windows.net/{TENANT}/", True),
    (f"https://sts.windows.net/{TENANT}", f"https://sts.windows.net/{TENANT}/", True),
    (KC_ISS + "/", KC_ISS, True),
    (KC_ISS, "https://otro.test/realms/geo", False),
])
async def test_el_emisor_llega_a_pyjwt_como_str_sea_cual_sea_su_version(monkeypatch, configurado, del_token, vale):
    """PyJWT 2.8 compara `payload["iss"] != issuer`, 2.9 solo admite str o list y 2.10 str o
    Sequence: con un conjunto, cualquiera de ellas rechazaba TODOS los tokens. Se imita esa
    comparación estricta, así el test no depende de la PyJWT instalada."""
    original = oidc.jwt.decode
    exigidos: list[object] = []

    def decode_estricto(*a: Any, **kw: Any) -> Any:
        if "issuer" in kw:
            exigidos.append(kw["issuer"])
            assert isinstance(kw["issuer"], str), f"issuer={kw['issuer']!r}"
        return original(*a, **kw)

    monkeypatch.setattr(oidc.jwt, "decode", decode_estricto)
    v = ValidadorOIDC(issuer=configurado, audience="api://geo", jwks_client=_JwksFalso("explicita"))
    tok = _token(del_token, "api://geo", org="acme")
    if vale:
        assert (await v.validar(tok)).org_id == "acme"
        assert exigidos == [del_token]
    else:
        with pytest.raises(oidc.TokenInvalido, match="InvalidIssuerError"):
            await v.validar(tok)
        assert exigidos == [configurado]


@pytest.mark.asyncio
async def test_con_jwks_url_explicita_no_se_descubre_nada(monkeypatch):
    idp = _Proveedor({KC_ISS: KC_DOC})
    _servir(monkeypatch, idp)
    interna = "http://keycloak:8080/realms/geo/protocol/openid-connect/certs"  # OIDC_JWKS_URL_INTERNA
    v = ValidadorOIDC(issuer=KC_ISS, audience="geo-copilot-api", jwks_url=interna)
    p = await v.validar(_token(KC_ISS, "geo-copilot-api", org="acme", realm_access={"roles": ["viewer"]}))
    assert p.rol == "viewer" and idp.pedidas == [] and _JwksFalso.creados == [interna]


@pytest.mark.asyncio
@pytest.mark.parametrize("proveedor, motivo", [
    (_Proveedor({}), "respondió 404"),
    (_Proveedor({KC_ISS: KC_DOC}, caido=True), "no respondió"),
    (_Proveedor({KC_ISS: {**KC_DOC, "issuer": "https://otro.test/realms/geo"}}), "declara el emisor"),
    (_Proveedor({KC_ISS: {"issuer": KC_ISS}}), "no trae «jwks_uri»"),
])
async def test_si_el_descubrimiento_falla_se_dice_que_fallo_y_no_se_adivina_la_ruta(monkeypatch, proveedor, motivo):
    _servir(monkeypatch, proveedor)
    v = ValidadorOIDC(issuer=KC_ISS, audience="geo-copilot-api")
    tok = _token(KC_ISS, "geo-copilot-api", org="acme")

    with pytest.raises(ProveedorNoDisponible, match=motivo):
        await v.validar(tok)

    assert _JwksFalso.creados == []  # ni la ruta de Keycloak ni ninguna otra
    assert all(u.endswith("/.well-known/openid-configuration") for u in proveedor.urls)


@pytest.mark.asyncio
async def test_con_el_proveedor_caido_no_se_espera_el_timeout_en_cada_peticion(monkeypatch):
    idp = _Proveedor({KC_ISS: KC_DOC}, caido=True)
    _servir(monkeypatch, idp)
    v = ValidadorOIDC(issuer=KC_ISS, audience="geo-copilot-api")
    tok = _token(KC_ISS, "geo-copilot-api", org="acme")
    for _ in range(3):
        with pytest.raises(ProveedorNoDisponible, match="no respondió"):
            await v.validar(tok)
    assert len(idp.pedidas) == 1  # el fallo se recuerda un rato (VIDA_FALLO_S)

    # pasado ese rato se vuelve a preguntar, y si el proveedor ya responde, todo sigue
    idp.caido = False
    descubrimiento._cache[KC_ISS] = (0.0, descubrimiento._cache[KC_ISS][1])
    p = await v.validar(_token(KC_ISS, "geo-copilot-api", org="acme", realm_access={"roles": ["admin"]}))
    assert p.rol == "admin" and len(idp.pedidas) == 2


# ---------------------------------------------------------------------------
# Token exchange: el endpoint de token también sale del descubrimiento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("iss, doc", [(KC_ISS, KC_DOC), (ENTRA_ISS, ENTRA_DOC)])
async def test_el_intercambio_sin_token_url_usa_el_token_endpoint_publicado(iss, doc):
    idp = _Proveedor({iss: doc})
    async with httpx.AsyncClient(transport=httpx.MockTransport(idp)) as http:
        tok = await intercambio.intercambiar("tok-de-ana", "hello-geo", settings=_ajustes(iss), http=http)
    assert tok == f"tok-de:{doc['token_endpoint']}"
    assert [(r.method, str(r.url)) for r in idp.pedidas] == [
        ("GET", f"{iss}/.well-known/openid-configuration"), ("POST", doc["token_endpoint"])]


@pytest.mark.asyncio
async def test_el_intercambio_con_token_url_explicita_no_descubre():
    idp = _Proveedor({KC_ISS: KC_DOC})
    interna = "http://keycloak:8080/realms/geo/protocol/openid-connect/token"  # OIDC_TOKEN_URL_INTERNA
    async with httpx.AsyncClient(transport=httpx.MockTransport(idp)) as http:
        await intercambio.intercambiar("t", "hello-geo", settings=_ajustes(KC_ISS, interna), http=http)
    assert [(r.method, str(r.url)) for r in idp.pedidas] == [("POST", interna)]


@pytest.mark.asyncio
@pytest.mark.parametrize("proveedor, motivo", [
    (_Proveedor({}), "respondió 404"),
    (_Proveedor({KC_ISS: {"issuer": KC_ISS, "jwks_uri": KC_DOC["jwks_uri"]}}), "no trae «token_endpoint»"),
])
async def test_si_no_hay_endpoint_de_token_el_intercambio_falla_y_dice_por_que(proveedor, motivo):
    async with httpx.AsyncClient(transport=httpx.MockTransport(proveedor)) as http:
        with pytest.raises(intercambio.IntercambioFallido, match=motivo):
            await intercambio.intercambiar("t", "hello-geo", settings=_ajustes(KC_ISS), http=http)
    assert [r.method for r in proveedor.pedidas] == ["GET"]  # nunca un POST a una ruta adivinada


# ---------------------------------------------------------------------------
# De extremo a extremo por HTTP: con el proveedor caído, 503 y la causa (no un 500 ni un 401)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_con_idp(monkeypatch):
    """La app real con OIDC sin OIDC_JWKS_URL: las claves dependen del descubrimiento."""
    from fastapi.testclient import TestClient

    from geo_copilot.api import app as app_module
    from geo_copilot.core.config import get_settings
    from geo_copilot.platform.identidad import servicio
    from geo_copilot.platform.identidad.propiedad import PropiedadEnMemoria
    from geo_copilot.platform.identidad.tickets import TicketsEnMemoria

    cfg = get_settings().model_copy(update={"oidc_issuer": KC_ISS, "oidc_audience": "geo-copilot-api",
                                            "oidc_jwks_url": None, "api_key": None})
    monkeypatch.setattr(app_module, "get_settings", lambda: cfg)
    monkeypatch.setattr("geo_copilot.api.websocket.get_settings", lambda: cfg)
    fastapi_app = app_module.create_app()
    fastapi_app.dependency_overrides[get_settings] = lambda: cfg
    with TestClient(fastapi_app, raise_server_exceptions=False) as c:
        servicio.instalar(servicio.Identidad(
            validador=ValidadorOIDC(issuer=KC_ISS, audience="geo-copilot-api"),
            tickets=TicketsEnMemoria(), propiedad=PropiedadEnMemoria()))
        yield c
    servicio.instalar(None)


@pytest.mark.parametrize("proveedor, causa", [
    (_Proveedor({KC_ISS: KC_DOC}, caido=True), "no respondió"),
    (_Proveedor({KC_ISS: {"issuer": KC_ISS}}), "no trae «jwks_uri»"),
])
def test_un_bearer_con_el_proveedor_caido_da_503_con_la_causa(monkeypatch, caplog, api_con_idp, proveedor, causa):
    _servir(monkeypatch, proveedor)
    cabeceras = {"Authorization": f"Bearer {_token(KC_ISS, 'geo-copilot-api', org='acme')}"}
    with caplog.at_level("WARNING"):
        for _ in range(2):  # la segunda sale de la caché del fallo: misma respuesta, mismo log
            r = api_con_idp.get("/api/v1/auth/yo", headers=cabeceras)
            assert r.status_code == 503, r.text
            assert causa in r.json()["detail"] and "proveedor de identidad" in r.json()["detail"]
            assert r.headers["Retry-After"] == str(int(descubrimiento.VIDA_FALLO_S))
    lineas = [x.getMessage() for x in caplog.records]
    assert sum("[identidad] petición con Bearer rechazada con 503" in m and causa in m for m in lineas) == 2
    assert not any("Unhandled exception" in m for m in lineas)
