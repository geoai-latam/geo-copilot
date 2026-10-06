"""F7 (S7.2): migraciones con Alembic.

Sin BD: la cadena de revisiones es lineal y los scripts de init-db se leen sin meta-órdenes de
psql. Con la BD de pruebas (`-m postgis`): en una base NUEVA crea los esquemas de la plataforma y
registra la versión. Correrla otra vez con la versión en head es un no-op de Alembic: la
re-ejecución REAL del SQL y el camino init-db → migrar están en test_f7_auditoria_migraciones.py.
"""
from __future__ import annotations

import os
import uuid

import pytest

from geo_copilot import migraciones

DUENO = os.environ.get("TEST_OWNER_DATABASE_URL", "postgresql://gc_test:gc_test_pw@localhost:5434/geocopilot_test")


def test_la_cadena_de_revisiones_es_lineal_y_llega_a_la_plataforma():
    from alembic.script import ScriptDirectory

    guion = ScriptDirectory.from_config(migraciones._config("postgresql+psycopg://x@y/z"))
    assert guion.get_heads() == ["0002_plataforma_f6"]
    cadena = [r.revision for r in guion.walk_revisions()]
    assert cadena == ["0002_plataforma_f6", "0001_base_workspace"]


@pytest.mark.parametrize("nombre", ["06_workspace.sql", "10_plataforma.sql"])
def test_los_scripts_de_init_db_se_leen_sin_meta_ordenes_de_psql(nombre):
    texto = migraciones.script_sql(nombre)
    assert texto.strip() and not any(linea.lstrip().startswith("\\") for linea in texto.splitlines())


@pytest.mark.postgis
def test_en_una_base_nueva_crea_la_plataforma_y_registra_la_version():
    psycopg = pytest.importorskip("psycopg")
    base = f"mig_{uuid.uuid4().hex[:8]}"
    try:
        # con tope: sin él, un puerto que acepta y no responde cuelga la suite en vez de saltar
        admin = psycopg.connect(DUENO, autocommit=True, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — sin BD de pruebas
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            pytest.fail(f"REQUIRE_TEST_DB=1 y no hay BD: {exc}")
        pytest.skip(f"sin BD de pruebas: {exc}")
    try:
        admin.execute(f'CREATE DATABASE "{base}"')
        url = DUENO.rsplit("/", 1)[0] + f"/{base}"
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        destino = url.replace("postgresql://", "postgresql+psycopg://", 1)
        migraciones.migrar(destino)
        migraciones.migrar(destino)  # otra vez: Alembic no corre nada (versión ya en head)
        with psycopg.connect(url) as c:
            esquemas = {f[0] for f in c.execute(
                "select nspname from pg_namespace where nspname in ('ws_meta','plataforma')")}
            version = c.execute("select version_num from alembic_version").fetchone()[0]
            tablas = c.execute("select count(*) from information_schema.tables "
                               "where table_schema='plataforma'").fetchone()[0]
        assert esquemas == {"ws_meta", "plataforma"} and version == "0002_plataforma_f6" and tablas >= 4
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)')
        admin.close()
