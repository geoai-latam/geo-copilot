"""Tests de autenticación (Fase 2 / S1; F6 / S6.1).

Cubre:
- ``require_principal``: el principal ``dev`` sin autenticación configurada, 401 sin/mal key,
  FAIL-CLOSED cuando la key está configurada pero vacía (antes se saltaba la auth en
  silencio) y el cliente de servicio con su organización y su rol.
- ``autenticar_websocket``: origin + ticket/key.
- ``enforce_production_auth``: el arranque debe rechazar correr en producción sin OIDC ni
  api_key.

El validador OIDC, los tickets y la propiedad de las sesiones: tests/test_identidad.py.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from geo_copilot.api.auth import (
    autenticar_websocket,
    enforce_production_auth,
    enforce_production_debug,
    require_principal,
)
from geo_copilot.platform.identidad import servicio
from geo_copilot.platform.identidad.principal import PRINCIPAL_DEV


def _settings(
    api_key=None, debug=False, cors_allow_all=True, cors_origins=None, environment="development",
    oidc_issuer=None, api_key_role="admin", api_key_org="acme",
):
    return SimpleNamespace(
        api_key=api_key,
        debug=debug,
        cors_allow_all=cors_allow_all,
        cors_origins=cors_origins or [],
        environment=environment,
        oidc_issuer=oidc_issuer,
        api_key_role=api_key_role,
        api_key_org=api_key_org,
    )


@pytest.fixture(autouse=True)
def _identidad_limpia():
    """Cada test con su identidad (sin validador OIDC, tickets y propiedad en memoria)."""
    servicio.instalar(servicio.construir(SimpleNamespace()))
    yield
    servicio.instalar(None)


class TestRequirePrincipal:
    @pytest.mark.asyncio
    async def test_sin_autenticacion_configurada_es_el_principal_dev(self):
        p = await require_principal(authorization=None, x_api_key=None, settings=_settings(api_key=None))
        assert p is PRINCIPAL_DEV and p.rol == "admin"

    @pytest.mark.asyncio
    async def test_missing_header_raises(self):
        with pytest.raises(HTTPException) as exc:
            await require_principal(authorization=None, x_api_key=None, settings=_settings(api_key=SecretStr("secret")))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_raises(self):
        with pytest.raises(HTTPException) as exc:
            await require_principal(authorization=None, x_api_key="nope",
                                    settings=_settings(api_key=SecretStr("secret")))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_la_key_correcta_es_un_cliente_de_servicio_con_su_org_y_su_rol(self):
        p = await require_principal(authorization=None, x_api_key="secret",
                                    settings=_settings(api_key=SecretStr("secret"), api_key_role="analyst"))
        assert (p.sub, p.org_id, p.rol, p.via) == ("servicio:api-key", "acme", "analyst", "api_key")

    @pytest.mark.asyncio
    async def test_empty_key_is_fail_closed(self):
        """S1: api_key='' (mal configurada) NO debe saltar la auth."""
        with pytest.raises(HTTPException) as exc:
            await require_principal(authorization=None, x_api_key="", settings=_settings(api_key=SecretStr("")))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_con_oidc_configurado_sin_credenciales_no_hay_principal_dev(self):
        with pytest.raises(HTTPException) as exc:
            await require_principal(authorization=None, x_api_key=None,
                                    settings=_settings(oidc_issuer="https://idp.test/realms/geo"))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_un_rol_desconocido_en_la_key_deja_sin_rol_403(self):
        with pytest.raises(HTTPException) as exc:
            await require_principal(authorization=None, x_api_key="k",
                                    settings=_settings(api_key=SecretStr("k"), api_key_role="root"))
        assert exc.value.status_code == 403 and "rol" in exc.value.detail


class TestEnforceProductionAuth:
    # RUN (auditoría): la señal de "prod" ahora es ``environment`` (alineada con
    # enforce_production_debug), no solo ``debug``.
    def test_prod_without_key_raises(self):
        with pytest.raises(RuntimeError):
            enforce_production_auth(_settings(api_key=None, debug=False, environment="production"))

    def test_prod_with_empty_key_raises(self):
        with pytest.raises(RuntimeError):
            enforce_production_auth(_settings(api_key=SecretStr(""), debug=False, environment="production"))

    def test_prod_with_key_ok(self):
        enforce_production_auth(_settings(api_key=SecretStr("secret"), debug=False, environment="production"))

    def test_prod_con_oidc_sin_key_ok(self):
        # F6: las personas entran por OIDC; la API key pasa a ser opcional (solo servicio)
        enforce_production_auth(_settings(api_key=None, debug=False, environment="production",
                                          oidc_issuer="https://idp.test/realms/geo"))

    def test_debug_without_key_ok(self):
        # En dev (debug=True) se permite correr sin key.
        enforce_production_auth(_settings(api_key=None, debug=True))

    def test_dev_environment_without_key_ok(self):
        # RUN: DEBUG=false + ENVIRONMENT=development (el .env.example) arranca sin
        # key — antes abortaba (incoherente con enforce_production_debug).
        enforce_production_auth(_settings(api_key=None, debug=False, environment="development"))


class TestEnforceProductionDebug:
    def test_prod_con_debug_true_rechaza(self):
        with pytest.raises(RuntimeError):
            enforce_production_debug(_settings(debug=True, environment="production"))

    def test_prod_con_debug_false_ok(self):
        enforce_production_debug(_settings(debug=False, environment="production"))

    def test_dev_con_debug_true_ok(self):
        enforce_production_debug(_settings(debug=True, environment="development"))

    def test_staging_con_debug_true_rechaza(self):
        # Cualquier entorno no-dev cuenta como producción para este guard.
        with pytest.raises(RuntimeError):
            enforce_production_debug(_settings(debug=True, environment="staging"))

    def test_environment_case_insensitive(self):
        with pytest.raises(RuntimeError):
            enforce_production_debug(_settings(debug=True, environment="Production"))


class TestWebSocketAuth:
    @pytest.mark.asyncio
    async def test_no_key_no_origin_passes(self):
        ws = SimpleNamespace(headers={}, query_params={})
        assert await autenticar_websocket(ws, _settings(api_key=None), "s1") is PRINCIPAL_DEV

    @pytest.mark.asyncio
    async def test_token_required_when_key_set(self):
        ws = SimpleNamespace(headers={}, query_params={})
        assert await autenticar_websocket(ws, _settings(api_key=SecretStr("k")), "s1") is None

    @pytest.mark.asyncio
    async def test_correct_token_passes(self):
        ws = SimpleNamespace(headers={}, query_params={"token": "k"})
        p = await autenticar_websocket(ws, _settings(api_key=SecretStr("k")), "s1")
        assert p is not None and p.via == "api_key"

    @pytest.mark.asyncio
    async def test_empty_key_fail_closed(self):
        ws = SimpleNamespace(headers={}, query_params={"token": ""})
        assert await autenticar_websocket(ws, _settings(api_key=SecretStr("")), "s1") is None

    @pytest.mark.asyncio
    async def test_origin_not_allowlisted_rejected(self):
        ws = SimpleNamespace(headers={"origin": "https://evil.com"}, query_params={})
        ok = await autenticar_websocket(
            ws, _settings(api_key=None, cors_allow_all=False, cors_origins=["https://ok.com"]), "s1")
        assert ok is None

    @pytest.mark.asyncio
    async def test_un_ticket_abre_solo_su_sesion_y_una_sola_vez(self):
        """F6: con OIDC el navegador abre el socket con un ticket de un solo uso de ESA sesión."""
        from geo_copilot.platform.identidad.principal import Principal

        ana = Principal(sub="ana", org_id="acme", roles=frozenset({"analyst"}))
        cfg = _settings(oidc_issuer="https://idp.test/realms/geo")
        tickets = servicio.identidad_actual().tickets
        t = await tickets.emitir(ana, "s1", 30)
        # para otra sesión no vale (y queda consumido)
        assert await autenticar_websocket(SimpleNamespace(headers={}, query_params={"ticket": t}), cfg, "s2") is None
        t = await tickets.emitir(ana, "s1", 30)
        p = await autenticar_websocket(SimpleNamespace(headers={}, query_params={"ticket": t}), cfg, "s1")
        assert p is not None and p.sub == "ana" and p.org_id == "acme"
        # un ticket ya usado (p. ej. copiado de un log) no vuelve a abrir nada
        assert await autenticar_websocket(SimpleNamespace(headers={}, query_params={"ticket": t}), cfg, "s1") is None
        # sin ticket, con OIDC no hay principal dev
        assert await autenticar_websocket(SimpleNamespace(headers={}, query_params={}), cfg, "s1") is None


class TestIsProductionEnvironment:
    """R0.12 (auditoría 2026-07-26, AUD-110).

    El predicado de "esto es producción" estaba duplicado literalmente en los
    dos guards de arranque. Se extrajo a `is_production_environment` para que
    todo consumidor nuevo —empezando por el gating de /docs— use la MISMA
    definición en vez de inventarse una tercera.
    """

    @pytest.mark.parametrize("env", ["development", "dev", "local", "test", "testing"])
    def test_entornos_de_desarrollo_no_son_produccion(self, env):
        from geo_copilot.api.auth import is_production_environment

        assert is_production_environment(_settings(environment=env)) is False

    @pytest.mark.parametrize("env", ["production", "prod", "staging", "PRODUCTION"])
    def test_cualquier_otro_entorno_es_produccion(self, env):
        from geo_copilot.api.auth import is_production_environment

        assert is_production_environment(_settings(environment=env)) is True

    def test_el_default_de_environment_es_el_seguro(self):
        """Auditoría 2026-09-08 (§3, §7 punto 8): omitir ENVIRONMENT no puede
        abrir la API.

        Este campo gobierna cuatro controles a la vez (API key obligatoria,
        /docs apagado, sandbox 'subprocess' prohibido, token del WebSocket).
        Su default era "development", así que el sistema entero se degradaba
        por una variable olvidada. Se comprueba el default DECLARADO en el
        modelo y no ``get_settings().environment``, porque el ``.env`` de la
        máquina y el ``ENVIRONMENT`` que fija ``tests/conftest.py`` pisan el
        valor efectivo — y es justamente el caso sin ninguno de los dos el que
        hay que blindar.
        """
        from geo_copilot.api.auth import _NON_PROD_ENVIRONMENTS
        from geo_copilot.core.config import Settings

        default = Settings.model_fields["environment"].default
        assert default == "production"
        assert default not in _NON_PROD_ENVIRONMENTS

    @pytest.mark.parametrize(
        "env,esperado_apagado",
        [("production", True), ("staging", True), ("development", False)],
    )
    def test_la_documentacion_interactiva_se_apaga_en_produccion(self, env, esperado_apagado):
        """/docs, /redoc y /openapi.json publicaban el inventario completo de
        endpoints y esquemas —incluidos los de approval/HITL— a cualquiera que
        alcanzase el puerto, sin credencial.

        Se construye la app REAL y se mira el atributo, en vez de reafirmar el
        predicado: lo que importa es que el factory lo consuma de verdad.
        """
        from unittest.mock import patch

        from geo_copilot.api import app as app_module
        from geo_copilot.core.config import get_settings

        cfg = get_settings().model_copy(update={"environment": env, "debug": False})
        with patch.object(app_module, "get_settings", return_value=cfg):
            fastapi_app = app_module.create_app()

        if esperado_apagado:
            assert fastapi_app.docs_url is None
            assert fastapi_app.redoc_url is None
            assert fastapi_app.openapi_url is None
        else:
            assert fastapi_app.docs_url == "/docs"
            assert fastapi_app.openapi_url == "/openapi.json"
