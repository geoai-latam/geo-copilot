"""Tokens OIDC de USUARIOS en un servidor GeoMCP (F6, S6.3: propagación de identidad).

Además de sus API keys de servicio, un servidor puede aceptar el token de acceso de una
persona que la plataforma obtuvo para él por token exchange (RFC 8693): así sabe QUIÉN llama
(sub, organización) y le da los scopes de sus roles, no los de una clave compartida.

Se activa por entorno con un prefijo (`HELLO_GEO_JWT_ISSUER`, `…_AUDIENCE`, `…_JWKS`,
`…_ROLES` = JSON rol → scopes). Sin `…_ISSUER`, el servidor solo acepta sus API keys.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from typing import Any

from geo_mcp_kit.auth import ApiKey

_ALGORITMOS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]

#: Quién hace la llamada en curso (la clave de servicio o el usuario del token).
_llamante: ContextVar[ApiKey | None] = ContextVar("geo_mcp_llamante", default=None)


def llamante() -> ApiKey | None:
    """La identidad de la llamada en curso: una API key de servicio o un usuario (`sub`, `org`)."""
    return _llamante.get()


def fijar_llamante(key: ApiKey | None) -> Any:
    return _llamante.set(key)


def _claim(datos: Mapping[str, Any], ruta: str) -> Any:
    valor: Any = datos
    for parte in ruta.split("."):
        if not isinstance(valor, Mapping):
            return None
        valor = valor.get(parte)
    return valor


class VerificadorJwt:
    def __init__(self, *, issuer: str, audience: str, jwks_url: str, roles_a_scopes: Mapping[str, Iterable[str]],
                 tool_scopes: Mapping[str, str], known_scopes: Iterable[str] = (),
                 roles_claim: str = "realm_access.roles", org_claim: str = "org",
                 rate_limit_per_min: int = 60, jwks_client: Any = None) -> None:
        import jwt

        conocidos = frozenset(known_scopes)
        mapa = {r: frozenset(s) for r, s in roles_a_scopes.items()}
        fuera = {s for ss in mapa.values() for s in ss} - conocidos if conocidos else set()
        if fuera:
            raise ValueError(f"roles→scopes usa scopes no declarados: {sorted(fuera)}")
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self._roles = mapa
        self._tool_scopes = dict(tool_scopes)
        self._roles_claim = roles_claim
        self._org_claim = org_claim
        self._limite = rate_limit_per_min
        self._jwt = jwt
        self._jwks = jwks_client or jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=10)

    async def verificar(self, bearer: str | None) -> ApiKey | None:
        """La identidad del token (con los scopes de sus roles), o None si no vale."""
        if not bearer or bearer.count(".") != 2:
            return None
        try:
            clave = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, bearer)
            datos = self._jwt.decode(bearer, clave.key, algorithms=_ALGORITMOS, audience=self.audience,
                                     issuer=self.issuer, leeway=30,
                                     options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except Exception:  # noqa: BLE001 — cualquier fallo del token = no autenticado (401)
            return None
        roles = _claim(datos, self._roles_claim) or []
        scopes = frozenset(s for r in roles if isinstance(r, str) for s in self._roles.get(r, ()))
        org = _claim(datos, self._org_claim)
        return ApiKey(name=f"usuario:{datos.get('preferred_username') or datos['sub']}", key_hash="",
                      scopes=scopes, rate_limit_per_min=self._limite, tool_scopes=self._tool_scopes,
                      sub=str(datos["sub"]), org=org if isinstance(org, str) else None)

    @classmethod
    def desde_entorno(cls, prefijo: str, *, tool_scopes: Mapping[str, str], known_scopes: Iterable[str],
                      entorno: Mapping[str, str] | None = None) -> VerificadorJwt | None:
        env = entorno if entorno is not None else os.environ
        issuer = (env.get(f"{prefijo}_JWT_ISSUER") or "").strip()
        if not issuer:
            return None
        return cls(issuer=issuer, audience=env[f"{prefijo}_JWT_AUDIENCE"],
                   jwks_url=env.get(f"{prefijo}_JWT_JWKS") or f"{issuer.rstrip('/')}/protocol/openid-connect/certs",
                   roles_a_scopes=json.loads(env.get(f"{prefijo}_JWT_ROLES") or "{}"),
                   tool_scopes=tool_scopes, known_scopes=known_scopes)
