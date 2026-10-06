#!/bin/bash
# =============================================================================
# Restore del dump catastral real (formato custom pg_dump).
# =============================================================================
# Postgres entrypoint ejecuta este script ANTES del .dump (orden alfabético:
# 00_postgis.sql → 01_restore_catastro.sh → ...).
#
# El .dump está en formato custom (pg_dump -F c) — no se puede ejecutar
# como SQL plain. Hay que usar pg_restore.
#
# Importante:
# - El dump trae su propio `CREATE DATABASE geocopilot` que falla porque ya
#   existe la BD geo_copilot del POSTGRES_DB. Usamos --no-owner --no-acl y
#   forzamos el target a geo_copilot con -d.
# - --clean borra objetos existentes (no aplica primera vez pero por si
#   re-corre el script manualmente).
# - Si el dump trae el rol `catastro`, lo creamos antes (no es nuestro
#   geo_user). Sin esto, GRANT-s fallan con "role does not exist".
# - F7 (auditoría): `catastro` es NOLOGIN. Solo hace falta que exista; antes
#   era LOGIN con una contraseña publicada en este repositorio, y el script
#   también corre en producción. En una BD ya creada:
#   ALTER ROLE catastro NOLOGIN PASSWORD NULL;
# =============================================================================

set -e

DUMP_FILE="/docker-entrypoint-initdb.d/02_catastro_real.dump"

echo "==> Verificando archivo de dump..."
if [ ! -f "$DUMP_FILE" ]; then
    echo "WARN: $DUMP_FILE no encontrado — saltando restore."
    exit 0
fi

echo "==> Creando rol 'catastro' si no existe (el dump lo referencia)..."
psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'catastro') THEN
            CREATE ROLE catastro NOLOGIN;
        END IF;
    END
    \$\$;
EOSQL

echo "==> pg_restore del catastro real (~500 MB, tarda varios minutos)..."
pg_restore \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --no-owner \
    --no-acl \
    --verbose \
    --jobs 2 \
    --exit-on-error \
    "$DUMP_FILE" \
    2>&1 | tail -50  # solo las últimas 50 líneas para no inundar logs

echo "==> Restore catastro completado."

# Verifica conteos básicos (smoke).
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'construcciones' AS tabla, count(*) FROM catastro.construcciones
    UNION ALL
    SELECT 'lotes', count(*) FROM catastro.lotes;
EOSQL
