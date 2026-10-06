#!/bin/bash
# =============================================================================
# F5 (T5.1): rol de LOGIN `mcp_lector` para el servidor MCP de SQL (postgis-mcp).
# =============================================================================
# El servidor MCP consulta el catastro con SU PROPIA credencial, no con la de la app:
# SELECT solo en el catastro (y los catálogos de PostGIS), transacciones de solo lectura
# y un tope de 20 s por sentencia. La clave sale del entorno (MCP_SQL_LECTOR_PASSWORD) por
# variable de psql, nunca interpolada ni registrada (mismo patrón que 05_geo_app_password.sh).
#
# Solo corre con el volumen vacío; para una BD ya desplegada:
#   docker/migrations/2026-09-27_mcp_lector.sh
# =============================================================================
set -euo pipefail
if [ -z "${MCP_SQL_LECTOR_PASSWORD:-}" ]; then
    echo "T5.1: MCP_SQL_LECTOR_PASSWORD no definida: el servidor MCP de SQL no podrá leer el catastro." >&2
    exit 0  # no bloquea el arranque de la BD: postgis-mcp es opcional
fi
PGPASSWORD="${POSTGRES_PASSWORD:-}" psql --no-password --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
    --set ON_ERROR_STOP=1 --set lector_password="${MCP_SQL_LECTOR_PASSWORD}" --quiet <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcp_lector') THEN
        CREATE ROLE mcp_lector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;
ALTER ROLE mcp_lector WITH LOGIN PASSWORD :'lector_password';
-- NO hereda gis_readonly: ese rol lee también ws_meta (el workspace de TODAS las sesiones,
-- que la app cruza con el catastro). El servidor MCP solo publica el catastro.
REVOKE gis_readonly FROM mcp_lector;
GRANT CONNECT ON DATABASE geo_copilot TO mcp_lector;
GRANT USAGE ON SCHEMA catastro TO mcp_lector;
GRANT SELECT ON ALL TABLES IN SCHEMA catastro TO mcp_lector;
ALTER DEFAULT PRIVILEGES IN SCHEMA catastro GRANT SELECT ON TABLES TO mcp_lector;
-- lo mínimo de PostGIS para transformar y describir: sus catálogos en public
GRANT USAGE ON SCHEMA public TO mcp_lector;
GRANT SELECT ON public.spatial_ref_sys, public.geometry_columns TO mcp_lector;
ALTER ROLE mcp_lector SET default_transaction_read_only = on;
ALTER ROLE mcp_lector SET statement_timeout = '20s';
SQL
echo "T5.1: mcp_lector listo (solo lectura del catastro)."
