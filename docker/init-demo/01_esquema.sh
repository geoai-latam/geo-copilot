#!/bin/sh
# postgis-demo (F5, T5.1): segunda BD PostGIS de referencia para el servidor MCP de SQL.
# Datos reales de otra temática: sedes educativas de Cundinamarca (IGAC, ArcGIS Hub; sin
# datos de contacto). Rol `lector` de SOLO LECTURA con la clave del entorno (nunca en el repo).
set -eu
: "${DEMO_LECTOR_PASSWORD:?falta DEMO_LECTOR_PASSWORD}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS educacion;
COMMENT ON SCHEMA educacion IS 'Sedes educativas de Cundinamarca (IGAC, datos abiertos)';
CREATE TABLE educacion.sedes_educativas (
  fid serial PRIMARY KEY,
  cod_col text, nom_col text, dir_col text, codigo_mun text, nombre_mun text,
  zona text, cod_inst text, nom_inst text, sector text,
  geom geometry(Point, 4326) NOT NULL
);
COMMENT ON TABLE educacion.sedes_educativas IS 'Sedes educativas oficiales y no oficiales de Cundinamarca: nombre, dirección, municipio, zona (urbana/rural), sector (oficial/no oficial)';
CREATE INDEX ON educacion.sedes_educativas USING gist (geom);
CREATE ROLE lector LOGIN PASSWORD '${DEMO_LECTOR_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE lector SET default_transaction_read_only = on;
ALTER ROLE lector SET statement_timeout = '20s';
GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO lector;
GRANT USAGE ON SCHEMA educacion TO lector;
GRANT SELECT ON ALL TABLES IN SCHEMA educacion TO lector;
ALTER DEFAULT PRIVILEGES IN SCHEMA educacion GRANT SELECT ON TABLES TO lector;
SQL
