"""Tickets de un solo uso para abrir el WebSocket (F6, S6.1).

Un navegador no puede poner cabeceras en un WebSocket, y el JWT en la URL acabaría en los logs
de nginx y de uvicorn. El cliente pide por REST (con su Bearer) un ticket para SU sesión; el
ticket vale `ws_ticket_ttl_s` segundos y se consume al abrir el socket: en un log no sirve.

Con Redis (varios workers) el ticket se consume con GETDEL, atómico: dos sockets no pueden
usar el mismo ticket.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Protocol

from geo_copilot.platform.identidad.principal import Principal


class TicketStore(Protocol):
    async def emitir(self, principal: Principal, session_id: str, ttl_s: int) -> str: ...
    async def consumir(self, ticket: str, session_id: str) -> Principal | None: ...


def _a_json(p: Principal, session_id: str) -> str:
    return json.dumps({"sub": p.sub, "org_id": p.org_id, "roles": sorted(p.roles), "nombre": p.nombre,
                       "via": p.via, "session_id": session_id})


def _de_json(crudo: str, session_id: str) -> Principal | None:
    d = json.loads(crudo)
    if d.get("session_id") != session_id:  # un ticket es de UNA sesión
        return None
    return Principal(sub=d["sub"], org_id=d["org_id"], roles=frozenset(d["roles"]), nombre=d["nombre"], via=d["via"])


class TicketsEnMemoria:
    """Un solo proceso (desarrollo y tests)."""

    def __init__(self) -> None:
        self._t: dict[str, tuple[float, str]] = {}

    async def emitir(self, principal: Principal, session_id: str, ttl_s: int) -> str:
        ahora = time.monotonic()
        self._t = {k: v for k, v in self._t.items() if v[0] > ahora}
        ticket = secrets.token_urlsafe(32)
        self._t[ticket] = (ahora + ttl_s, _a_json(principal, session_id))
        return ticket

    async def consumir(self, ticket: str, session_id: str) -> Principal | None:
        vence, crudo = self._t.pop(ticket, (0.0, ""))
        if vence <= time.monotonic():
            return None
        return _de_json(crudo, session_id)


class TicketsEnRedis:
    def __init__(self, url: str, cliente: Any = None) -> None:
        if cliente is None:
            import redis.asyncio as aioredis

            cliente = aioredis.Redis.from_url(url, decode_responses=True)
        self._r = cliente

    async def emitir(self, principal: Principal, session_id: str, ttl_s: int) -> str:
        ticket = secrets.token_urlsafe(32)
        await self._r.set(f"ws:ticket:{ticket}", _a_json(principal, session_id), ex=ttl_s)
        return ticket

    async def consumir(self, ticket: str, session_id: str) -> Principal | None:
        crudo = await self._r.getdel(f"ws:ticket:{ticket}")
        if not crudo:
            return None
        return _de_json(crudo.decode() if isinstance(crudo, bytes) else crudo, session_id)
