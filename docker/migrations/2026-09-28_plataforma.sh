#!/bin/sh
# Migración F6 — esquema `plataforma` (sesiones con dueño, conexiones por organización,
# pins, auditoría encadenada) y dueño en ws_meta.proyectos, para una BD YA desplegada.
#
# init-db/10_plataforma.sql solo corre en un volumen FRESCO. Es idempotente, así que la
# migración es aplicar ese mismo archivo:
#
#   sh docker/migrations/2026-09-28_plataforma.sh
#
# Los proyectos anteriores a F6 quedan sin dueño (owner_sub NULL): nadie los ve hasta que
# un administrador los asigne (UPDATE ws_meta.proyectos SET owner_sub=…, org_id=…).
#
# F7 (auditoría): HISTÓRICO. Desde F7 el esquema de la plataforma lo versiona Alembic: usar
# `python -m geo_copilot.migraciones` (en producción, el servicio `migrar`), que además registra
# la versión en `alembic_version`. Este script no la registra, y si 10_plataforma.sql cambia,
# el cambio llega a las BD existentes por una revisión nueva (src/geo_copilot/migraciones/versions),
# no por aquí. Ver docs/sistema/09-configuracion-y-deploy.md §9.5.
set -eu
docker exec -i geo_copilot_db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$(dirname "$0")/../init-db/10_plataforma.sql"
