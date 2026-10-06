"""Envoltorio ASGI de un servidor GeoMCP: /health, /metrics, auth, rate limit y rutas extra.

Orden de las capas (extraído de imagery-mcp, S3.1):
1. `/health` abierto (healthchecks del compose y del cliente).
2. Todo lo demás exige Bearer válido (401).
3. `/metrics` tras el Bearer: revela patrones de uso.
4. Rate limit por clave (429), con peso por ruta (teselas baratas).
5. Rutas extra del servidor (p. ej. teselas) con su scope (403, fail-closed).
6. `/mcp`: si el mensaje JSON-RPC es `tools/call`, se exige el scope de CADA
   tool invocada (403) antes de que el SDK la vea. El cuerpo se lee, se
   inspecciona y se re-inyecta, con tope de tamaño (413).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from geo_mcp_kit.auth import ApiKey, KeyRing, RateLimiter, extract_bearer

logger = logging.getLogger("geo_mcp_kit")

Scope = dict[str, Any]
Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class ExtraRoute:
    """Una ruta HTTP propia del servidor (teselas, descargas firmadas…).

    ``match(path)`` devuelve los parámetros de la ruta o None si no es suya.
    ``handler(scope, send, params, api_key)`` responde. ``requires_tool``: la
    ruta hereda el scope de esa tool (quien no puede llamar la tool, no ve su
    salida). ``weight``: peso en el rate limit.
    """

    match: Callable[[str], Any]
    handler: Callable[[Scope, Send, Any, ApiKey], Awaitable[None]]
    requires_tool: str
    weight: float = 1.0


def jsonrpc_tool_names(body: bytes) -> list[str]:
    """Nombres de tools invocadas en un cuerpo JSON-RPC (single o batch)."""
    try:
        payload = json.loads(body or b"null")
    except ValueError:
        return []
    messages = payload if isinstance(payload, list) else [payload]
    names = []
    for m in messages:
        if isinstance(m, dict) and m.get("method") == "tools/call":
            name = (m.get("params") or {}).get("name")
            if name:
                names.append(str(name))
    return names


async def respond_json(send: Send, status: int, payload: dict) -> None:
    raw = json.dumps(payload).encode()
    await send({
        "type": "http.response.start", "status": status,
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode())],
    })
    await send({"type": "http.response.body", "body": raw})


async def respond_png(send: Send, png: bytes, *, max_age: int = 86400) -> None:
    await send({
        "type": "http.response.start", "status": 200,
        "headers": [(b"content-type", b"image/png"),
                    (b"cache-control", f"public, max-age={max_age}".encode()),
                    (b"content-length", str(len(png)).encode())],
    })
    await send({"type": "http.response.body", "body": png})


class GeoMcpAuth:
    """401 sin clave · 429 sobre el límite · 403 sin scope (fail-closed) · 413 cuerpo enorme."""

    def __init__(
        self,
        app: Any,
        keyring: KeyRing,
        limiter: RateLimiter,
        *,
        service: str,
        routes: tuple[ExtraRoute, ...] = (),
        metrics: Callable[[], dict] | None = None,
        max_body_bytes: int = 2 * 1024 * 1024,
        jwt: Any = None,
    ) -> None:
        # F7 (S7.3): con OTEL_EXPORTER_OTLP_ENDPOINT, la traza del turno del agente sigue aquí
        from geo_mcp_kit.observabilidad import instalar_correlacion, instrumentar

        self.app = instrumentar(app, service)
        instalar_correlacion()  # F7 (auditoría): el X-Request-ID del núcleo en los logs
        self.keyring = keyring
        #: F6: verificador de tokens de usuario (oidc.VerificadorJwt), además de las API keys
        self.jwt = jwt
        self.limiter = limiter
        self.service = service
        self.routes = routes
        self.metrics = metrics
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Any, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from geo_mcp_kit.observabilidad import fijar_peticion, restaurar_peticion

        id_peticion = next((v.decode("latin-1") for k, v in scope.get("headers", [])
                            if k.lower() == b"x-request-id"), None)
        ficha = fijar_peticion(id_peticion)
        try:
            await self._atender(scope, receive, send)
        finally:
            restaurar_peticion(ficha)

    async def _atender(self, scope: Scope, receive: Any, send: Send) -> None:  # noqa: C901, PLR0912
        path = scope.get("path", "")
        if path == "/health":
            await respond_json(send, 200, {"status": "healthy", "service": self.service})
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        bearer = extract_bearer(headers.get("authorization"))
        api_key = self.keyring.verify(bearer)
        if api_key is None and self.jwt is not None:
            api_key = await self.jwt.verificar(bearer)
        if api_key is None:
            await respond_json(send, 401, {"error": "credencial inválida o ausente"})
            return

        if path == "/metrics":
            await respond_json(send, 200, {"service": self.service, "metrics": self.metrics() if self.metrics else {}})
            return

        for route in self.routes:
            params = route.match(path)
            if params is None:
                continue
            if not self.limiter.allow(api_key, weight=route.weight):
                await respond_json(send, 429, {"error": "rate limit excedido"})
                return
            if not api_key.allows_tool(route.requires_tool):
                await respond_json(send, 403, {"error": f"sin scope para {route.requires_tool}"})
                return
            await route.handler(scope, send, params, api_key)
            return

        if not self.limiter.allow(api_key):
            await respond_json(send, 429, {"error": "rate limit excedido"})
            return

        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > self.max_body_bytes:
                await respond_json(send, 413, {"error": "cuerpo demasiado grande"})
                return
            more = message.get("more_body", False)

        for tool_name in jsonrpc_tool_names(body):
            if not api_key.allows_tool(tool_name):
                await respond_json(send, 403, {
                    "error": f"la clave '{api_key.name}' no tiene scope para {tool_name}",
                })
                return

        consumed = False

        async def replay() -> dict[str, Any]:
            # El cuerpo ya leído (para el scope por tool) se entrega UNA vez; después, el
            # `receive` real, que ESPERA hasta `http.disconnect`. Antes devolvía «cuerpo vacío»
            # al instante y para siempre: con un GET /mcp abierto (canal de eventos, p. ej. el
            # puente mcp-remote de Claude Desktop) el SDK, que llama a receive() para detectar
            # la desconexión, giraba al 100 % de CPU y el servidor dejaba de responder a todos.
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        from geo_mcp_kit.oidc import fijar_llamante

        fijar_llamante(api_key)  # las tools saben quién llama (oidc.llamante())
        await self.app(scope, replay, send)
