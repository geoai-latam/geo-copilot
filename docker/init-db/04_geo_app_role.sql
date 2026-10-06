-- R0.6 (auditoría 2026-07-26, AUD-04): rol de aplicación NOSUPERUSER.
-- =============================================================================
-- POR QUÉ
--
-- `POSTGRES_USER=geo_user` es SUPERUSUARIO en la imagen `postgis/postgis`
-- (verificado contra el contenedor: `SELECT rolsuper ... -> t`). La app usaba
-- ese mismo rol como usuario de login para TODO, incluido el SQL que escribe el
-- LLM. Y una transacción `READ ONLY` **no contiene a un superusuario**:
-- verificado que `BEGIN READ ONLY; SELECT pg_read_file('/etc/passwd')` devuelve
-- el fichero, y que `pg_authid` expone los hashes SCRAM.
--
-- La defensa `SET LOCAL ROLE gis_readonly` existía, pero era la ÚNICA, y su
-- chequeo previo fallaba abierto: cualquier excepción transitoria lo desactivaba
-- de forma permanente y el SQL del modelo pasaba a correr como superusuario.
--
-- QUÉ HACE
--
-- Crea `geo_app`: un rol de login SIN superusuario, sin CREATEDB y sin
-- CREATEROLE, miembro de `gis_readonly`. La aplicación debe conectarse con ÉSTE.
-- `POSTGRES_USER` queda reservado para la inicialización y el mantenimiento.
--
-- Idempotente: se puede re-ejecutar sobre una BD existente.
-- Se ejecuta después de 03_gis_readonly.sql (orden alfabético del initdb).
-- =============================================================================

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'geo_app') THEN
        -- La contraseña se fija fuera de este script (ALTER ROLE ... PASSWORD)
        -- para no versionar un secreto. Sin ella el rol no puede conectarse.
        CREATE ROLE geo_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
        RAISE NOTICE 'R0.6: rol geo_app creado (sin contraseña todavía)';
    ELSE
        -- Re-aplicar los atributos por si el rol venía de otra instalación.
        ALTER ROLE geo_app NOSUPERUSER NOCREATEDB NOCREATEROLE;
        RAISE NOTICE 'R0.6: rol geo_app ya existía, atributos reafirmados';
    END IF;
END
$$;

-- Miembro de gis_readonly: es a lo que `_execute_sql` baja con SET LOCAL ROLE.
-- NOINHERIT en el rol hace que NO tenga esos permisos por defecto: sólo los
-- obtiene cuando hace el SET ROLE explícito. Eso es justo lo que queremos.
GRANT gis_readonly TO geo_app;

-- Permisos propios mínimos para operar fuera del SQL del LLM (introspección de
-- esquema, healthcheck). El acceso a datos de dominio va vía gis_readonly.
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

-- Comprobación explícita: si esto no se cumple, el bootstrap debe fallar.
DO $$
DECLARE es_super boolean;
BEGIN
    SELECT rolsuper INTO es_super FROM pg_roles WHERE rolname = 'geo_app';
    IF es_super THEN
        RAISE EXCEPTION 'R0.6: geo_app quedó como SUPERUSER — bootstrap inválido';
    END IF;
END
$$;
