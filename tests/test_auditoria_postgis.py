"""F6 (S6.4) — la auditoría en PostGIS real (`-m postgis`): solo añadir y encadenada.

La app (rol de tests `gc_app` → `geo_plataforma`) escribe y lee, pero no puede cambiar ni
borrar filas. Un superusuario sí podría (desactivando el trigger): por eso la cadena de hash,
que `verificar()` recalcula en la BD y que delata la fila tocada.
"""

from __future__ import annotations

import os
import uuid

import pytest

from geo_copilot.platform.auditoria import AuditoriaEnPostgres, Evento
from geo_copilot.platform.identidad.principal import Principal

pytestmark = [pytest.mark.postgis, pytest.mark.asyncio(loop_scope="session")]

# El dueño de la BD de tests (docker-compose.test.yml): solo para simular una manipulación.
SUPER_URL = os.environ.get("TEST_SUPER_DATABASE_URL", "postgresql://gc_test:gc_test_pw@localhost:5434/geocopilot_test")
ORG = f"org-{uuid.uuid4().hex[:8]}"
ANA = Principal(sub="ana", org_id=ORG, roles=frozenset({"analyst"}), nombre="Ana", via="oidc")


async def test_escribe_lista_y_la_cadena_esta_integra(workspace_pool):
    a = AuditoriaEnPostgres(workspace_pool)
    await a.escribir(ANA, Evento("consulta", "agente", "recibida", "s1", {"consulta": "trae los lotes"}))
    await a.escribir(ANA, Evento("capacidad.ejecutar", "core.ws_buffer", "ok", "s1", {"argumentos": {"meters": 500}}))
    filas = await a.listar(ORG)
    assert [f["accion"] for f in filas] == ["capacidad.ejecutar", "consulta"]  # lo último primero
    assert filas[0]["detalle"] == {"argumentos": {"meters": 500}} and filas[0]["actor_nombre"] == "Ana"
    assert (await a.listar("otra-org")) == []
    v = await a.verificar()
    assert v["integra"] is True and v["encadenada"] is True and v["filas"] >= 2


async def test_la_app_no_puede_cambiar_ni_borrar_lo_auditado(workspace_pool):
    for sql in ("UPDATE plataforma.auditoria SET resultado = 'ok'", "DELETE FROM plataforma.auditoria",
                "TRUNCATE plataforma.auditoria"):
        async with workspace_pool.acquire() as conn:
            with pytest.raises(Exception, match="permission denied|permiso denegado|must be owner|debe ser dueño"):
                async with conn.transaction():
                    await conn.execute("SET LOCAL ROLE geo_plataforma")
                    await conn.execute(sql)


async def test_una_fila_tocada_directamente_rompe_la_cadena(workspace_pool):
    asyncpg = pytest.importorskip("asyncpg")
    a = AuditoriaEnPostgres(workspace_pool)
    await a.escribir(ANA, Evento("hitl.aprobar", "sql_execution:x", "approved", "s1", {"titulo": "SQL"}))
    fila = (await a.listar(ORG, limite=1))[0]
    try:
        su = await asyncpg.connect(SUPER_URL, timeout=3)
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"sin superusuario de la BD de tests: {e}")
    try:
        # hasta el superusuario choca con el trigger de inmutabilidad…
        with pytest.raises(asyncpg.RaiseError, match="solo añadir"):
            await su.execute("UPDATE plataforma.auditoria SET resultado = 'rejected' WHERE id = $1", fila["id"])
        # …si lo desactiva a propósito, la cadena lo delata
        await su.execute("ALTER TABLE plataforma.auditoria DISABLE TRIGGER auditoria_inmutable")
        await su.execute("UPDATE plataforma.auditoria SET resultado = 'rejected' WHERE id = $1", fila["id"])
        await su.execute("ALTER TABLE plataforma.auditoria ENABLE TRIGGER auditoria_inmutable")
        v = await a.verificar()
        assert v["integra"] is False and v["primera_rota"] == fila["id"]
    finally:
        # dejar la BD de tests como estaba (la cadena es de todas las pruebas)
        await su.execute("ALTER TABLE plataforma.auditoria DISABLE TRIGGER auditoria_inmutable")
        await su.execute("UPDATE plataforma.auditoria SET resultado = 'approved' WHERE id = $1", fila["id"])
        await su.execute("ALTER TABLE plataforma.auditoria ENABLE TRIGGER auditoria_inmutable")
        await su.close()
    assert (await a.verificar())["integra"] is True
