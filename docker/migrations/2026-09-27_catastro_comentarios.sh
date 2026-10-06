#!/bin/sh
# Migración F5 — comentarios de alcance de las tablas del catastro para una BD YA desplegada.
# Aplica el mismo archivo de init-db (idempotente), no una copia:
#   sh docker/migrations/2026-09-27_catastro_comentarios.sh
set -eu
docker exec -i geo_copilot_db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$(dirname "$0")/../init-db/09_catastro_comentarios.sql"
