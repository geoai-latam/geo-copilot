-- =============================================================================
-- Migración SEC-02 — rol gis_readonly para una BD YA desplegada.
-- =============================================================================
-- init-db/03_gis_readonly.sql solo corre en un volumen FRESCO. Para una BD
-- existente, aplicar esto (idempotente):
--
--   docker exec -i geo_copilot_db psql -U geo_user -d geo_copilot \
--     < docker/migrations/2026-07-20_gis_readonly.sql
--
-- Tras aplicarlo, reiniciar `app` para que re-chequee la disponibilidad del rol
-- (el chequeo se cachea por proceso).
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gis_readonly') THEN
        CREATE ROLE gis_readonly NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA catastro TO gis_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA catastro TO gis_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA catastro GRANT SELECT ON TABLES TO gis_readonly;

GRANT USAGE ON SCHEMA public TO gis_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO gis_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO gis_readonly;

GRANT gis_readonly TO geo_user;
