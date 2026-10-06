"""Token exchange (RFC 8693) hacia los servidores MCP que lo declaran (F6, S6.3).

Para un servidor con `auth: {type: token_exchange, audience: X}`, cada llamada que hace un
USUARIO no va con la clave de servicio sino con un token de ese usuario para la audiencia X,
que la API pide al proveedor de identidad cambiando el token con el que el usuario entró. El
servidor sabe así quién llama (sub, organización) y le aplica los scopes de sus roles.

Los tokens obtenidos se guardan en memoria hasta poco antes de caducar (por usuario y
audiencia). Nunca se registran.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from geo_copilot.platform.identidad.descubrimiento import ProveedorNoDisponible, endpoint
from geo_copilot.platform.identidad.principal import principal_actual

_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_ACCESS = "urn:ietf:params:oauth:token-type:access_token"
_MARGEN_S = 30
_cache: dict[tuple[str, str], tuple[float, str]] = {}


class IntercambioFallido(Exception):
    """El proveedor no dio un token del usuario para ese servidor."""


async def _url_token(settings: Any, http: httpx.AsyncClient | None) -> str:
    """OIDC_TOKEN_URL o, sin ella, el `token_endpoint` que publica el emisor (F7, descubrimiento)."""
    if getattr(settings, "oidc_token_url", None):
        return str(settings.oidc_token_url)
    if not getattr(settings, "oidc_issuer", None):
        raise IntercambioFallido("no hay proveedor de identidad configurado (OIDC_ISSUER)")
    try:
        return await endpoint(str(settings.oidc_issuer), "token_endpoint", http=http)
    except ProveedorNoDisponible as exc:
        raise IntercambioFallido(str(exc)) from exc


async def intercambiar(token_usuario: str, audiencia: str, *, settings: Any = None,
                       http: httpx.AsyncClient | None = None) -> str:
    """Un token del usuario para `audiencia` (de la caché si sigue vigente)."""
    if settings is None:
        from geo_copilot.core.config import get_settings

        settings = get_settings()
    clave = (hashlib.sha256(token_usuario.encode()).hexdigest(), audiencia)
    en_cache = _cache.get(clave)
    if en_cache and en_cache[0] > time.monotonic():
        return en_cache[1]
    secreto = settings.oidc_api_client_secret.get_secret_value() if settings.oidc_api_client_secret else ""
    if not secreto:
        raise IntercambioFallido("la API no tiene credencial en el proveedor (OIDC_API_CLIENT_SECRET)")
    datos = {"grant_type": _GRANT, "subject_token": token_usuario, "subject_token_type": _ACCESS,
             "requested_token_type": _ACCESS, "audience": audiencia}
    propio = http is None
    cliente = http or httpx.AsyncClient(timeout=10)
    try:
        url = await _url_token(settings, http)
        r = await cliente.post(url, data=datos, auth=(settings.oidc_api_client_id, secreto))
    except httpx.HTTPError as exc:
        raise IntercambioFallido(f"el proveedor de identidad no respondió ({type(exc).__name__})") from exc
    finally:
        if propio:
            await cliente.aclose()
    if r.status_code != 200:
        motivo = ""
        try:
            motivo = str(r.json().get("error_description") or r.json().get("error") or "")[:200]
        except ValueError:
            pass
        raise IntercambioFallido(f"el proveedor rechazó el intercambio para «{audiencia}» ({r.status_code}"
                                 f"{': ' + motivo if motivo else ''})")
    cuerpo = r.json()
    token = str(cuerpo["access_token"])
    vida = float(cuerpo.get("expires_in") or 60)
    if len(_cache) > 10_000:
        _cache.clear()
    _cache[clave] = (time.monotonic() + max(0.0, vida - _MARGEN_S), token)
    return token


async def token_para(cfg: Any) -> str:
    """Bearer para el servidor de `cfg`: el del usuario de la petición o, sin usuario, el de servicio."""
    from geo_copilot.platform.mcp.connection import ServidorNoDisponible

    p = principal_actual()
    audiencia = cfg.auth.audience or cfg.id
    if p is not None and p.via == "oidc":
        # una PERSONA: con su token o nada. Caer a la clave de servicio firmaría sus acciones
        # como si las hiciera el sistema.
        if not p.token:
            raise ServidorNoDisponible(f"'{cfg.id}' atiende con la identidad del usuario y este canal no la trae")
        try:
            return await intercambiar(p.token, audiencia)
        except IntercambioFallido as exc:
            raise ServidorNoDisponible(f"sin token del usuario para '{cfg.id}': {exc}") from exc
    secreto = cfg.auth.resolver()
    if not secreto:
        raise ServidorNoDisponible(f"el servidor '{cfg.id}' no tiene credencial de servicio ({cfg.auth.secret_ref})")
    return str(secreto)
