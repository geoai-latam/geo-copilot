"""F6 (S6.2) — conexiones y pins de organización en PostGIS real (`-m postgis`)."""

from __future__ import annotations

import base64
import os
import uuid

import pytest

from geo_copilot.platform.conexiones.cifrado import Cifrador
from geo_copilot.platform.conexiones.store import (
    ConexionesEnPostgres,
    ConexionExiste,
    PinesEnPostgres,
)
from geo_copilot.platform.mcp.config import ServerConfig

pytestmark = [pytest.mark.postgis, pytest.mark.asyncio(loop_scope="session")]

ORG = f"org-{uuid.uuid4().hex[:8]}"
OTRA = f"org-{uuid.uuid4().hex[:8]}"
CIFRADOR = Cifrador(os.urandom(32))


def _cfg(cid: str) -> ServerConfig:
    return ServerConfig.model_validate({"id": cid, "url": "https://mcp.example.org/mcp", "auth": {"type": "bearer"},
                                        "tools": {"allow": ["*"]}, "adapter": "tabular_geo", "red_restringida": True})


async def test_la_conexion_se_guarda_cifrada_y_es_solo_de_su_organizacion(workspace_pool):
    s = ConexionesEnPostgres(workspace_pool, CIFRADOR)
    secreto = "sk-" + base64.b64encode(os.urandom(12)).decode()
    c = await s.crear(ORG, _cfg("almacen"), secreto, "carla")
    assert c.tiene_credencial and c.creada_por == "carla"
    with pytest.raises(ConexionExiste):
        await s.crear(ORG, _cfg("almacen"), None, "carla")
    # en la BD solo está el sobre cifrado
    async with workspace_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE geo_plataforma")
        sobre = await conn.fetchval("SELECT secreto FROM plataforma.conexiones WHERE org_id = $1 AND id = $2",
                                    ORG, "almacen")
        config = await conn.fetchval("SELECT config::text FROM plataforma.conexiones WHERE org_id = $1", ORG)
    assert secreto.encode() not in bytes(sobre) and secreto not in config
    (cfg,) = await s.configs(ORG)
    assert cfg.auth.resolver() == secreto and cfg.adapter == "tabular_geo" and cfg.red_restringida
    assert await s.listar(OTRA) == [] and await s.configs(OTRA) == []
    assert not await s.borrar(OTRA, "almacen")  # otra organización no la borra
    assert await s.borrar(ORG, "almacen") and await s.listar(ORG) == []


async def test_los_pins_son_por_organizacion_y_guardan_quien_aprobo(workspace_pool):
    a, b = PinesEnPostgres(workspace_pool, ORG), PinesEnPostgres(workspace_pool, OTRA)
    await a.set("almacen:run_query", "h1", por="primer-uso")
    assert await a.get("almacen:run_query") == "h1" and await b.get("almacen:run_query") is None
    await a.set("almacen:run_query", "h2", por="carla")
    assert await a.get("almacen:run_query") == "h2"
    async with workspace_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE geo_plataforma")
        por = await conn.fetchval("SELECT aprobada_por FROM plataforma.pins WHERE org_id = $1", ORG)
    assert por == "carla"
