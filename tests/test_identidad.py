"""F6 (S6.1) — identidad: tokens OIDC, propiedad de las sesiones y la API con dos usuarios.

El validador se prueba con tokens firmados por una clave RSA de verdad (la del «proveedor»),
no con un doble del decodificador: lo que se comprueba es la criptografía y los claims.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from geo_copilot.platform.identidad.oidc import SinOrganizacion, TokenInvalido, ValidadorOIDC
from geo_copilot.platform.identidad.principal import Principal
from geo_copilot.platform.identidad.propiedad import PropiedadEnMemoria, SesionAjena

ISSUER = "https://idp.test/realms/geo"
AUD = "geo-copilot-api"
_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTRA = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Jwks:
    """Lo que devolvería el endpoint JWKS del proveedor: su clave pública."""

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=_CLAVE.public_key())


def _token(clave=_CLAVE, alg="RS256", **claims) -> str:
    ahora = int(time.time())
    datos = {"iss": ISSUER, "aud": AUD, "sub": "u-ana", "iat": ahora, "exp": ahora + 300,
             "org": "acme", "realm_access": {"roles": ["analyst", "offline_access"]},
             "name": "Ana Analista", **claims}
    datos = {k: v for k, v in datos.items() if v is not None}
    return jwt.encode(datos, clave, algorithm=alg, headers={"kid": "k1"})


def _validador() -> ValidadorOIDC:
    return ValidadorOIDC(issuer=ISSUER, audience=AUD, jwks_client=_Jwks())


# ---------------------------------------------------------------------------
# Validador OIDC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_un_token_valido_da_el_usuario_con_su_organizacion_y_sus_roles_de_la_app():
    p = await _validador().validar(_token())
    assert (p.sub, p.org_id, p.rol, p.nombre, p.via) == ("u-ana", "acme", "analyst", "Ana Analista", "oidc")
    assert p.roles == frozenset({"analyst"})  # los roles ajenos a la app (offline_access) no cuentan
    assert "token" not in repr(p)  # el token viaja con el principal, pero nunca se imprime


@pytest.mark.asyncio
@pytest.mark.parametrize("token, motivo", [
    (lambda: _token(iss="https://otro.test/realms/geo"), "otro emisor"),
    (lambda: _token(aud="otra-app"), "otra audiencia"),
    (lambda: _token(exp=int(time.time()) - 120), "caducado"),
    (lambda: _token(clave=_OTRA), "firmado por otra clave"),
    (lambda: _token(sub=None), "sin sub"),
    (lambda: jwt.encode({"iss": ISSUER, "aud": AUD, "sub": "x", "org": "acme", "iat": int(time.time()),
                         "exp": int(time.time()) + 300}, "secreto-compartido", algorithm="HS256"), "HS256"),
    (lambda: jwt.encode({"iss": ISSUER, "aud": AUD, "sub": "x", "org": "acme", "iat": int(time.time()),
                         "exp": int(time.time()) + 300}, None, algorithm="none"), "alg none"),
    (lambda: "no-es-un-jwt", "basura"),
])
async def test_tokens_que_no_valen(token, motivo):
    with pytest.raises(TokenInvalido):
        await _validador().validar(token())


@pytest.mark.asyncio
async def test_sin_organizacion_es_un_error_propio():
    with pytest.raises(SinOrganizacion):
        await _validador().validar(_token(org=None))
    # un atributo multivaluado con UNA organización vale; con varias no se elige a ciegas
    assert (await _validador().validar(_token(org=["acme"]))).org_id == "acme"
    with pytest.raises(SinOrganizacion):
        await _validador().validar(_token(org=["acme", "beta"]))


@pytest.mark.asyncio
async def test_roles_y_organizacion_en_claims_configurables():
    v = ValidadorOIDC(issuer=ISSUER, audience=AUD, jwks_client=_Jwks(), org_claim="tenant", roles_claim="geo.roles")
    p = await v.validar(_token(tenant="beta", geo={"roles": ["admin"]}))
    assert (p.org_id, p.rol) == ("beta", "admin")


def test_jerarquia_de_roles():
    visor = Principal(sub="v", org_id="o", roles=frozenset({"viewer"}))
    admin = Principal(sub="a", org_id="o", roles=frozenset({"viewer", "admin"}))
    assert visor.puede("viewer") and not visor.puede("analyst")
    assert admin.rol == "admin" and admin.puede("analyst")
    assert Principal(sub="n", org_id="o").rol is None


# ---------------------------------------------------------------------------
# Propiedad de las sesiones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_una_sesion_libre_queda_de_quien_la_usa_y_para_otro_no_existe():
    prop = PropiedadEnMemoria()
    ana = Principal(sub="ana", org_id="acme", roles=frozenset({"analyst"}))
    beto = Principal(sub="beto", org_id="acme", roles=frozenset({"admin"}))
    await prop.asegurar(ana, "s1")
    await prop.asegurar(ana, "s1")  # su dueña, las veces que quiera
    with pytest.raises(SesionAjena):  # ni siendo admin de la misma organización
        await prop.asegurar(beto, "s1")
    assert await prop.dueno("s1") == ("ana", "acme")


# ---------------------------------------------------------------------------
# La API con dos usuarios (E6.2 a nivel de rutas)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_dos_usuarios(monkeypatch):
    """La app con OIDC (validador con la clave de prueba) y la propiedad en memoria."""
    from fastapi.testclient import TestClient

    from geo_copilot.api import app as app_module
    from geo_copilot.core.config import get_settings
    from geo_copilot.platform.identidad import servicio
    from geo_copilot.platform.identidad.tickets import TicketsEnMemoria

    cfg = get_settings().model_copy(update={"oidc_issuer": ISSUER, "oidc_audience": AUD, "api_key": None})
    monkeypatch.setattr(app_module, "get_settings", lambda: cfg)
    monkeypatch.setattr("geo_copilot.api.websocket.get_settings", lambda: cfg)
    fastapi_app = app_module.create_app()
    fastapi_app.dependency_overrides[get_settings] = lambda: cfg
    with TestClient(fastapi_app) as c:
        # después del arranque: el lifespan instala la identidad de la configuración real
        servicio.instalar(servicio.Identidad(validador=_validador(), tickets=TicketsEnMemoria(),
                                             propiedad=PropiedadEnMemoria()))
        yield c
    servicio.instalar(None)


def _h(**claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(**claims)}"}


def test_sin_token_401_y_con_token_quien_soy(api_dos_usuarios):
    c = api_dos_usuarios
    assert c.get("/api/v1/auth/config").json() == {"modo": "oidc", "issuer": ISSUER, "client_id": "geo-copilot-web"}
    assert c.get("/api/v1/session/abc/history").status_code == 401
    assert c.get("/api/v1/auth/yo").status_code == 401
    yo = c.get("/api/v1/auth/yo", headers=_h()).json()
    assert yo == {"sub": "u-ana", "org_id": "acme", "rol": "analyst", "nombre": "Ana Analista", "via": "oidc"}


def test_un_usuario_sin_rol_de_la_app_recibe_403_con_el_motivo(api_dos_usuarios):
    r = api_dos_usuarios.get("/api/v1/auth/yo", headers=_h(realm_access={"roles": ["offline_access"]}))
    assert r.status_code == 403 and "rol" in r.json()["detail"]


def test_la_sesion_de_otro_usuario_no_existe_para_el(api_dos_usuarios):
    c = api_dos_usuarios
    ana, beto = _h(), _h(sub="u-beto", name="Beto")
    sid = c.post("/api/v1/session/", json={}, headers=ana).json()["session_id"]
    assert c.get(f"/api/v1/session/{sid}", headers=ana).status_code == 200
    for metodo, ruta in [("get", f"/api/v1/session/{sid}"), ("get", f"/api/v1/session/{sid}/history"),
                         ("post", f"/api/v1/session/{sid}/reset"), ("delete", f"/api/v1/session/{sid}"),
                         ("post", f"/api/v1/session/{sid}/ws-ticket"),
                         ("get", f"/api/v1/approval/pending?session_id={sid}")]:
        r = getattr(c, metodo)(ruta, headers=beto)
        assert r.status_code == 404, (ruta, r.status_code)
    # tampoco puede «crear» una sesión con el id de Ana
    assert c.post("/api/v1/session/", json={"session_id": sid}, headers=beto).status_code == 404
    # y Ana sigue teniendo la suya
    assert c.get(f"/api/v1/session/{sid}", headers=ana).status_code == 200


def test_el_ticket_del_ws_solo_se_emite_para_la_sesion_propia(api_dos_usuarios):
    c = api_dos_usuarios
    ana = _h()
    sid = c.post("/api/v1/session/", json={}, headers=ana).json()["session_id"]
    r = c.post(f"/api/v1/session/{sid}/ws-ticket", headers=ana)
    assert r.status_code == 200 and len(r.json()["ticket"]) >= 32 and r.json()["expira_en_s"] == 30


def test_el_ws_con_ticket_abre_y_sin_ticket_o_de_otro_no(api_dos_usuarios):
    from starlette.websockets import WebSocketDisconnect

    c = api_dos_usuarios
    ana, beto = _h(), _h(sub="u-beto")
    sid = c.post("/api/v1/session/", json={}, headers=ana).json()["session_id"]
    ticket = c.post(f"/api/v1/session/{sid}/ws-ticket", headers=ana).json()["ticket"]
    with c.websocket_connect(f"/ws/{sid}?ticket={ticket}") as ws:
        assert ws.receive_json()["data"]["status"] == "connected"
    # el mismo ticket otra vez: rechazado antes de aceptar
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect(f"/ws/{sid}?ticket={ticket}") as ws:
            ws.receive_json()
    assert exc.value.code == 4403
    # Beto no consigue ticket para la sesión de Ana (404) ni entra sin él
    assert c.post(f"/api/v1/session/{sid}/ws-ticket", headers=beto).status_code == 404
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect(f"/ws/{sid}") as ws:
            ws.receive_json()
    assert exc.value.code == 4403
