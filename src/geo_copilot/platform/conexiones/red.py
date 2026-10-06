"""A dónde puede conectarse una conexión dada de alta por una organización (F6, S6.2).

La URL la escribe el administrador de una organización, no el de la plataforma: sin control,
servir para sondear la red interna (la BD, otros servidores MCP, el metadata de la nube). Por eso:

- solo `https`, salvo hosts permitidos explícitamente por la plataforma (`MCP_HOSTS_PERMITIDOS`,
  p. ej. servicios internos del compose en desarrollo);
- todas las IPs a las que resuelve el host deben ser públicas (no privadas, loopback,
  link-local, reservadas…), salvo esos mismos hosts permitidos;
- y la conexión se hace A LA IP VALIDADA (con el nombre en Host y SNI): un DNS que cambie de
  respuesta entre la validación y la conexión (rebinding) no la lleva a otra parte.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

import httpx


class UrlNoPermitida(ValueError):
    """La URL no es un destino permitido para una conexión de organización."""


def hosts_permitidos(entorno: dict[str, str] | None = None) -> frozenset[str]:
    crudo = (entorno if entorno is not None else os.environ).get("MCP_HOSTS_PERMITIDOS", "")
    return frozenset(h.strip().lower() for h in crudo.split(",") if h.strip())


def _publica(ip: str) -> bool:
    d = ipaddress.ip_address(ip)
    if isinstance(d, ipaddress.IPv6Address) and d.ipv4_mapped:
        d = d.ipv4_mapped
    return d.is_global and not d.is_multicast


async def resolver_publica(host: str) -> str:
    """La primera IP de `host`, si TODAS las suyas son públicas; si no, UrlNoPermitida."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlNoPermitida(f"el host «{host}» no resuelve") from exc
    ips = [str(i[4][0]) for i in infos]
    if not ips:
        raise UrlNoPermitida(f"el host «{host}» no resuelve")
    internas = [ip for ip in ips if not _publica(ip)]
    if internas:
        raise UrlNoPermitida(f"«{host}» resuelve a una dirección interna ({internas[0]}): no permitido")
    return ips[0]


async def validar_url(url: str, permitidos: frozenset[str] | None = None) -> None:
    permitidos = hosts_permitidos() if permitidos is None else permitidos
    partes = urlsplit(url)
    host = (partes.hostname or "").lower()
    if not host:
        raise UrlNoPermitida("la URL no tiene host")
    if partes.username or partes.password:
        raise UrlNoPermitida("la URL no puede llevar credenciales (van en el campo de credencial)")
    if host in permitidos:
        return
    if partes.scheme != "https":
        raise UrlNoPermitida("solo se permiten URLs https")
    await resolver_publica(host)


class TransporteFijado(httpx.AsyncBaseTransport):
    """Transporte que conecta a la IP validada del host (anti DNS rebinding)."""

    def __init__(self, permitidos: frozenset[str] | None = None) -> None:
        self._permitidos = hosts_permitidos() if permitidos is None else permitidos
        self._interno = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        if host in self._permitidos:
            return await self._interno.handle_async_request(request)
        if request.url.scheme != "https":
            raise UrlNoPermitida("solo se permiten URLs https")
        try:
            ipaddress.ip_address(host)
            es_ip = True
        except ValueError:
            es_ip = False
        ip = host if es_ip else await resolver_publica(host)
        if es_ip and not _publica(ip):
            raise UrlNoPermitida(f"dirección interna no permitida: {ip}")
        request.url = request.url.copy_with(host=ip)
        request.headers["Host"] = host if request.url.port in (None, 443) else f"{host}:{request.url.port}"
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await self._interno.handle_async_request(request)

    async def aclose(self) -> None:
        await self._interno.aclose()


def transporte_para(cfg: object) -> httpx.AsyncBaseTransport | None:
    """El transporte con el que hablar con el servidor de `cfg`: fijado si es de una organización."""
    return TransporteFijado() if getattr(cfg, "red_restringida", False) else None
