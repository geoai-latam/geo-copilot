#!/bin/bash
# =============================================================================
# R0.6 (auditoría 2026-07-26, AUD-04): fija la contraseña del rol `geo_app`.
# =============================================================================
# POR QUÉ ESTE ARCHIVO EXISTE
#
# `04_geo_app_role.sql` crea `geo_app` con `CREATE ROLE ... LOGIN` y **sin
# contraseña**, y su comentario delega el `ALTER ROLE ... PASSWORD` a "fuera de
# este script" para no versionar un secreto. Ese "fuera" no existía en ninguna
# parte del repositorio: `geo_app` aparecía sólo en el .sql y en el compose.
#
# Consecuencia medida (auditoría 2026-09-08): un rol de login sin contraseña no
# autentica por scram-sha-256, así que `app` no podía conectarse y
# `docker compose up` no levantaba. El camino de menor resistencia para el
# operador era revertir el DATABASE_URL a `geo_user` — que en la imagen
# postgis es SUPERUSUARIO — deshaciendo exactamente lo que R0.6 cerró.
#
# QUÉ HACE
#
# Lee `GEO_APP_PASSWORD` del entorno del contenedor (la inyecta el compose, y
# es la misma variable que consume el DATABASE_URL de `app`) y la aplica.
# La contraseña NO se versiona, NO viaja por la línea de comandos (que sería
# visible en `ps`) y NO se registra: se pasa a psql por stdin y por variable.
#
# Idempotente: `ALTER ROLE` se puede re-ejecutar sobre una BD existente.
# Corre después de 04 por orden alfabético del entrypoint de Postgres.
#
# OJO: los scripts de `/docker-entrypoint-initdb.d` corren SOLO cuando el
# volumen está vacío. Para una BD ya desplegada usa la migración equivalente:
#   docker/migrations/2026-07-27_geo_app_password.sh
# =============================================================================
set -euo pipefail

if [ -z "${GEO_APP_PASSWORD:-}" ]; then
    echo "R0.6: FATAL — GEO_APP_PASSWORD no está definida en el entorno del" >&2
    echo "      contenedor de Postgres. El rol geo_app quedaría sin contraseña" >&2
    echo "      y la aplicación no podría conectarse. Define la variable en" >&2
    echo "      .env (ver .env.example) y vuelve a levantar el stack." >&2
    exit 1
fi

# `--no-password` evita cualquier prompt interactivo; el entrypoint ya nos da
# conexión local por peer/trust como POSTGRES_USER.
# La contraseña entra como variable de psql (:'geo_app_password'), nunca
# interpolada en el texto del SQL por bash: así no aparece en un log de error
# de sintaxis ni en el eco del comando.
PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    --no-password \
    --username "${POSTGRES_USER}" \
    --dbname "${POSTGRES_DB}" \
    --set ON_ERROR_STOP=1 \
    --set geo_app_password="${GEO_APP_PASSWORD}" \
    --quiet \
    <<'SQL'
ALTER ROLE geo_app WITH PASSWORD :'geo_app_password';

-- Comprobación explícita: si el rol quedó sin credencial, el bootstrap debe
-- fallar aquí y no seis pasos más tarde con un "authentication failed" opaco.
DO $$
DECLARE tiene_password boolean;
BEGIN
    SELECT rolpassword IS NOT NULL INTO tiene_password
    FROM pg_authid WHERE rolname = 'geo_app';

    IF NOT tiene_password THEN
        RAISE EXCEPTION 'R0.6: geo_app sigue sin contraseña — bootstrap inválido';
    END IF;
    RAISE NOTICE 'R0.6: contraseña de geo_app fijada; la app ya puede conectarse';
END
$$;
SQL

echo "R0.6: geo_app listo (rol NOSUPERUSER con credencial)."
