"""F7 (auditoría), grupo migraciones: hallazgos #39, #40, #42, #57 y #82.

- #39: cada revisión lleva su SQL congelado y declara la huella del SQL de init-db que recoge;
  cambiar 06/10 sin una revisión nueva se detecta.
- #82: las revisiones no leen archivos (ni MIGRACIONES_SQL_DIR, ni copias en la imagen).
- #42: la app ya no ejecuta DDL sobre `ws_meta.proyectos` en cada guardar/listar/abrir.
- #40/#57 (`-m postgis`): el camino real de producción —init-db crea la BD y luego `migrar`— y
  una re-ejecución REAL de las revisiones (no el no-op de Alembic con la versión ya en head).
"""
from __future__ import annotations

import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from geo_copilot import migraciones

DUENO = os.environ.get("TEST_OWNER_DATABASE_URL", "postgresql://gc_test:gc_test_pw@localhost:5434/geocopilot_test")

# Lo de init-db que NO es esquema de la aplicación: datos de dominio y roles con contraseña
# (dependen de secretos del despliegue). Todo lo demás debe recogerlo una revisión.
FUERA_DE_MIGRACIONES = {
    "00_postgis.sql", "01_restore_catastro.sh", "02_catastro_real.dump", "02_seed_demo.sql",
    "03_gis_readonly.sql", "04_geo_app_role.sql", "05_geo_app_password.sh", "07_srid_colombia.sql",
    "08_mcp_lector.sh", "09_catastro_comentarios.sql", "README.md",
}


def _revisiones():
    from alembic.script import ScriptDirectory

    guion = ScriptDirectory.from_config(migraciones._config("postgresql+psycopg://x@y/z"))
    return list(reversed(list(guion.walk_revisions())))  # de la base a head


# --------------------------------------------------------------------------- #39 / #82


def test_init_db_06_y_10_estan_recogidos_por_una_revision():
    assert set(migraciones.huellas_registradas()) == {"06_workspace.sql", "10_plataforma.sql"}
    assert migraciones.init_db_sin_revision() == [], (
        "cambiaste un SQL de docker/init-db sin una revisión que lleve el cambio a las BD existentes: "
        "crea versions/000N_… con el SQL y declara en INIT_DB la huella nueva")


def test_un_cambio_en_init_db_sin_revision_nueva_se_detecta(tmp_path: Path):
    for nombre in ("06_workspace.sql", "10_plataforma.sql"):
        shutil.copy(migraciones.INIT_DB / nombre, tmp_path / nombre)
    assert migraciones.init_db_sin_revision(tmp_path) == []

    destino = tmp_path / "10_plataforma.sql"
    destino.write_text(destino.read_text(encoding="utf-8")
                       + "\nALTER TABLE plataforma.conexiones ADD COLUMN IF NOT EXISTS expira_en timestamptz;\n",
                       encoding="utf-8")
    assert migraciones.init_db_sin_revision(tmp_path) == ["10_plataforma.sql"]


def test_la_huella_no_depende_de_los_saltos_de_linea(tmp_path: Path):
    # el checkout de Windows (autocrlf) no puede hacer saltar la guarda
    texto = (migraciones.INIT_DB / "06_workspace.sql").read_text(encoding="utf-8")
    (tmp_path / "06_workspace.sql").write_bytes(texto.replace("\n", "\r\n").encode("utf-8"))
    shutil.copy(migraciones.INIT_DB / "10_plataforma.sql", tmp_path / "10_plataforma.sql")
    assert migraciones.init_db_sin_revision(tmp_path) == []


def test_un_cambio_solo_de_comentarios_no_exige_revision(tmp_path: Path):
    """Las cabeceras de 06/10 se quedaban desfasadas porque corregirlas pedía una revisión nueva."""
    for nombre in ("06_workspace.sql", "10_plataforma.sql"):
        shutil.copy(migraciones.INIT_DB / nombre, tmp_path / nombre)
    destino = tmp_path / "10_plataforma.sql"
    texto = destino.read_text(encoding="utf-8")
    destino.write_text("-- cabecera reescrita\n--\n\n" + texto.replace("\n--", "\n-- (otra redacción)", 3)
                       + "\n   -- nota final\n\n", encoding="utf-8")
    assert migraciones.init_db_sin_revision(tmp_path) == []
    # una línea de SQL sí cuenta, aunque se disfrace con un comentario al final
    destino.write_text(texto + "\nGRANT USAGE ON SCHEMA plataforma TO PUBLIC;  -- solo un comentario\n",
                       encoding="utf-8")
    assert migraciones.init_db_sin_revision(tmp_path) == ["10_plataforma.sql"]


def test_las_revisiones_base_declaran_la_huella_de_su_propio_sql_congelado():
    """0001/0002 son copia de 06/10: la huella que declaran es la de lo que ejecutan."""
    for rev in _revisiones():
        if rev.revision in ("0001_base_workspace", "0002_plataforma_f6"):
            ((nombre, declarada),) = rev.module.INIT_DB.items()
            assert migraciones.huella(rev.module.SQL) == declarada, nombre


def test_todo_init_db_es_dominio_o_lo_recoge_una_revision():
    presentes = {p.name for p in migraciones.INIT_DB.iterdir() if p.is_file()}
    sin_clasificar = presentes - FUERA_DE_MIGRACIONES - set(migraciones.huellas_registradas())
    assert not sin_clasificar, f"archivo nuevo en init-db sin revisión ni clasificar como dominio: {sin_clasificar}"


def test_las_revisiones_ejecutan_su_sql_congelado_sin_leer_archivos(monkeypatch):
    ejecutado: list[str] = []
    revisiones = _revisiones()

    def _no_leas(*_a, **_k):
        raise AssertionError("una revisión no debe leer archivos: su SQL va congelado dentro")

    monkeypatch.setattr(Path, "read_text", _no_leas)
    monkeypatch.setattr(Path, "read_bytes", _no_leas)
    monkeypatch.setenv("MIGRACIONES_SQL_DIR", "/no/existe")  # #82: ya no hay a qué caer
    for rev in revisiones:
        monkeypatch.setattr(rev.module, "ejecutar_sql", ejecutado.append)
        rev.module.upgrade()
    assert ejecutado == [rev.module.SQL for rev in revisiones]
    for sql in ejecutado:
        assert sql.strip() and not any(linea.lstrip().startswith("\\") for linea in sql.splitlines())
    # lo que la app da por hecho al no crear nada en tiempo de ejecución (#42)
    assert "CREATE TABLE IF NOT EXISTS ws_meta.proyectos" in ejecutado[0]
    assert "proyectos_owner_idx" in ejecutado[1] and "ADD COLUMN IF NOT EXISTS owner_sub" in ejecutado[1]


# --------------------------------------------------------------------------- #42


class _Conn:
    def __init__(self, ejecutadas: list[str]):
        self.ejecutadas = ejecutadas

    async def execute(self, sql, *args):
        self.ejecutadas.append(sql)

    async def fetchrow(self, sql, *args):
        self.ejecutadas.append(sql)
        ahora = datetime.now(UTC)
        if sql.lstrip().startswith("INSERT"):
            return {"id": "pr_1", "nombre": args[2], "workspace_id": args[1], "updated_at": ahora}
        return {"id": "pr_1", "nombre": "Finca", "workspace_id": "s1", "estado": "{}",
                "updated_at": ahora, "owner_sub": "ana"}

    async def fetch(self, sql, *args):
        self.ejecutadas.append(sql)
        return []

    @asynccontextmanager
    async def transaction(self):
        yield


class _Pool:
    def __init__(self):
        self.ejecutadas: list[str] = []

    @asynccontextmanager
    async def acquire(self):
        yield _Conn(self.ejecutadas)


async def test_los_proyectos_no_ejecutan_ddl_en_tiempo_de_ejecucion():
    from geo_copilot.platform.workspace import DatasetStore

    pool = _Pool()
    store = DatasetStore(pool)
    await store.guardar_proyecto("s1", "Finca", {"capas": []}, owner_sub="ana", org_id="acme")
    await store.listar_proyectos("ana")
    assert (await store.obtener_proyecto("pr_1"))["owner_sub"] == "ana"
    ddl = [s for s in pool.ejecutadas if any(k in s.upper() for k in ("CREATE TABLE", "ALTER TABLE"))]
    assert ddl == [], f"DDL en caliente (bloqueo ACCESS EXCLUSIVE en cada llamada): {ddl}"


# --------------------------------------------------------------------------- #40 / #57 (BD real)


@pytest.fixture
def base_nueva():
    """Una BD vacía (con postgis) en el clúster de pruebas; se borra al terminar."""
    psycopg = pytest.importorskip("psycopg")
    try:
        # con tope: sin él, un puerto que acepta y no responde cuelga la suite en vez de saltar
        admin = psycopg.connect(DUENO, autocommit=True, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — sin BD de pruebas
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            pytest.fail(f"REQUIRE_TEST_DB=1 y no hay BD: {exc}")
        pytest.skip(f"sin BD de pruebas: {exc}")
    base = f"mig_{uuid.uuid4().hex[:8]}"
    try:
        admin.execute(f'CREATE DATABASE "{base}"')
        url = DUENO.rsplit("/", 1)[0] + f"/{base}"
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        yield url
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)')
        admin.close()


def _estado(url: str) -> tuple[str, int, bool]:
    import psycopg

    with psycopg.connect(url) as c:
        version = c.execute("select version_num from alembic_version").fetchone()[0]
        tablas = c.execute("select count(*) from information_schema.tables "
                           "where table_schema='plataforma'").fetchone()[0]
        indice = c.execute("select exists (select from pg_indexes where schemaname='ws_meta' "
                           "and indexname='proyectos_owner_idx')").fetchone()[0]
    return version, tablas, indice


@pytest.mark.postgis
def test_init_db_primero_y_luego_migrar_como_en_un_despliegue_limpio(base_nueva):
    import psycopg

    # lo que hace el entrypoint de postgres con docker/init-db en un volumen nuevo
    with psycopg.connect(base_nueva, autocommit=True) as c:
        for nombre in ("06_workspace.sql", "10_plataforma.sql"):
            c.execute(migraciones.script_sql(nombre))
    # y después el servicio `migrar`, sobre una BD con los esquemas pero sin alembic_version
    migraciones.migrar(base_nueva.replace("postgresql://", "postgresql+psycopg://", 1))
    assert _estado(base_nueva) == ("0002_plataforma_f6", 4, True)


@pytest.mark.postgis
def test_las_revisiones_se_pueden_reejecutar_de_verdad(base_nueva):
    import psycopg

    destino = base_nueva.replace("postgresql://", "postgresql+psycopg://", 1)
    migraciones.migrar(destino)
    # sin la versión registrada Alembic vuelve a correr 0001 y 0002 enteras (no un no-op)
    with psycopg.connect(base_nueva, autocommit=True) as c:
        c.execute("DROP TABLE alembic_version")
    migraciones.migrar(destino)
    assert _estado(base_nueva) == ("0002_plataforma_f6", 4, True)


def test_la_guia_de_migraciones_de_desarrollo_apunta_a_su_postgis():
    """docs/09 §9.5: el PostGIS de desarrollo está en 5433 (5432 suele ser un Postgres nativo) y la
    URL se exporta para las dos órdenes (`actual` sin ella sale con «define MIGRACIONES_DATABASE_URL»)."""
    doc = (migraciones.INIT_DB.parents[1] / "docs" / "sistema" / "09-configuracion-y-deploy.md").read_text(
        encoding="utf-8")
    seccion = doc[doc.index("## 9.5"):doc.index("## 9.6")]
    assert "export MIGRACIONES_DATABASE_URL=postgresql://geo_user:<POSTGRES_PASSWORD>@localhost:5433/" in seccion
    assert "@localhost:5432/" not in seccion
    for archivo in ("08_mcp_lector.sh", "09_catastro_comentarios.sql", "10_plataforma.sql", "07_srid_colombia.sql"):
        assert f"`{archivo}`" in seccion, archivo
    assert "históricos" in seccion  # los scripts de docker/migrations para 06/10
