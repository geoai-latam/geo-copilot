"""Descubrimiento OIDC: los endpoints del proveedor, leídos de su propio documento (F7).

F7 (auditoría): sin `OIDC_JWKS_URL` / `OIDC_TOKEN_URL` se derivaban con la ruta de Keycloak
(`/protocol/openid-connect/...`), que no existe en Entra ID ni en Google: toda petición con
Bearer daba 401 sin decir por qué. Ahora, cuando no se dan explícitas, se leen de
`{issuer}/.well-known/openid-configuration` (`jwks_uri`, `token_endpoint`), como manda OpenID
Connect Discovery 1.0. Si no se puede leer, se dice qué falló; no se adivina ninguna ruta.

Las URL explícitas siguen mandando: en Docker son las INTERNAS del proveedor
(`OIDC_*_URL_INTERNA` en docker-compose.yml), porque la URL pública del emisor puede no ser
alcanzable desde el contenedor.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RUTA = "/.well-known/openid-configuration"
TIMEOUT_S = 5.0
VIDA_S = 3600.0  # el documento cambia rara vez; las claves rotan aparte (PyJWKClient)
VIDA_FALLO_S = 30.0  # con el proveedor caído, no esperar el timeout en cada petición

_cache: dict[str, tuple[float, dict[str, Any] | str]] = {}


class ProveedorNoDisponible(Exception):
    """No se pudo leer del proveedor de identidad un endpoint que hace falta."""


def url_documento(issuer: str) -> str:
    return f"{issuer.rstrip('/')}{RUTA}"


async def documento(issuer: str, *, http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """El documento de descubrimiento del emisor (de la caché si sigue vigente)."""
    clave = issuer.rstrip("/")
    en_cache = _cache.get(clave)
    if en_cache and en_cache[0] > time.monotonic():
        if isinstance(en_cache[1], str):
            raise ProveedorNoDisponible(en_cache[1])
        return en_cache[1]
    try:
        doc = await _leer(issuer, http)
    except ProveedorNoDisponible as exc:
        logger.error("[identidad] %s", exc)
        _cache[clave] = (time.monotonic() + VIDA_FALLO_S, str(exc))
        raise
    _cache[clave] = (time.monotonic() + VIDA_S, doc)
    return doc


async def _leer(issuer: str, http: httpx.AsyncClient | None) -> dict[str, Any]:
    url = url_documento(issuer)
    propio = http is None
    cliente = http or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True)
    try:
        r = await cliente.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise ProveedorNoDisponible(
            f"descubrimiento OIDC: {url} no respondió ({type(exc).__name__}). Revisa OIDC_ISSUER o "
            "define OIDC_JWKS_URL / OIDC_TOKEN_URL") from exc
    finally:
        if propio:
            await cliente.aclose()
    if r.status_code != 200:
        raise ProveedorNoDisponible(f"descubrimiento OIDC: {url} respondió {r.status_code}. Revisa OIDC_ISSUER "
                                    "o define OIDC_JWKS_URL / OIDC_TOKEN_URL")
    try:
        doc = r.json()
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        raise ProveedorNoDisponible(f"descubrimiento OIDC: {url} no devolvió un objeto JSON")
    # OIDC Discovery §4.3: el `issuer` del documento es el mismo con el que se pidió. Si no, el
    # OIDC_ISSUER no es el `iss` de los tokens (p. ej. Entra v1.0 frente a v2.0) y todo daría 401.
    declarado = str(doc.get("issuer") or "")
    if declarado.rstrip("/") != issuer.rstrip("/"):
        raise ProveedorNoDisponible(
            f"descubrimiento OIDC: {url} declara el emisor «{declarado}», no «{issuer}». OIDC_ISSUER "
            "tiene que ser exactamente el `iss` de los tokens")
    return doc


async def endpoint(issuer: str, campo: str, *, http: httpx.AsyncClient | None = None) -> str:
    """Un endpoint del documento (`jwks_uri`, `token_endpoint`…) o ProveedorNoDisponible."""
    valor = (await documento(issuer, http=http)).get(campo)
    if not isinstance(valor, str) or not valor.strip():
        motivo = f"descubrimiento OIDC: el documento de {issuer} no trae «{campo}»"
        logger.error("[identidad] %s", motivo)
        raise ProveedorNoDisponible(motivo)
    return valor.strip()
