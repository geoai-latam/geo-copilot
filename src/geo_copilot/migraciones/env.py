"""Entorno de Alembic: migraciones en línea contra la URL de `Config` (sin modelos ORM)."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _en_linea() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise SystemExit("falta la URL de la base de datos (MIGRACIONES_DATABASE_URL)")
    motor = create_engine(url, poolclass=pool.NullPool)
    with motor.connect() as conexion:
        context.configure(connection=conexion, target_metadata=None, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("las migraciones se aplican en línea contra la BD")
_en_linea()
