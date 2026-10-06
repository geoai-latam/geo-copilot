-- Workspace espacial (S2.1 del plan de plataforma).
--
-- Cada sesión materializa sus resultados (capas de la BD, de ArcGIS, de un MCP,
-- de un análisis) como tablas en un esquema propio `ws_<hash>`, donde se pueden
-- cruzar en SQL con los datos de dominio. La BD de dominio sigue siendo de solo
-- lectura: SOLO el rol `geo_workspace` escribe, y solo en sus esquemas.
--
--   geo_app  (LOGIN, NOINHERIT) --SET LOCAL ROLE--> geo_workspace  (escribe ws_*)
--                               --SET LOCAL ROLE--> gis_readonly   (lee dominio + ws_*)
--
-- Idempotente: lo corre el initdb en un volumen NUEVO y el compose de tests
-- (docker-compose.test.yml lo monta). En una BD ya desplegada lo pone al día
-- Alembic (`python -m geo_copilot.migraciones`; en producción, el servicio
-- `migrar`), con la copia congelada en la revisión 0001_base_workspace.
-- Cambiar el SQL de este archivo exige una revisión nueva (docs/sistema/09 §9.5);
-- los comentarios no cuentan para su huella.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gis_readonly') THEN
        CREATE ROLE gis_readonly NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'geo_workspace') THEN
        CREATE ROLE geo_workspace NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    ELSE
        ALTER ROLE geo_workspace NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

-- Crear esquemas ws_<hash> bajo demanda. CREATE en la base permite crear
-- esquemas, no tocar los existentes: sobre `public`/`catastro` no tiene nada.
DO $$
BEGIN
    EXECUTE format('GRANT CREATE ON DATABASE %I TO geo_workspace', current_database());
END
$$;

CREATE SCHEMA IF NOT EXISTS ws_meta;
ALTER SCHEMA ws_meta OWNER TO geo_workspace;

CREATE TABLE IF NOT EXISTS ws_meta.datasets (
    id             text PRIMARY KEY,               -- ds_<hex>
    workspace_id   text NOT NULL,                  -- la sesión dueña
    schema_name    text NOT NULL,                  -- ws_<hash>
    table_name     text NOT NULL,                  -- d_<hex>
    name           text NOT NULL,
    kind           text NOT NULL,                  -- vector | table
    feature_count  integer NOT NULL DEFAULT 0,
    bytes          bigint NOT NULL DEFAULT 0,
    layer_ref      jsonb NOT NULL,                 -- el LayerRef completo (contrato)
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL
);
ALTER TABLE ws_meta.datasets OWNER TO geo_workspace;
CREATE INDEX IF NOT EXISTS datasets_workspace_idx ON ws_meta.datasets (workspace_id);
CREATE INDEX IF NOT EXISTS datasets_expires_idx ON ws_meta.datasets (expires_at);

-- FH.11: proyectos (mapa + conversación + registro) guardados de una sesión. Mientras existe el
-- proyecto, los datasets de su sesión no vencen (expires_at = infinity al guardarlo).
CREATE TABLE IF NOT EXISTS ws_meta.proyectos (
    id             text PRIMARY KEY,               -- pr_<hex>
    workspace_id   text NOT NULL,                  -- la sesión (y su esquema ws_*)
    nombre         text NOT NULL,
    estado         jsonb NOT NULL,                 -- capas, vistas, chat, registro, cámara
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, nombre)
);
ALTER TABLE ws_meta.proyectos OWNER TO geo_workspace;

-- El lector ve el catálogo (el semantic layer lo introspecciona en S2.2).
GRANT USAGE ON SCHEMA ws_meta TO gis_readonly;
GRANT SELECT ON ws_meta.datasets TO gis_readonly;

-- La app puede ASUMIR el rol de escritura del workspace (no lo hereda: geo_app
-- es NOINHERIT, así que fuera de un `SET LOCAL ROLE` sigue sin escribir nada).
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'geo_app') THEN
        GRANT geo_workspace TO geo_app;
    END IF;
END
$$;
