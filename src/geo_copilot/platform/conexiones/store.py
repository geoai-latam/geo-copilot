"""Conexiones MCP de cada organización (F6, S6.2), en `plataforma.conexiones`.

La configuración se guarda como la de un servidor del YAML (`ServerConfig`) SIN el secreto; el
secreto va aparte, cifrado (cifrado.py). Al construir el hub de la organización se descifra y
viaja solo en memoria (`AuthConfig.valor`, que no se serializa ni se imprime).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from geo_copilot.platform.conexiones.cifrado import CifradoNoConfigurado, Cifrador
from geo_copilot.platform.mcp.config import ServerConfig

ROL_PLATAFORMA = "geo_plataforma"


class ConexionExiste(Exception):
    """Ya hay una conexión con ese id en la organización."""


@dataclass
class Conexion:
    org_id: str
    id: str
    config: dict[str, Any]
    tiene_credencial: bool
    creada_por: str
    creada: str

    def publica(self) -> dict[str, Any]:
        """Lo que se puede mostrar: la configuración y si hay credencial, NUNCA la credencial."""
        return {"id": self.id, "url": self.config.get("url"), "description": self.config.get("description"),
                "adapter": self.config.get("adapter"), "trust": self.config.get("trust"),
                "tiene_credencial": self.tiene_credencial, "creada_por": self.creada_por, "creada": self.creada}


def _config_sin_secreto(cfg: ServerConfig) -> dict[str, Any]:
    d = cfg.model_dump(mode="json", exclude={"red_restringida", "enabled"})
    d["auth"] = {"type": cfg.auth.type}  # la referencia al secreto la pone el núcleo
    return d


def config_con_secreto(d: dict[str, Any], secreto: str | None) -> ServerConfig:
    datos = {**d, "auth": {"type": (d.get("auth") or {}).get("type", "bearer")}, "red_restringida": True}
    cfg = ServerConfig.model_validate(datos)
    if secreto is not None:
        from pydantic import SecretStr

        cfg.auth.valor = SecretStr(secreto)
    return cfg


class ConexionesStore(Protocol):
    async def listar(self, org_id: str) -> list[Conexion]: ...
    async def crear(self, org_id: str, cfg: ServerConfig, secreto: str | None, creada_por: str) -> Conexion: ...
    async def borrar(self, org_id: str, conexion_id: str) -> bool: ...
    async def configs(self, org_id: str) -> list[ServerConfig]: ...


class ConexionesEnMemoria:
    def __init__(self, cifrador: Cifrador | None) -> None:
        self._cifrador = cifrador
        self._d: dict[tuple[str, str], tuple[dict[str, Any], bytes | None, str, str]] = {}

    async def listar(self, org_id: str) -> list[Conexion]:
        return [Conexion(o, i, c, s is not None, por, cuando) for (o, i), (c, s, por, cuando) in self._d.items()
                if o == org_id]

    async def crear(self, org_id: str, cfg: ServerConfig, secreto: str | None, creada_por: str) -> Conexion:
        if (org_id, cfg.id) in self._d:
            raise ConexionExiste(cfg.id)
        sobre = _cifrar(self._cifrador, secreto, org_id, cfg.id)
        cuando = datetime.now(UTC).isoformat()
        self._d[(org_id, cfg.id)] = (_config_sin_secreto(cfg), sobre, creada_por, cuando)
        return Conexion(org_id, cfg.id, _config_sin_secreto(cfg), sobre is not None, creada_por, cuando)

    async def borrar(self, org_id: str, conexion_id: str) -> bool:
        return self._d.pop((org_id, conexion_id), None) is not None

    async def configs(self, org_id: str) -> list[ServerConfig]:
        return [config_con_secreto(c, _descifrar(self._cifrador, s, o, i))
                for (o, i), (c, s, _p, _c) in self._d.items() if o == org_id]


def _cifrar(cifrador: Cifrador | None, secreto: str | None, org_id: str, conexion_id: str) -> bytes | None:
    if not secreto:
        return None
    if cifrador is None:
        raise CifradoNoConfigurado("falta SECRETS_KEK: no se pueden guardar credenciales de conexiones")
    return cifrador.cifrar(secreto, org_id=org_id, conexion_id=conexion_id)


def _descifrar(cifrador: Cifrador | None, sobre: bytes | None, org_id: str, conexion_id: str) -> str | None:
    if sobre is None:
        return None
    if cifrador is None:
        raise CifradoNoConfigurado("falta SECRETS_KEK: no se pueden leer credenciales de conexiones")
    return cifrador.descifrar(bytes(sobre), org_id=org_id, conexion_id=conexion_id)


class ConexionesEnPostgres:
    def __init__(self, pool: Any, cifrador: Cifrador | None) -> None:
        self._pool = pool
        self._cifrador = cifrador

    async def listar(self, org_id: str) -> list[Conexion]:
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            filas = await conn.fetch(
                "SELECT id, config, secreto IS NOT NULL AS tiene, creada_por, created_at FROM plataforma.conexiones "
                "WHERE org_id = $1 AND habilitada ORDER BY id", org_id)
        return [Conexion(org_id, f["id"], _json(f["config"]), f["tiene"], f["creada_por"], f["created_at"].isoformat())
                for f in filas]

    async def crear(self, org_id: str, cfg: ServerConfig, secreto: str | None, creada_por: str) -> Conexion:
        import asyncpg

        sobre = _cifrar(self._cifrador, secreto, org_id, cfg.id)
        config = _config_sin_secreto(cfg)
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
                f = await conn.fetchrow(
                    "INSERT INTO plataforma.conexiones (org_id, id, config, secreto, creada_por) "
                    "VALUES ($1, $2, $3::jsonb, $4, $5) RETURNING created_at",
                    org_id, cfg.id, json.dumps(config), sobre, creada_por)
        except asyncpg.UniqueViolationError as exc:
            raise ConexionExiste(cfg.id) from exc
        return Conexion(org_id, cfg.id, config, sobre is not None, creada_por, f["created_at"].isoformat())

    async def borrar(self, org_id: str, conexion_id: str) -> bool:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            r = await conn.execute("DELETE FROM plataforma.conexiones WHERE org_id = $1 AND id = $2", org_id, conexion_id)
            await conn.execute("DELETE FROM plataforma.pins WHERE org_id = $1 AND servidor = $2", org_id, conexion_id)
        return bool(str(r).endswith(" 1"))

    async def configs(self, org_id: str) -> list[ServerConfig]:
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            filas = await conn.fetch("SELECT id, config, secreto FROM plataforma.conexiones "
                                     "WHERE org_id = $1 AND habilitada ORDER BY id", org_id)
        return [config_con_secreto(_json(f["config"]), _descifrar(self._cifrador, f["secreto"], org_id, f["id"]))
                for f in filas]


def _json(v: Any) -> dict[str, Any]:
    return json.loads(v) if isinstance(v, str) else dict(v)


class PinesEnPostgres:
    """Pins de las herramientas de UNA organización (`plataforma.pins`), con quién los aprobó."""

    def __init__(self, pool: Any, org_id: str) -> None:
        self._pool = pool
        self._org = org_id

    async def get(self, clave: str) -> str | None:
        servidor, _, tool = clave.partition(":")
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            huella = await conn.fetchval("SELECT huella FROM plataforma.pins WHERE org_id = $1 AND servidor = $2 "
                                         "AND tool = $3", self._org, servidor, tool)
        return str(huella) if huella is not None else None

    async def set(self, clave: str, valor: str, *, por: str = "") -> None:
        servidor, _, tool = clave.partition(":")
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            await conn.execute(
                "INSERT INTO plataforma.pins (org_id, servidor, tool, huella, aprobada_por) VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (org_id, servidor, tool) DO UPDATE SET huella = EXCLUDED.huella, "
                "aprobada_por = EXCLUDED.aprobada_por, aprobada_en = now()",
                self._org, servidor, tool, valor, por or "desconocido")
