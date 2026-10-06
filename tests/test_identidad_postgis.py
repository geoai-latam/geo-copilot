"""F6 — dueños de sesión y de proyecto contra PostGIS real (`-m postgis`).

Mismo esquema que producción (docker/init-db/10_plataforma.sql, montado en el compose de
tests) y el rol de la app de tests (`gc_app`, NOINHERIT) bajando a `geo_plataforma`.
"""

from __future__ import annotations

import uuid

import pytest

from geo_copilot.platform.identidad.principal import Principal
from geo_copilot.platform.identidad.propiedad import PropiedadEnPostgres, SesionAjena

pytestmark = pytest.mark.postgis

ANA = Principal(sub=f"ana-{uuid.uuid4().hex[:6]}", org_id="acme", roles=frozenset({"analyst"}))
BETO = Principal(sub=f"beto-{uuid.uuid4().hex[:6]}", org_id="acme", roles=frozenset({"admin"}))


@pytest.mark.asyncio(loop_scope="session")
async def test_el_dueno_de_una_sesion_se_guarda_y_no_cambia(workspace_pool):
    prop = PropiedadEnPostgres(workspace_pool)
    sid = f"s-{uuid.uuid4().hex}"
    await prop.asegurar(ANA, sid)
    assert await prop.dueno(sid) == (ANA.sub, "acme")
    # otro proceso (sin la caché de este) tampoco se la queda
    with pytest.raises(SesionAjena):
        await PropiedadEnPostgres(workspace_pool).asegurar(BETO, sid)
    # la app no puede reasignarla: la tabla solo admite INSERT/SELECT para su rol
    async with workspace_pool.acquire() as conn:
        with pytest.raises(Exception, match="permission denied|permiso denegado"):
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE geo_plataforma")
                await conn.execute("UPDATE plataforma.sesiones SET owner_sub = $1 WHERE session_id = $2",
                                   BETO.sub, sid)


@pytest.mark.asyncio(loop_scope="session")
async def test_los_proyectos_son_de_quien_los_guarda(workspace_pool):
    from geo_copilot.platform.workspace import DatasetStore

    store = DatasetStore(workspace_pool)
    sid = f"s-{uuid.uuid4().hex}"
    p = await store.guardar_proyecto(sid, "Finca", {"capas": []}, owner_sub=ANA.sub, org_id="acme")
    assert [x["id"] for x in await store.listar_proyectos(ANA.sub)].count(p["id"]) == 1
    assert p["id"] not in [x["id"] for x in await store.listar_proyectos(BETO.sub)]
    assert (await store.obtener_proyecto(p["id"]))["owner_sub"] == ANA.sub
