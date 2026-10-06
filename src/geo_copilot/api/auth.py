"""
Autenticación de la API — quién hace cada petición (F6, S6.1).

Tres formas de entrar, en este orden:

1. **Persona por OIDC** (``OIDC_ISSUER`` definido): ``Authorization: Bearer <JWT>`` firmado
   por el emisor; la organización y los roles salen de sus claims.
2. **Cliente de servicio** (``API_KEY`` definida): ``X-API-Key``; su organización y su rol los
   fija la configuración (``API_KEY_ORG``, ``API_KEY_ROLE``).
3. **Desarrollo sin autenticación** (ni OIDC ni API key): el principal ``dev`` (admin). En
   producción el guard de arranque impide llegar aquí.

El WebSocket no lleva cabeceras desde el navegador: se abre con un ticket de un solo uso que
se pide por REST (``POST /session/{id}/ws-ticket``). Todas las rutas dependen de
``require_principal``; lo que tiene dueño lo comprueba después ``asegurar_sesion``.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, status
from starlette.websockets import WebSocket

from geo_copilot.core.config import Settings, get_settings
from geo_copilot.core.logging import get_logger
from geo_copilot.platform.identidad import descubrimiento
from geo_copilot.platform.identidad.oidc import (
    ProveedorNoDisponible,
    SinOrganizacion,
    TokenInvalido,
)
from geo_copilot.platform.identidad.principal import (
    PRINCIPAL_DEV,
    ROLES,
    Principal,
    fijar_principal,
)
from geo_copilot.platform.identidad.propiedad import SesionAjena
from geo_copilot.platform.identidad.servicio import identidad_actual

logger = get_logger(__name__)


def _matches(provided: str, expected: str) -> bool:
    """Constant-time comparison so we don't leak the key length via timing."""
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _configured_key(settings: Settings) -> str | None:
    """Devuelve la API key efectiva, o None si la auth está deshabilitada.

    S1: distinguimos tres estados:
    - ``api_key is None``         → auth deshabilitada (solo dev).
    - ``api_key`` no vacía        → auth activa con esa key.
    - ``api_key`` vacía ("")      → MAL CONFIGURADA. Antes esto saltaba la
      auth en silencio; ahora lo tratamos como "auth requerida pero sin
      key válida" → fail-closed (ninguna petición pasa).
    """
    if settings.api_key is None:
        return None
    return settings.api_key.get_secret_value()


def enforce_production_auth(settings: Settings) -> None:
    """Guard de arranque (D2): rechazar correr en producción sin API key.

    Si ``debug`` es False y no hay una API key válida configurada, el
    proceso NO debe arrancar — de lo contrario toda la superficie
    protegida quedaría abierta sin credenciales. En dev (``debug=True``)
    se permite correr sin key.

    Raises:
        RuntimeError: en producción sin key válida.
    """
    # RUN (auditoría E2E): alinear la señal de "dev" con enforce_production_debug,
    # que decide por ``environment`` (no solo por ``debug``). Antes: con
    # DEBUG=false + environment=development (el .env.example) este guard abortaba
    # el arranque por falta de API_KEY, incoherente con el otro guard y rompiendo
    # el quickstart. Ahora dev/test no exigen key; prod (environment no-dev) sí.
    if getattr(settings, "debug", False) or not is_production_environment(settings):
        return
    # F6: OIDC es una forma válida de autenticar (la API key pasa a ser solo de servicio)
    if getattr(settings, "oidc_issuer", None):
        return
    key = _configured_key(settings)
    if not key:
        raise RuntimeError(
            "Arranque rechazado: fuera de desarrollo hace falta OIDC_ISSUER (personas) o "
            "API_KEY (servicio). Configura uno o pon ENVIRONMENT=development para dev."
        )


# Entornos que NO son producción: se permite debug=True (dev/test).
_NON_PROD_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}


def enforce_sandbox_backend(settings: Settings) -> None:
    """Guard de arranque (R0.5, AUD-03): no ejecutar código del LLM en-proceso
    fuera de desarrollo.

    El filtro AST de ``PythonSandbox.analyze_security`` NO es una barrera: se
    evade por completo con ``json.__builtins__`` (verificado ejecutando el
    runner real, se obtienen ``eval``/``open``/``__import__``/``socket``). Con
    ``sandbox_backend="subprocess"`` el código generado por el LLM corre en el
    MISMO proceso que la API, con ``DATABASE_URL``, las claves del LLM y
    ``DOCKER_HOST`` en el entorno — es decir, RCE directo. La contención real
    es el contenedor ``sandbox`` (network:none, read_only, cap_drop:ALL).

    Raises:
        RuntimeError: en un entorno no-desarrollo con backend 'subprocess'.
    """
    backend = str(getattr(settings, "sandbox_backend", "docker")).strip().lower()
    if backend == "subprocess" and is_production_environment(settings):
        raise RuntimeError(
            "Arranque rechazado: SANDBOX_BACKEND='subprocess' ejecuta el código "
            "generado por el LLM dentro del proceso de la API y su filtro AST es "
            "evadible. Fuera de desarrollo usa SANDBOX_BACKEND='docker'."
        )


def is_production_environment(settings: Settings) -> bool:
    """¿El proceso corre en un entorno de producción?

    R0.12 (auditoría 2026-07-26): definición ÚNICA de "esto es producción".
    Antes el predicado estaba duplicado literalmente en los dos guards de
    arranque, y cualquier consumidor nuevo tendía a inventarse un tercero
    ligeramente distinto. Todo lo que dependa de estar en producción debe
    llamar aquí.
    """
    env = str(getattr(settings, "environment", "development")).strip().lower()
    return env not in _NON_PROD_ENVIRONMENTS


def enforce_production_debug(settings: Settings) -> None:
    """Guard de arranque (SEC-ERROR-LEAK): no correr en producción con debug=True.

    En ``debug=True`` el saneador de errores anexa el detalle crudo (trazas,
    nombres de tablas/columnas PostGIS) a las respuestas — útil en dev, pero una
    filtración en producción. Si ``environment`` NO es de desarrollo y
    ``debug`` es True, el proceso NO debe arrancar.

    Raises:
        RuntimeError: en un entorno de producción con debug activado.
    """
    if getattr(settings, "debug", False) and is_production_environment(settings):
        raise RuntimeError(
            "Arranque rechazado: DEBUG=true con ENVIRONMENT="
            f"'{settings.environment}'. En producción DEBUG debe ser false para "
            "no filtrar trazas ni la estructura de la BD en las respuestas de error."
        )


def _auth_desactivada(settings: Settings) -> bool:
    return settings.api_key is None and not getattr(settings, "oidc_issuer", None)


def _principal_de_servicio(settings: Settings) -> Principal:
    rol = str(getattr(settings, "api_key_role", "admin"))
    return Principal(sub="servicio:api-key", org_id=str(getattr(settings, "api_key_org", "default")),
                     roles=frozenset({rol} & set(ROLES)), nombre="Cliente de servicio", via="api_key")


def _no_autenticado() -> HTTPException:
    # Cuerpo genérico: no se distingue «falta» de «no vale» (enumeración barata)
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado",
                         headers={"WWW-Authenticate": "Bearer"})


async def autenticar(authorization: str | None, x_api_key: str | None, settings: Settings) -> Principal:
    """El principal de unas credenciales, o HTTPException 401/403 (503 si el proveedor de identidad
    no responde)."""
    identidad = identidad_actual()
    if authorization and authorization[:7].lower() == "bearer " and identidad.validador is not None:
        try:
            return await identidad.validador.validar(authorization[7:].strip())
        except SinOrganizacion:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu usuario no tiene una organización asignada en el proveedor de identidad. "
                       "Pide a un administrador que te la asigne.") from None
        except TokenInvalido:
            raise _no_autenticado() from None
        except ProveedorNoDisponible as exc:
            # F7 (auditoría): el proveedor (su descubrimiento) no responde o está mal configurado. No
            # es culpa del token (no es un 401) y no es un fallo nuestro sin manejar (antes, 500 con
            # traza en cada petición): 503, la causa al cliente y una línea de log por petición.
            logger.warning(f"[identidad] petición con Bearer rechazada con 503: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"El proveedor de identidad no está disponible: {exc}",
                headers={"Retry-After": str(int(descubrimiento.VIDA_FALLO_S))}) from None
    expected = _configured_key(settings)
    if x_api_key is not None and expected is not None:
        # S1: key configurada pero vacía → fail-closed
        if expected and _matches(x_api_key, expected):
            return _principal_de_servicio(settings)
        raise _no_autenticado()
    if _auth_desactivada(settings):
        return PRINCIPAL_DEV
    raise _no_autenticado()


async def require_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Dependencia de TODAS las rutas: quién es; sin rol de la aplicación, 403.

    Deja el principal en la contextvar de la petición: el orquestador, el hub MCP y la
    auditoría lo leen de ahí.
    """
    principal = await autenticar(authorization, x_api_key, settings)
    if principal.rol is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu usuario no tiene un rol en GEO Copilot (viewer, analyst o admin). "
                   "Pide a un administrador que te lo asigne.")
    fijar_principal(principal)
    await cargar_conexiones_de(principal)
    return principal


async def cargar_conexiones_de(principal: Principal) -> None:
    """F6 (S6.2): sus conexiones MCP propias, cargadas la primera vez que alguien de su
    organización entra (después, en caché)."""
    from geo_copilot.platform.conexiones.hubs import hubs_actuales

    hubs = hubs_actuales()
    if hubs is not None:
        await hubs.asegurar(principal.org_id)


def requiere_rol(minimo: str):
    """Dependencia: el principal con al menos ese rol (403 con el motivo si no)."""

    async def _dep(principal: Principal = Depends(require_principal)) -> Principal:
        if not principal.puede(minimo):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Esta acción requiere el rol «{minimo}»; tu rol es «{principal.rol}».")
        return principal

    return _dep


async def asegurar_sesion(principal: Principal, session_id: str) -> None:
    """La sesión es de este principal (o queda suya si estaba libre); si es de otro, 404.

    404 y no 403: a quien no es su dueño no se le confirma que la sesión exista.
    """
    try:
        await identidad_actual().propiedad.asegurar(principal, session_id)
    except SesionAjena:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sesión no encontrada") from None


async def autenticar_websocket(websocket: WebSocket, settings: Settings, session_id: str) -> Principal | None:
    """Origin + credenciales del WebSocket. None = rechazar ANTES de aceptarlo.

    Credenciales, en orden: ``?ticket=`` de un solo uso para ESTA sesión (lo que usa el
    navegador), la API key de servicio (``?token=`` o cabecera ``X-API-Key``) o, sin
    autenticación configurada, el principal ``dev``.
    """
    origin = websocket.headers.get("origin")
    if origin is not None and not settings.cors_allow_all and origin not in settings.cors_origins:
        return None
    identidad = identidad_actual()
    ticket = websocket.query_params.get("ticket")
    if ticket:
        return await identidad.tickets.consumir(ticket, session_id)
    token = websocket.query_params.get("token") or websocket.headers.get("x-api-key")
    expected = _configured_key(settings)
    if token is not None and expected is not None:
        return _principal_de_servicio(settings) if expected and _matches(token, expected) else None
    if _auth_desactivada(settings):
        return PRINCIPAL_DEV
    return None


async def asegurar_sesion_actual(session_id: str) -> Principal:
    """`asegurar_sesion` con el principal de la petición en curso (lo fijó `require_principal`)."""
    from geo_copilot.platform.identidad.principal import principal_actual

    principal = principal_actual()
    if principal is None:  # una ruta sin require_principal: no se adivina quién es
        raise _no_autenticado()
    await asegurar_sesion(principal, session_id)
    return principal
