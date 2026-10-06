-- =============================================================================
-- SEC-02 (auditoría E2E 2026-07-20): rol de solo-lectura de MÍNIMOS privilegios
-- para el SQL generado por el LLM.
-- =============================================================================
-- El SQL del LLM ya corre en `conn.transaction(readonly=True)` (bloquea
-- escrituras) + statement_timeout. Pero corría como `geo_user`, que puede LEER
-- cualquier tabla del cluster. Este rol acota la LECTURA a los esquemas de
-- dominio: `_execute_sql` hace `SET LOCAL ROLE gis_readonly` dentro de la
-- transacción, así un SELECT del LLM no puede leer tablas fuera del dominio.
--
-- Corre en volumen FRESCO (postgres entrypoint, orden alfabético: DESPUÉS del
-- restore del catastro en 01_*.sh). Para una BD YA desplegada, aplicar el mismo
-- SQL con `psql` (idempotente) — ver docker/migrations/2026-07-20_gis_readonly.sql.
-- =============================================================================

-- Rol sin login (solo se alcanza vía SET ROLE desde geo_user).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gis_readonly') THEN
        CREATE ROLE gis_readonly NOLOGIN;
    END IF;
END
$$;

-- SELECT solo sobre los esquemas de dominio geoespacial.
GRANT USAGE ON SCHEMA catastro TO gis_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA catastro TO gis_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA catastro GRANT SELECT ON TABLES TO gis_readonly;

-- public trae PostGIS (spatial_ref_sys, geometry_columns) — necesarios para las
-- funciones espaciales — y las tablas geo del dominio.
GRANT USAGE ON SCHEMA public TO gis_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO gis_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO gis_readonly;

-- geo_user (el login de la app) debe poder `SET ROLE gis_readonly`.
GRANT gis_readonly TO geo_user;
