"""Migraciones del esquema de la plataforma (F7, S7.2) con Alembic.

Qué gestiona: los esquemas que son de la APLICACIÓN (workspace `ws_meta`/roles de escritura y
`plataforma`: dueños de sesión, conexiones por organización, pins, auditoría). No gestiona los
DATOS de dominio (el catastro lo carga `docker/init-db`) ni los roles con contraseña (03–05 de
`init-db`), que dependen de secretos del despliegue.

Antes: `init-db` solo corría en un volumen nuevo y cada cambio llegaba como un script de shell
con `docker exec` que alguien tenía que acordarse de correr. Ahora hay una versión registrada
(`alembic_version`) y el despliegue corre `python -m geo_copilot.migraciones` antes de la app:

    MIGRACIONES_DATABASE_URL=postgresql+psycopg://<dueño>:<clave>@postgis:5432/geo_copilot \\
        python -m geo_copilot.migraciones            # upgrade head
    python -m geo_copilot.migraciones actual         # qué versión tiene la BD

Cada revisión lleva CONGELADO el SQL que ejecuta (`SQL`): «0002» significa siempre lo mismo y la
imagen no depende de archivos externos. Las revisiones base son la copia de los SQL idempotentes
de `docker/init-db` (06 y 10): en una BD que init-db ya creó no cambian nada; en una existente,
la ponen al día.

F7 (auditoría): `init-db` sigue creando los volúmenes NUEVOS, así que cambiar 06/10 ya no basta
(una BD que está en head no vuelve a correr nada). Cada revisión declara en `INIT_DB` la huella
del SQL de init-db que deja aplicado; si cambias 06/10, crea una revisión nueva que lleve ese
cambio a las BD existentes y declare la huella nueva (`init_db_sin_revision()` y los tests lo
exigen). La huella es la del SQL efectivo: cambiar solo comentarios `--` no exige revisión.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
# Solo existe en el repo (los tests y quien escribe una revisión nueva): la imagen no lo necesita
INIT_DB = AQUI.parents[2] / "docker" / "init-db"


def script_sql(nombre: str, directorio: Path = INIT_DB) -> str:
    """El SQL de `init-db` sin las meta-órdenes de psql (`\\set …`) y con saltos de línea `\\n`."""
    texto = (directorio / nombre).read_text(encoding="utf-8")
    return "\n".join(linea for linea in texto.splitlines() if not linea.lstrip().startswith("\\"))


def huella(sql: str) -> str:
    """sha256 del SQL EFECTIVO: sin las líneas que son solo un comentario `--` ni las vacías, y sin
    depender de CRLF/LF (el checkout de Windows convierte los saltos).

    F7 (auditoría): antes cubría el archivo entero y corregir un comentario de 06/10 exigía una
    revisión nueva; las cabeceras se quedaban desfasadas para no tener que hacerla. Un comentario al
    final de una línea con SQL sí cuenta (quitarlo exigiría entender cadenas y `$$`)."""
    efectivas = (linea for linea in sql.splitlines() if linea.strip() and not linea.lstrip().startswith("--"))
    return hashlib.sha256("\n".join(efectivas).encode("utf-8")).hexdigest()


def ejecutar_sql(sql: str) -> None:
    """Dentro de una revisión: el SQL congelado entero por la conexión psycopg cruda (protocolo
    simple: admite varias sentencias y bloques `DO $$`; SQLAlchemy pasaría parámetros)."""
    from alembic import op

    op.get_bind().connection.dbapi_connection.execute(sql)  # type: ignore[union-attr]


def _config(url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(AQUI))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def huellas_registradas() -> dict[str, str]:
    """Por archivo de init-db, la huella que declara la ÚLTIMA revisión que lo recoge."""
    from alembic.script import ScriptDirectory

    guion = ScriptDirectory.from_config(_config("postgresql+psycopg://x@y/z"))
    huellas: dict[str, str] = {}
    for rev in reversed(list(guion.walk_revisions())):  # de la base a head
        huellas.update(getattr(rev.module, "INIT_DB", {}))
    return huellas


def init_db_sin_revision(directorio: Path = INIT_DB) -> list[str]:
    """Los SQL de init-db que cambiaron sin una revisión que lleve el cambio a las BD existentes."""
    return sorted(nombre for nombre, registrada in huellas_registradas().items()
                  if huella(script_sql(nombre, directorio)) != registrada)


def url_de_entorno() -> str:
    url = os.environ.get("MIGRACIONES_DATABASE_URL", "")
    if not url:
        raise SystemExit("define MIGRACIONES_DATABASE_URL (un rol dueño de los esquemas, no geo_app)")
    # un DSN postgresql:// se usa con psycopg 3 (ejecuta los scripts con varias sentencias)
    return url.replace("postgresql://", "postgresql+psycopg://", 1) if url.startswith("postgresql://") else url


def migrar(url: str, destino: str = "head") -> None:
    from alembic import command

    command.upgrade(_config(url), destino)


def main(argv: list[str] | None = None) -> None:
    import logging

    from alembic import command

    # sin esto Alembic no dice nada: el runbook verifica «Running upgrade …» en los logs de `migrar`
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

    args = list(sys.argv[1:] if argv is None else argv)
    orden = args[0] if args else "upgrade"
    cfg = _config(url_de_entorno())
    if orden == "upgrade":
        command.upgrade(cfg, args[1] if len(args) > 1 else "head")
    elif orden == "actual":
        command.current(cfg, verbose=True)
    elif orden == "historia":
        command.history(cfg)
    else:
        raise SystemExit(f"orden desconocida {orden!r} (upgrade [destino] | actual | historia)")
