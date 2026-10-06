#!/bin/sh
# Migración S2.1 — workspace espacial (06) para una BD YA desplegada. No aplica 07 (EPSG:9377).
#
# init-db/06_workspace.sql solo corre en un volumen FRESCO. Es idempotente,
# así que la migración es aplicar ese mismo archivo (no una copia que pueda
# divergir):
#
#   sh docker/migrations/2026-09-25_workspace.sh
#
# Tras aplicarlo no hace falta reiniciar `app`: el workspace se usa bajo demanda.
#
# F7 (auditoría): HISTÓRICO. Desde F7 el esquema de 06 lo versiona Alembic: usar
# `python -m geo_copilot.migraciones` (en producción, el servicio `migrar`), que además registra
# la versión en `alembic_version` y trae los cambios de las revisiones nuevas. Ver
# docs/sistema/09-configuracion-y-deploy.md §9.5 (allí también cómo aplicar 07).
set -eu
docker exec -i geo_copilot_db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$(dirname "$0")/../init-db/06_workspace.sql"
