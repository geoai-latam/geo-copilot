"""Cliente HTTP con protección SSRF por IP-pinning (portado del núcleo, SEC-01; antes en arcgis-mcp).

Los servidores que piden URLs que vienen de fuera (catálogos, texto del usuario, el LLM) las piden
en su nombre: `cliente(url)` resuelve y valida el host UNA vez (esquema http/https, puerto estándar,
NINGUNA IP privada/interna entre las que resuelve) y devuelve un `httpx.Client` cuyo transporte manda
cada petición a esa IP fijada, con `Host` y SNI del nombre original (el certificado sigue validando
contra el nombre). Así se cierra el DNS rebinding (validar con una resolución y conectar con otra).
Sin redirecciones: una a otro host saldría sin pin.

Requiere `httpx` (extra `red` del kit).
"""

from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx

REDES_BLOQUEADAS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "0.0.0.0/8",
    "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
    "::1/128", "fc00::/7", "fe80::/10", "ff00::/8",
)]
PUERTOS_SEGUROS = {80, 443, 8080, 8443, 6443}


class UrlNoPermitida(ValueError):
    """La URL no se puede pedir desde el servidor (esquema, puerto, dominio o IP interna)."""


def ip_validada(url: str, dominios: list[str] | None = None) -> tuple[str, str]:
    """(ip, hostname) tras validar TODAS las IPs del host; una sola interna bloquea todo."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise UrlNoPermitida(f"esquema no permitido: {p.scheme or '(ninguno)'}")
    host = p.hostname
    if not host:
        raise UrlNoPermitida("la URL no tiene host")
    puerto = p.port or (443 if p.scheme == "https" else 80)
    if puerto not in PUERTOS_SEGUROS:
        raise UrlNoPermitida(f"puerto no permitido: {puerto}")
    if dominios and not any(host == d or host.endswith(f".{d}") for d in dominios):
        raise UrlNoPermitida(f"dominio no permitido: {host}")
    try:
        ips = socket.gethostbyname_ex(host)[2]
    except (socket.gaierror, socket.herror) as exc:
        raise UrlNoPermitida(f"no se resuelve el host {host}") from exc
    for ip in ips:
        obj = ipaddress.ip_address(ip)
        destino = obj.ipv4_mapped if isinstance(obj, ipaddress.IPv6Address) and obj.ipv4_mapped else obj
        if any(destino in red for red in REDES_BLOQUEADAS):
            raise UrlNoPermitida(f"{host} resuelve a una IP interna ({ip})")
    if not ips:
        raise UrlNoPermitida(f"{host} no resuelve a ninguna IP")
    return ips[0], host


class _TransporteFijado(httpx.HTTPTransport):
    def __init__(self, host_a_ip: dict[str, str], **kw) -> None:
        super().__init__(**kw)
        self._host_a_ip = host_a_ip

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        ip = self._host_a_ip.get(host)
        if ip:
            request.headers["Host"] = host
            request.extensions["sni_hostname"] = host
            request.url = request.url.copy_with(host=ip)
        return super().handle_request(request)


def cliente(url: str, *, dominios: list[str] | None = None, timeout: float = 20.0,
            user_agent: str = "geo-copilot-mcp/1") -> httpx.Client:
    """`httpx.Client` fijado a la IP validada del host de `url` (lanza `UrlNoPermitida`)."""
    ip, host = ip_validada(url, dominios)
    return httpx.Client(transport=_TransporteFijado({host: ip}), timeout=timeout, follow_redirects=False,
                        headers={"User-Agent": user_agent})


class ErrorRed(Exception):
    """El servicio externo no respondió o respondió un error HTTP (mensaje honesto para el LLM).

    `codigo` es el código rt/1 que el ToolRunner propaga. None deja el defecto del runner
    (`parametro_invalido`), para los errores de configuración o de parámetro. Una caída, un timeout
    o un 5xx son `fuente_no_disponible` (reintentar sirve); un 4xx o una respuesta inválida,
    `fuente_respuesta_invalida`. Sin esto, la caída de una fuente salía como `error_interno` —como si
    la puerta se hubiera roto— (auditoría de fuentes 2026-10-25: el SDMX de BanRep en HTTP 500).
    """

    def __init__(self, mensaje: str, codigo: str | None = None) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


class RespuestaGrande(ErrorRed):
    """La respuesta pasa del tope: quien pagina puede pedir menos de una vez."""


def get(url: str, params: dict[str, Any] | None = None, *, dominios: list[str] | None = None,
        timeout: float = 60.0, user_agent: str = "geo-copilot-mcp/1", reintentos: int = 1,
        max_bytes: int = 40_000_000) -> httpx.Response:
    """GET con el cliente SSRF. Un fallo de RED se reintenta; un HTTP de error es la respuesta y no se repite.

    Medido (ogc-mcp, 2026-10-15): servidores públicos (IGAC) cortan conexiones de vez en cuando y a la
    siguiente responden.
    """
    return _pedir("GET", url, {"params": params}, dominios, timeout, user_agent, reintentos, max_bytes)


def post(url: str, data: dict[str, Any], *, dominios: list[str] | None = None, timeout: float = 60.0,
         user_agent: str = "geo-copilot-mcp/1", reintentos: int = 1, max_bytes: int = 40_000_000) -> httpx.Response:
    """POST de formulario con el cliente SSRF (consultas largas: un `IN (…)` de cientos de códigos)."""
    return _pedir("POST", url, {"data": data}, dominios, timeout, user_agent, reintentos, max_bytes)


def _pedir(metodo: str, url: str, kw: dict[str, Any], dominios: list[str] | None, timeout: float,
           user_agent: str, reintentos: int, max_bytes: int) -> httpx.Response:
    for intento in range(reintentos + 1):
        try:
            with cliente(url, dominios=dominios, timeout=timeout, user_agent=user_agent) as c:
                r = c.request(metodo, url, **kw)
            break
        except httpx.TransportError as exc:
            if intento == reintentos:
                raise ErrorRed(
                    f"el servicio no respondió ({type(exc).__name__})", "fuente_no_disponible"
                ) from exc
            time.sleep(2.0)
    if r.status_code != 200:
        # un 4xx (salvo 429) es la fuente rechazando la consulta; un 5xx/429/caída, la fuente no
        # disponible (reintentar sirve). Nunca `error_interno`: la puerta no se rompió.
        codigo = (
            "fuente_respuesta_invalida"
            if 400 <= r.status_code < 500 and r.status_code != 429
            else "fuente_no_disponible"
        )
        raise ErrorRed(f"el servicio respondió HTTP {r.status_code}: {r.text[:200]}", codigo)
    if len(r.content) > max_bytes:
        raise RespuestaGrande(
            f"la respuesta del servicio pasa de {max_bytes // 1_000_000} MB",
            "fuente_respuesta_invalida",
        )
    return r
