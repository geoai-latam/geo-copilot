"""Validación de tokens OIDC (F6, S6.1).

El token lo emite el proveedor (Keycloak en desarrollo; Entra/Google/… en producción) y la API
solo lo VERIFICA: firma con las claves públicas del emisor (JWKS), `iss`, `aud`, vigencia. Solo
algoritmos asimétricos: un token `none` o firmado con HS256 (clave compartida) se rechaza.

La organización y los roles salen de claims configurables (`OIDC_ORG_CLAIM`,
`OIDC_ROLES_CLAIM`): son hechos del proveedor de identidad, no se deducen de nada.

Las claves se piden a `OIDC_JWKS_URL` o, sin ella, a la `jwks_uri` del documento de
descubrimiento del emisor (F7, `descubrimiento.py`): ninguna ruta propia de un proveedor.
"""

from __future__ import annotations

import asyncio
from typing import Any

import jwt

from geo_copilot.platform.identidad.descubrimiento import ProveedorNoDisponible, endpoint
from geo_copilot.platform.identidad.principal import ROLES, Principal

__all__ = ["ALGORITMOS", "ProveedorNoDisponible", "SinOrganizacion", "TokenInvalido", "ValidadorOIDC"]

ALGORITMOS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]


class TokenInvalido(Exception):
    """El token no vale (firma, emisor, audiencia, vigencia o formato). Mensaje genérico al cliente."""


class SinOrganizacion(Exception):
    """Token válido de un usuario al que el proveedor no le asignó organización."""


def _claim(datos: dict[str, Any], ruta: str) -> Any:
    valor: Any = datos
    for parte in ruta.split("."):
        if not isinstance(valor, dict):
            return None
        valor = valor.get(parte)
    return valor


class ValidadorOIDC:
    def __init__(self, *, issuer: str, audience: str, jwks_url: str | None = None,
                 org_claim: str = "org", roles_claim: str = "realm_access.roles", leeway_s: int = 30,
                 jwks_client: Any = None) -> None:
        self.issuer = issuer
        # F7 (auditoría): el `iss` se compara tal cual, con o sin la barra final. Antes se quitaba
        # siempre, y Entra v1.0 (`https://sts.windows.net/<tenant>/`) no casaba nunca.
        self._emisores = frozenset({issuer, issuer.rstrip("/"), issuer.rstrip("/") + "/"})
        self.audience = audience
        self.org_claim = org_claim
        self.roles_claim = roles_claim
        self.leeway_s = leeway_s
        self._jwks_url = jwks_url or None
        self._jwks = jwks_client
        if self._jwks is None and self._jwks_url:
            self._jwks = self._cliente_jwks(self._jwks_url)

    @staticmethod
    def _cliente_jwks(url: str) -> Any:
        # Las claves se guardan en caché; una clave nueva (rotación) se pide al ver un `kid` desconocido
        return jwt.PyJWKClient(url, cache_keys=True, lifespan=300, timeout=10)

    async def _claves(self) -> Any:
        """El cliente JWKS; sin OIDC_JWKS_URL, con la `jwks_uri` que publica el emisor.

        Si el proveedor no la da, ProveedorNoDisponible (no es culpa del token: no es un 401).
        """
        if self._jwks is None:
            self._jwks_url = await endpoint(self.issuer, "jwks_uri")
            self._jwks = self._cliente_jwks(self._jwks_url)
        return self._jwks

    def _emisor(self, token: str) -> str:
        """El emisor que PyJWT debe exigir, siempre un str: el `iss` del token si es una de las formas
        aceptadas (con o sin barra), si no el configurado (y PyJWT lo rechaza). PyJWT 2.8–2.10 no
        acepta un conjunto en `issuer`: con un frozenset rechazaban TODOS los tokens."""
        iss = jwt.decode(token, options={"verify_signature": False}).get("iss")
        return iss if isinstance(iss, str) and iss in self._emisores else self.issuer

    async def validar(self, token: str) -> Principal:
        jwks = await self._claves()
        try:
            # PyJWKClient pide las claves con urllib (bloqueante) cuando no las tiene en caché
            clave = await asyncio.to_thread(jwks.get_signing_key_from_jwt, token)
            datos = jwt.decode(
                token, clave.key, algorithms=ALGORITMOS, audience=self.audience, issuer=self._emisor(token),
                leeway=self.leeway_s, options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            raise TokenInvalido(type(exc).__name__) from exc
        org = _claim(datos, self.org_claim)
        if isinstance(org, list):  # atributo multivaluado del proveedor: uno solo
            org = org[0] if len(org) == 1 else None
        if not isinstance(org, str) or not org.strip():
            raise SinOrganizacion(str(datos.get("sub")))
        roles = _claim(datos, self.roles_claim) or []
        return Principal(
            sub=str(datos["sub"]), org_id=org.strip(),
            roles=frozenset(r for r in roles if isinstance(r, str) and r in ROLES),
            nombre=str(datos.get("name") or datos.get("preferred_username") or datos.get("email") or ""),
            via="oidc", token=token,
        )
