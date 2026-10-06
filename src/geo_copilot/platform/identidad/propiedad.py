"""De quién es cada sesión (F6, S6.1).

La sesión de conversación y su workspace comparten id. El dueño se guarda en PostGIS
(`plataforma.sesiones`), no en Redis: la conversación caduca y el workspace (o un proyecto
guardado) sigue; su dueño no puede perderse con ella.

La primera petición autenticada que usa un id libre lo reclama; después, para cualquier otro
principal esa sesión NO EXISTE (404, no 403: no se confirma que exista). La propiedad no cambia
nunca, así que se guarda en caché en el proceso.
"""

from __future__ import annotations

from typing import Any, Protocol

from geo_copilot.platform.identidad.principal import Principal

ROL_PLATAFORMA = "geo_plataforma"


class SesionAjena(Exception):
    """La sesión existe y es de otro principal (la API responde 404)."""


class PropiedadStore(Protocol):
    async def asegurar(self, principal: Principal, session_id: str) -> None: ...
    async def dueno(self, session_id: str) -> tuple[str, str] | None: ...


class PropiedadEnMemoria:
    """Desarrollo sin BD y tests: la misma regla, en el proceso."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[str, str]] = {}

    async def asegurar(self, principal: Principal, session_id: str) -> None:
        sub, _org = self._d.setdefault(session_id, (principal.sub, principal.org_id))
        if sub != principal.sub:
            raise SesionAjena(session_id)

    async def dueno(self, session_id: str) -> tuple[str, str] | None:
        return self._d.get(session_id)


class PropiedadEnPostgres:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._cache: dict[str, str] = {}

    async def asegurar(self, principal: Principal, session_id: str) -> None:
        sub = self._cache.get(session_id)
        if sub is None:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
                await conn.execute(
                    "INSERT INTO plataforma.sesiones (session_id, owner_sub, org_id) VALUES ($1, $2, $3) "
                    "ON CONFLICT (session_id) DO NOTHING", session_id, principal.sub, principal.org_id)
                sub = await conn.fetchval("SELECT owner_sub FROM plataforma.sesiones WHERE session_id = $1",
                                          session_id)
            if len(self._cache) > 50_000:
                self._cache.clear()
            self._cache[session_id] = sub
        if sub != principal.sub:
            raise SesionAjena(session_id)

    async def dueno(self, session_id: str) -> tuple[str, str] | None:
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            fila = await conn.fetchrow("SELECT owner_sub, org_id FROM plataforma.sesiones WHERE session_id = $1",
                                       session_id)
        return (fila["owner_sub"], fila["org_id"]) if fila else None
