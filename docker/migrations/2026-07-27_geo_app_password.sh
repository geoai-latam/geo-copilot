#!/bin/bash
# =============================================================================
# Migración R0.6 (AUD-04) — contraseña de `geo_app` en una BD YA desplegada.
# =============================================================================
# `init-db/04_geo_app_role.sql` y `init-db/05_geo_app_password.sh` sólo corren
# sobre un volumen FRESCO. Para una base de datos existente, aplica esto desde
# el host (idempotente):
#
#   GEO_APP_PASSWORD='...' ./docker/migrations/2026-07-27_geo_app_password.sh
#
# O, si prefieres tomarla del .env que ya usa el compose:
#
#   set -a; . ./.env; set +a
#   ./docker/migrations/2026-07-27_geo_app_password.sh
#
# Tras aplicarlo, reinicia `app` para que tome el DATABASE_URL con `geo_app`.
#
# Es un .sh y no un .sql a propósito: un `.sql` versionado tendría que llevar
# la contraseña escrita dentro, que es justo lo que R0.6 evita.
# =============================================================================
set -euo pipefail

CONTENEDOR="${GEO_COPILOT_DB_CONTAINER:-geo_copilot_db}"
USUARIO="${POSTGRES_USER:-geo_user}"
BASE="${POSTGRES_DB:-geo_copilot}"

if [ -z "${GEO_APP_PASSWORD:-}" ]; then
    echo "FATAL: define GEO_APP_PASSWORD antes de correr esta migración." >&2
    echo "       Debe ser la MISMA que consume el DATABASE_URL de \`app\`." >&2
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${CONTENEDOR}"; then
    echo "FATAL: el contenedor '${CONTENEDOR}' no está corriendo." >&2
    echo "       Levántalo o exporta GEO_COPILOT_DB_CONTAINER con su nombre." >&2
    exit 1
fi

# La contraseña viaja por stdin como variable de psql, no por argv (que sería
# visible en `ps` del host y del contenedor) ni interpolada en el texto SQL.
docker exec -i \
    -e "GEO_APP_PASSWORD=${GEO_APP_PASSWORD}" \
    "${CONTENEDOR}" \
    psql --username "${USUARIO}" --dbname "${BASE}" \
         --set ON_ERROR_STOP=1 --quiet <<'SQL'
\set geo_app_password `echo "$GEO_APP_PASSWORD"`

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'geo_app') THEN
        CREATE ROLE geo_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
        RAISE NOTICE 'R0.6: rol geo_app creado';
    ELSE
        ALTER ROLE geo_app NOSUPERUSER NOCREATEDB NOCREATEROLE;
        RAISE NOTICE 'R0.6: rol geo_app ya existía, atributos reafirmados';
    END IF;
END
$$;

ALTER ROLE geo_app WITH PASSWORD :'geo_app_password';

GRANT gis_readonly TO geo_app;
GRANT CONNECT ON DATABASE geo_copilot TO geo_app;
GRANT USAGE ON SCHEMA public TO geo_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO geo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO geo_app;

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_namespace WHERE nspname = 'catastro') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA catastro TO geo_app';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA catastro TO geo_app';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA catastro GRANT SELECT ON TABLES TO geo_app';
    END IF;
END
$$;

-- Las dos comprobaciones que hacen que esto falle ruidosamente en vez de
-- dejar un rol inservible o, peor, un superusuario.
DO $$
DECLARE es_super boolean; tiene_password boolean;
BEGIN
    SELECT rolsuper INTO es_super FROM pg_roles WHERE rolname = 'geo_app';
    IF es_super THEN
        RAISE EXCEPTION 'R0.6: geo_app quedó como SUPERUSER — migración inválida';
    END IF;

    SELECT rolpassword IS NOT NULL INTO tiene_password
    FROM pg_authid WHERE rolname = 'geo_app';
    IF NOT tiene_password THEN
        RAISE EXCEPTION 'R0.6: geo_app sigue sin contraseña — migración inválida';
    END IF;

    RAISE NOTICE 'R0.6: geo_app listo (NOSUPERUSER, con credencial)';
END
$$;
SQL

echo "R0.6: migración aplicada. Reinicia \`app\` para que use geo_app."
