"""F6: esquema `plataforma` (dueños de sesión, conexiones por organización, pins, auditoría).

Revision ID: 0002_plataforma_f6
Revises: 0001_base_workspace
Create Date: 2026-09-28
"""
from geo_copilot.migraciones import ejecutar_sql

revision = "0002_plataforma_f6"
down_revision = "0001_base_workspace"
branch_labels = None
depends_on = None

# F7 (auditoría): congelado; 10_plataforma.sql puede cambiar después, esta revisión no
INIT_DB = {"10_plataforma.sql": "d265af916fcbdbe4a55d70574f33c7b57867b7db52d3a9b7b8ee93d4d6db8546"}


def upgrade() -> None:
    ejecutar_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("las migraciones de la plataforma no se deshacen: se corrigen con otra revisión")


# Copia de docker/init-db/10_plataforma.sql (sin `\set`) tal como estaba en esta revisión.
SQL = r"""
-- F6 — identidad, conexiones por organización y auditoría (esquema `plataforma`).
--
--   geo_app (LOGIN, NOINHERIT) --SET LOCAL ROLE--> geo_plataforma (lee/escribe lo permitido)
--   geo_plataforma_dueno (NOLOGIN) es el DUEÑO del esquema y la app NO es miembro: no puede
--   alterar tablas, ni saltarse el «solo añadir» de la auditoría.
--
--   plataforma.sesiones      de quién es cada sesión/workspace (S6.1)
--   plataforma.conexiones    servidores MCP de cada organización con su secreto cifrado (S6.2)
--   plataforma.pins          huella aprobada de cada tool por organización (S6.2)
--   plataforma.auditoria     quién ejecutó y aprobó qué; solo añadir, encadenada por hash (S6.4)
--
-- Además `ws_meta.proyectos` gana dueño (owner_sub, org_id).
--
-- Idempotente: lo corre el initdb, el compose de tests y docker/migrations/2026-09-28_plataforma.sh.


DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'geo_plataforma_dueno') THEN
        CREATE ROLE geo_plataforma_dueno NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'geo_plataforma') THEN
        CREATE ROLE geo_plataforma NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

-- geo_app lo crea init-db/04 (en la BD de tests el rol de la app es otro y lo concede su fixture)
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'geo_app') THEN
        GRANT geo_plataforma TO geo_app;
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS plataforma AUTHORIZATION geo_plataforma_dueno;
ALTER SCHEMA plataforma OWNER TO geo_plataforma_dueno;
REVOKE ALL ON SCHEMA plataforma FROM PUBLIC;
GRANT USAGE ON SCHEMA plataforma TO geo_plataforma;

-- ---------------------------------------------------------------------------
-- Sesiones con dueño
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plataforma.sesiones (
    session_id  text PRIMARY KEY CHECK (session_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    owner_sub   text NOT NULL,
    org_id      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE plataforma.sesiones OWNER TO geo_plataforma_dueno;
CREATE INDEX IF NOT EXISTS sesiones_owner_idx ON plataforma.sesiones (owner_sub);
GRANT SELECT, INSERT ON plataforma.sesiones TO geo_plataforma;   -- el dueño no cambia nunca

-- ---------------------------------------------------------------------------
-- Conexiones MCP por organización
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plataforma.conexiones (
    org_id      text NOT NULL,
    id          text NOT NULL CHECK (id ~ '^[a-z][a-z0-9_]{0,30}$'),
    config      jsonb NOT NULL,          -- ServerConfig SIN el secreto
    secreto     bytea,                   -- cifrado (envelope, ver platform/conexiones/cifrado.py)
    habilitada  boolean NOT NULL DEFAULT true,
    creada_por  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, id)
);
ALTER TABLE plataforma.conexiones OWNER TO geo_plataforma_dueno;
GRANT SELECT, INSERT, UPDATE, DELETE ON plataforma.conexiones TO geo_plataforma;

CREATE TABLE IF NOT EXISTS plataforma.pins (
    org_id       text NOT NULL,
    servidor     text NOT NULL,
    tool         text NOT NULL,
    huella       text NOT NULL,
    aprobada_por text NOT NULL,
    aprobada_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, servidor, tool)
);
ALTER TABLE plataforma.pins OWNER TO geo_plataforma_dueno;
GRANT SELECT, INSERT, UPDATE, DELETE ON plataforma.pins TO geo_plataforma;

-- ---------------------------------------------------------------------------
-- Auditoría: solo añadir, encadenada por hash
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plataforma.auditoria (
    id            bigserial PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT clock_timestamp(),
    org_id        text NOT NULL,
    actor_sub     text NOT NULL,
    actor_nombre  text NOT NULL DEFAULT '',
    actor_via     text NOT NULL,
    accion        text NOT NULL,
    recurso       text NOT NULL,
    session_id    text,
    resultado     text NOT NULL,
    detalle       jsonb NOT NULL DEFAULT '{}'::jsonb,
    hash_prev     text NOT NULL DEFAULT '',
    hash          text NOT NULL DEFAULT ''
);
ALTER TABLE plataforma.auditoria OWNER TO geo_plataforma_dueno;
ALTER SEQUENCE plataforma.auditoria_id_seq OWNER TO geo_plataforma_dueno;
CREATE INDEX IF NOT EXISTS auditoria_org_ts_idx ON plataforma.auditoria (org_id, ts DESC);
REVOKE ALL ON plataforma.auditoria FROM geo_plataforma;
GRANT SELECT, INSERT ON plataforma.auditoria TO geo_plataforma;
GRANT USAGE ON SEQUENCE plataforma.auditoria_id_seq TO geo_plataforma;

-- El hash de cada fila cubre su contenido y el hash de la anterior: borrar o cambiar una fila
-- directamente en la BD (p. ej. un superusuario) rompe la cadena y la verificación lo detecta.
-- El cerrojo serializa las inserciones para que la cadena no se bifurque.
CREATE OR REPLACE FUNCTION plataforma.auditoria_encadenar() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, plataforma AS $$
DECLARE previo text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('plataforma.auditoria'));
    SELECT a.hash INTO previo FROM plataforma.auditoria a ORDER BY a.id DESC LIMIT 1;
    NEW.hash_prev := coalesce(previo, '');
    NEW.hash := encode(sha256(convert_to(
        NEW.hash_prev || '|' || NEW.id::text || '|' || to_char(NEW.ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' ||
        NEW.org_id || '|' || NEW.actor_sub || '|' || NEW.actor_via || '|' || NEW.accion || '|' ||
        NEW.recurso || '|' || coalesce(NEW.session_id, '') || '|' || NEW.resultado || '|' || NEW.detalle::text,
        'UTF8')), 'hex');
    RETURN NEW;
END
$$;
ALTER FUNCTION plataforma.auditoria_encadenar() OWNER TO geo_plataforma_dueno;

CREATE OR REPLACE FUNCTION plataforma.auditoria_inmutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'plataforma.auditoria es de solo añadir (% rechazado)', TG_OP;
END
$$;
ALTER FUNCTION plataforma.auditoria_inmutable() OWNER TO geo_plataforma_dueno;

DROP TRIGGER IF EXISTS auditoria_encadenar ON plataforma.auditoria;
CREATE TRIGGER auditoria_encadenar BEFORE INSERT ON plataforma.auditoria
    FOR EACH ROW EXECUTE FUNCTION plataforma.auditoria_encadenar();
DROP TRIGGER IF EXISTS auditoria_inmutable ON plataforma.auditoria;
CREATE TRIGGER auditoria_inmutable BEFORE UPDATE OR DELETE ON plataforma.auditoria
    FOR EACH ROW EXECUTE FUNCTION plataforma.auditoria_inmutable();
DROP TRIGGER IF EXISTS auditoria_sin_truncate ON plataforma.auditoria;
CREATE TRIGGER auditoria_sin_truncate BEFORE TRUNCATE ON plataforma.auditoria
    FOR EACH STATEMENT EXECUTE FUNCTION plataforma.auditoria_inmutable();

-- ---------------------------------------------------------------------------
-- Proyectos con dueño
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'ws_meta' AND tablename = 'proyectos') THEN
        ALTER TABLE ws_meta.proyectos ADD COLUMN IF NOT EXISTS owner_sub text;
        ALTER TABLE ws_meta.proyectos ADD COLUMN IF NOT EXISTS org_id text;
        CREATE INDEX IF NOT EXISTS proyectos_owner_idx ON ws_meta.proyectos (owner_sub);
    END IF;
END
$$;
"""
