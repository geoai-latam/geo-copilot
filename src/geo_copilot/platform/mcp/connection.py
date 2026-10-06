"""Conexión a un servidor MCP (S3.2): el cliente genérico del núcleo.

Sobre el SDK oficial (`mcp.ClientSession` + streamable HTTP). Lo que el núcleo
NO delega en el servidor (§3.6.6): allowlist de tools, tiempo máximo y tamaño
máximo de resultado se aplican AQUÍ. Un servidor que falla seguido se marca no
disponible (circuit breaker) y el agente lo dice en vez de colgarse.

Servidores sin estado (`stateless_http`): una sesión corta por operación. El
catálogo de tools se cachea con TTL (los servidores sin sesión no emiten
`notifications/tools/list_changed`; al reconectar se revalida).
"""

from __future__ import annotations

import json
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from geo_copilot.core.logging import get_logger
from geo_copilot.platform.mcp.config import ServerConfig

logger = get_logger(__name__)

#: Descubrir las tools de un servidor no es una tarea larga: más de esto es un servidor colgado.
LISTAR_TIMEOUT_S = 20.0
LISTAR_MAX_PAGINAS = 50

Estado = Literal["desconocido", "disponible", "no_disponible"]


class McpError(Exception):
    """Fallo al hablar con un servidor MCP (mensaje apto para el LLM/usuario)."""


class ServidorNoDisponible(McpError):
    pass


def traceparent() -> str:
    """W3C trace context para el servidor MCP (correlación núcleo ↔ servidor).

    F7 (S7.3): con OpenTelemetry activo, el del span EN CURSO (la herramienta que llama): así la
    traza del turno sigue dentro del servidor. Sin OTel, uno nuevo (antes, siempre uno al azar:
    las trazas quedaban partidas en dos)."""
    try:
        from opentelemetry import propagate, trace

        if trace.get_current_span().get_span_context().is_valid:
            portador: dict[str, str] = {}
            propagate.inject(portador)
            if portador.get("traceparent"):
                return portador["traceparent"]
    except ImportError:
        pass
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


@dataclass
class _Breaker:
    umbral: int = 3
    enfriamiento_s: float = 30.0
    fallos: int = 0
    abierto_hasta: float = 0.0

    def abierto(self) -> bool:
        return time.monotonic() < self.abierto_hasta

    def exito(self) -> None:
        self.fallos, self.abierto_hasta = 0, 0.0

    def fallo(self) -> None:
        self.fallos += 1
        if self.fallos >= self.umbral:
            self.abierto_hasta = time.monotonic() + self.enfriamiento_s


@dataclass
class McpConnection:
    cfg: ServerConfig
    ttl_catalogo_s: float = 300.0
    breaker: _Breaker = field(default_factory=_Breaker)
    estado: Estado = "desconocido"
    ultimo_error: str | None = None
    info_servidor: dict[str, Any] = field(default_factory=dict)
    _tools: list[Any] | None = None
    _tools_en: float = 0.0

    # ------------------------------------------------------------------
    @asynccontextmanager
    async def _sesion(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"traceparent": traceparent()}
        from geo_copilot.platform.observabilidad import correlacion

        if (id_peticion := correlacion()) is not None:
            headers["X-Request-ID"] = id_peticion  # la misma petición en los logs del servidor
        if self.cfg.auth.type == "token_exchange":
            # F6 (S6.3): el token del USUARIO de la petición para este servidor; sin usuario
            # (el sistema descubriendo tools), la clave de servicio
            from geo_copilot.platform.identidad.intercambio import token_para

            headers["Authorization"] = f"Bearer {await token_para(self.cfg)}"
        elif self.cfg.auth.type == "bearer":
            secreto = self.cfg.auth.resolver()
            if not secreto:
                raise ServidorNoDisponible(
                    f"el servidor '{self.cfg.id}' no tiene credencial configurada "
                    f"({self.cfg.auth.secret_ref})"
                )
            headers["Authorization"] = f"Bearer {secreto}"
        tiempo = httpx.Timeout(min(30.0, self.cfg.policy.timeout_s), read=self.cfg.policy.timeout_s)
        # F6: una conexión de organización va a la IP pública validada y fijada
        from geo_copilot.platform.conexiones.red import transporte_para

        transporte = transporte_para(self.cfg)
        async with httpx.AsyncClient(headers=headers, timeout=tiempo, follow_redirects=False,
                                     transport=transporte) as http,                 streamable_http_client(self.cfg.url, http_client=http) as (lectura, escritura, _):
            async with ClientSession(
                lectura, escritura, read_timeout_seconds=timedelta(seconds=self.cfg.policy.timeout_s),
            ) as sesion:
                init = await sesion.initialize()
                self.info_servidor = {
                    "name": getattr(init.serverInfo, "name", None),
                    "version": getattr(init.serverInfo, "version", None),
                    "instructions": getattr(init, "instructions", None),
                }
                yield sesion

    async def _con_breaker(self, operacion):
        if self.breaker.abierto():
            self.estado = "no_disponible"
            raise ServidorNoDisponible(
                f"el servicio '{self.cfg.id}' no está disponible ahora mismo "
                f"({self.ultimo_error or 'falló varias veces seguidas'})"
            )
        try:
            resultado = await operacion()
        except ServidorNoDisponible as exc:
            self.estado, self.ultimo_error = "no_disponible", str(exc)
            self.breaker.fallo()
            _metrica_error(self.cfg.id, "no_disponible")
            raise
        except McpError:
            raise  # fallo de la tool (no del servidor): no cuenta para el breaker
        except Exception as exc:  # red/protocolo: se re-lanza como ServidorNoDisponible
            _metrica_error(self.cfg.id, type(exc).__name__)
            self.estado = "no_disponible"
            self.ultimo_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            self.breaker.fallo()
            logger.warning("[mcp:%s] fallo de conexión: %s", self.cfg.id, self.ultimo_error)
            raise ServidorNoDisponible(
                f"el servicio '{self.cfg.id}' no respondió ({type(exc).__name__})"
            ) from exc
        self.estado, self.ultimo_error = "disponible", None
        self.breaker.exito()
        return resultado

    # ------------------------------------------------------------------
    async def list_tools(self, *, forzar: bool = False) -> list[Any]:
        """Tools permitidas por la allowlist (catálogo cacheado con TTL)."""
        if not forzar and self._tools is not None and time.monotonic() - self._tools_en < self.ttl_catalogo_s:
            return self._tools

        async def _listar() -> list[Any]:
            import anyio

            # Tope de tiempo: sin él, un servidor colgado dejaba colgado el refresco (y el login de
            # su organización, que lo dispara). Paginación: un servidor con muchas tools las entrega
            # por páginas (`nextCursor`); antes solo se leía la primera.
            tools: list[Any] = []
            with anyio.fail_after(min(self.cfg.policy.timeout_s, LISTAR_TIMEOUT_S)):
                async with self._sesion() as s:
                    cursor = None
                    for _ in range(LISTAR_MAX_PAGINAS):
                        res = await s.list_tools(cursor=cursor) if cursor else await s.list_tools()
                        tools.extend(res.tools)
                        cursor = getattr(res, "nextCursor", None)
                        if not cursor:
                            break
            return [t for t in tools if self.cfg.tools.permite(t.name)]

        self._tools = await self._con_breaker(_listar)
        self._tools_en = time.monotonic()
        return self._tools

    async def call_tool(self, nombre: str, argumentos: dict[str, Any]) -> Any:
        """Resultado MCP de la tool; aplica allowlist, timeout y tamaño máximo aquí."""
        if not self.cfg.tools.permite(nombre):
            raise McpError(f"la tool '{nombre}' no está permitida en el servidor '{self.cfg.id}'")

        async def _llamar():
            import anyio

            with anyio.fail_after(self.cfg.policy.timeout_s):
                async with self._sesion() as s:
                    return await s.call_tool(nombre, argumentos)

        try:
            resultado = await self._con_breaker(_llamar)
        except TimeoutError as exc:  # anyio.fail_after
            raise McpError(
                f"'{nombre}' superó el tiempo máximo de {self.cfg.policy.timeout_s:.0f} s"
            ) from exc
        tam = len(json.dumps(resultado.model_dump(mode="json"), default=str))
        if tam > self.cfg.policy.max_result_mb * 1024 * 1024:
            raise McpError(
                f"el resultado de '{nombre}' pesa {tam / 1e6:.1f} MB y supera el máximo "
                f"de {self.cfg.policy.max_result_mb:g} MB del servidor '{self.cfg.id}'"
            )
        return resultado


def _metrica_error(servidor: str, tipo: str) -> None:
    from geo_copilot.platform.observabilidad import registrar_mcp_error

    registrar_mcp_error(servidor, tipo)
