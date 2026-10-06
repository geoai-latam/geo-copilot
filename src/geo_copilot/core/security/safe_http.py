"""Cliente HTTP con protección SSRF por IP-pinning (SEC-01).

Problema (auditoría E2E 2026-07-20): los conectores validaban la URL con
``URLValidator.validate_url`` (resuelve el DNS una vez para chequear IPs
privadas) y luego abrían un ``httpx.AsyncClient`` que RE-resuelve el host al
conectar. Entre ambas resoluciones un DNS atacante de TTL bajo puede pasar de
una IP pública (pasa el chequeo) a ``169.254.169.254`` / ``10.x`` / ``127.0.0.1``
(destino real) — TOCTOU / DNS rebinding.

Solución: ``pinned_client(url)`` resuelve+valida el host UNA vez
(``URLValidator.resolve_validated_ip``) y devuelve un ``httpx.AsyncClient`` cuyo
transporte reescribe cada request al mismo host hacia la **IP fijada**, con
``Host`` + SNI del hostname original (el certificado TLS sigue validando contra
el nombre, no la IP). Como el pin vive en el transporte, cubre TODAS las
peticiones del cliente —incluidas las de paginación y sub-helpers que reusan el
mismo ``client``— sin tocar cada ``client.get``. ``follow_redirects=False`` por
defecto: un redirect a otro host quedaría sin pin y reabriría el SSRF.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from geo_copilot.core.logging import get_logger
from geo_copilot.core.security.url_validator import URLValidator

logger = get_logger(__name__)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """Transporte que fija ``hostname -> ip`` validada para cerrar el rebinding.

    Reescribe el host del request a la IP fijada y conserva ``Host`` + SNI del
    hostname original. Un host que NO esté en el mapa (p.ej. un redirect a otro
    dominio) se deja pasar SIN reescribir — combinado con ``follow_redirects=
    False`` en el cliente, esos casos no ocurren; si alguien fuerza
    ``follow_redirects=True`` el redirect saldría sin pin (documentado).
    """

    def __init__(self, host_to_ip: dict[str, str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._host_to_ip = host_to_ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        pinned_ip = self._host_to_ip.get(original_host)
        if pinned_ip:
            # Host header explícito (el nombre original) + SNI para TLS; la
            # conexión va a la IP fijada.
            request.headers["Host"] = original_host
            request.extensions["sni_hostname"] = original_host
            request.url = request.url.copy_with(host=pinned_ip)
        return await super().handle_async_request(request)


def pinned_client(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    allow_any_port: bool = False,
    **client_kwargs,
) -> httpx.AsyncClient:
    """Devuelve un ``httpx.AsyncClient`` con IP-pinning para el host de ``url``.

    Resuelve+valida el host una vez (bloqueo de IPs privadas/internas, scheme y
    —salvo ``allow_any_port``— puerto). Lanza ``ValueError`` si algo no valida.
    Úsese como ``async with pinned_client(url, timeout=T) as client: ...`` y
    pásese la URL con hostname (el transporte la reescribe a la IP fijada).
    """
    safe_ip, hostname = URLValidator.resolve_validated_ip(url, allowed_domains)

    if not allow_any_port:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in URLValidator.SAFE_PORTS:
            raise ValueError(f"Port not allowed: {port}")

    client_kwargs.setdefault("follow_redirects", False)
    transport = _PinnedTransport({hostname: safe_ip})
    return httpx.AsyncClient(transport=transport, **client_kwargs)
