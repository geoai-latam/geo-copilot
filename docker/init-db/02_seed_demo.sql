-- =============================================================================
-- Seed de DEMOSTRACIÓN del catastro (y fixture de los tests de integración)
-- =============================================================================
-- Este archivo tiene DOS consumidores, y por eso existe una copia idéntica de
-- él en `docker/init-db/02_seed_demo.sql` (`tests/test_seed_demo.py` falla si
-- las dos divergen):
--
--   1. El stack principal (`docker/docker-compose.yml`). Un clon limpio NO
--      trae el dump real —522 MB, `.gitignore`— así que sin esto el producto
--      arranca con la base VACÍA: "consultas en lenguaje natural sobre
--      PostGIS" sin PostGIS que consultar.
--   2. El stack de tests (`docker-compose.test.yml`), montado como
--      `01_seed.sql`.
--
-- Schema VERIFICADO contra el dump real (`pg_restore --schema-only` sobre
-- docker/init-db/02_catastro_real.dump, pg_dump 18.1): columnas, tipos y el
-- CHECK de SRID coinciden. El dump sólo trae estas dos tablas:
--
--   catastro.construcciones (2.4M filas en el dump real, 25 acá)
--   catastro.lotes          (933K filas en el dump real, 8 acá)
--
-- Datos SINTÉTICOS pero realistas: coordenadas reales de Bogotá/Mosquera
-- (Cundinamarca), SRID 4326, atributos coherentes (pisos 1-15, áreas
-- razonables, tipos de uso variados). No hay datos catastrales de nadie: ni
-- propietarios, ni direcciones, ni identificadores de personas.
--
-- Cobertura, MEDIDA sobre los datos (la lista anterior estaba escrita a ojo y
-- decía 3 / 5 / 5 / 5 pisos y 4 sótanos / 3 voladizos; ninguno de esos cuatro
-- números coincidía con las filas). Contado con `count(*) FILTER`:
-- - 25 construcciones: 5 de 1 piso, 8 de 2-3, 7 de 4-8 y 5 de 10-15
-- - 13 con sótano (connsotano > 0) y 14 con voladizo (convoladiz > 0)
-- - 18 en Bogotá (código 11…) y 7 en Mosquera (código 2524…)
-- - 8 lotes: 5 en Bogotá D.C. (distrito 11) y 3 en Mosquera (distrito 25)
-- - el join por atributo `construcciones.lotecodigo = lotes.lotcodigo` da 8
--   pares, y el join espacial `ST_Within(c.shape, l.shape)` da 9: el lote
--   110040002-01 contiene DOS construcciones (110020002-001 y 110040002-001,
--   que comparten coordenada) y 16 construcciones no caen dentro de ningún
--   lote. Los dos joins NO son intercambiables, y eso se deja como está: un
--   `LEFT JOIN` espacial con huecos y un lote con dos edificios encima es lo
--   que hace demostrable un análisis geoespacial. Si lo cambias, cuadra los
--   dos números otra vez o corrige este comentario.
--
-- IDEMPOTENTE Y NO DESTRUCTIVO. Quien SÍ tenga el dump real no pierde nada ni
-- recibe filas duplicadas: el INSERT sólo corre si la tabla está vacía. Ver el
-- bloque `DO` de más abajo.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS catastro;

-- -----------------------------------------------------------------------------
-- TABLAS (idénticas al dump real)
--
-- `shape` va sin typmod —`geometry` a secas— porque así está en el dump real
-- (verificado): la restricción de SRID la pone el CHECK, no el tipo. No lo
-- "arregles" a `geometry(Polygon, 4326)`: dejaría de ser un sustituto fiel del
-- dump, y el introspector reportaría un SRID que el dump real no declara.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catastro.construcciones (
    objectid    integer PRIMARY KEY,
    concodigo   varchar(25),
    connpisos   smallint,
    contsemis   integer,
    connsotano  smallint,
    lotecodigo  varchar(12),
    conmejora   integer,
    convoladiz  integer,
    conaltura   numeric(38, 8),
    conelevaci  integer,
    shape       geometry,
    CONSTRAINT enforce_srid_shape_constr CHECK (st_srid(shape) = 4326)
);

CREATE TABLE IF NOT EXISTS catastro.lotes (
    objectid    integer PRIMARY KEY,
    lotcodigo   varchar(12),
    lotdispers  varchar(1),
    lotildispe  varchar(12),
    lotupredia  integer,
    manzcodigo  varchar(9),
    lotdistrit  integer,
    shape       geometry,
    CONSTRAINT enforce_srid_shape_lotes CHECK (st_srid(shape) = 4326)
);

-- -----------------------------------------------------------------------------
-- DATOS — construcciones
-- Coordenadas en lon/lat WGS84 (SRID 4326). Polígonos pequeños representando
-- la huella catastral de cada construcción.
--
-- El INSERT y el índice van dentro de un guard de "tabla vacía". Si la tabla YA
-- tiene filas es que el dump real se restauró en `01_restore_catastro.sh`, y
-- entonces (a) los objectid 1-25 chocarían con la PK y abortarían el initdb
-- entero —el entrypoint de postgres corre los .sql con `ON_ERROR_STOP=1`— y
-- (b) el `CREATE INDEX IF NOT EXISTS` construiría un SEGUNDO índice GiST sobre
-- 2.4M geometrías, porque el del dump se llama `sidx_17470_11` y el nombre no
-- coincide (verificado con `pg_restore --list`).
-- -----------------------------------------------------------------------------

-- Helper: insertar un cuadrado de ~10m × ~10m alrededor de (lon, lat).
-- 0.0001° ≈ 11.1 m en el ecuador. Usamos 0.00009° (~10m).

DO $seed_construcciones$
BEGIN
IF EXISTS (SELECT 1 FROM catastro.construcciones) THEN
    RAISE NOTICE 'seed: catastro.construcciones ya tiene datos, no se toca (dump real presente)';
ELSE

INSERT INTO catastro.construcciones VALUES
-- Casas de 1 piso (3) en Bogotá Sur
(1,  '110010001-001', 1, 0, 0, '110010001-01', 5000000,  0, 3.0,  100, ST_GeomFromText('POLYGON((-74.10 4.55, -74.0999 4.55, -74.0999 4.5501, -74.10 4.5501, -74.10 4.55))', 4326)),
(2,  '110010002-001', 1, 0, 0, '110010002-01', 6000000,  0, 3.2,  100, ST_GeomFromText('POLYGON((-74.11 4.56, -74.1099 4.56, -74.1099 4.5601, -74.11 4.5601, -74.11 4.56))', 4326)),
(3,  '110010003-001', 1, 0, 0, '110010003-01', 4500000,  0, 2.8,  100, ST_GeomFromText('POLYGON((-74.12 4.57, -74.1199 4.57, -74.1199 4.5701, -74.12 4.5701, -74.12 4.57))', 4326)),

-- Residencial 2-3 pisos (5) en Bogotá Centro/Norte
(4,  '110020001-001', 2, 0, 0, '110020001-01', 15000000, 1, 6.5,  120, ST_GeomFromText('POLYGON((-74.07 4.65, -74.0699 4.65, -74.0699 4.6501, -74.07 4.6501, -74.07 4.65))', 4326)),
(5,  '110020002-001', 3, 0, 0, '110020002-01', 22000000, 1, 9.8,  120, ST_GeomFromText('POLYGON((-74.06 4.66, -74.0599 4.66, -74.0599 4.6601, -74.06 4.6601, -74.06 4.66))', 4326)),
(6,  '110020003-001', 2, 0, 0, '110020003-01', 18000000, 0, 6.2,  130, ST_GeomFromText('POLYGON((-74.05 4.67, -74.0499 4.67, -74.0499 4.6701, -74.05 4.6701, -74.05 4.67))', 4326)),
(7,  '110020004-001', 3, 0, 1, '110020004-01', 28000000, 1, 9.5,  130, ST_GeomFromText('POLYGON((-74.04 4.68, -74.0399 4.68, -74.0399 4.6801, -74.04 4.6801, -74.04 4.68))', 4326)),
(8,  '110020005-001', 2, 0, 0, '110020005-01', 17000000, 0, 6.0,  130, ST_GeomFromText('POLYGON((-74.03 4.69, -74.0299 4.69, -74.0299 4.6901, -74.03 4.6901, -74.03 4.69))', 4326)),

-- Multifamiliar 4-8 pisos (5)
(9,  '110030001-001', 4, 0, 1, '110030001-01', 80000000,  1, 12.5, 140, ST_GeomFromText('POLYGON((-74.08 4.70, -74.0799 4.70, -74.0799 4.7001, -74.08 4.7001, -74.08 4.70))', 4326)),
(10, '110030002-001', 5, 0, 1, '110030002-01', 100000000, 1, 15.0, 140, ST_GeomFromText('POLYGON((-74.09 4.71, -74.0899 4.71, -74.0899 4.7101, -74.09 4.7101, -74.09 4.71))', 4326)),
(11, '110030003-001', 6, 0, 1, '110030003-01', 130000000, 1, 18.5, 150, ST_GeomFromText('POLYGON((-74.10 4.72, -74.0999 4.72, -74.0999 4.7201, -74.10 4.7201, -74.10 4.72))', 4326)),
(12, '110030004-001', 8, 0, 1, '110030004-01', 180000000, 1, 24.0, 160, ST_GeomFromText('POLYGON((-74.11 4.73, -74.1099 4.73, -74.1099 4.7301, -74.11 4.7301, -74.11 4.73))', 4326)),
(13, '110030005-001', 7, 0, 0, '110030005-01', 150000000, 0, 21.0, 150, ST_GeomFromText('POLYGON((-74.12 4.74, -74.1199 4.74, -74.1199 4.7401, -74.12 4.7401, -74.12 4.74))', 4326)),

-- Edificios altos 10-15 pisos (5)
(14, '110040001-001', 10, 1, 2, '110040001-01', 300000000, 1, 30.0, 170, ST_GeomFromText('POLYGON((-74.05 4.65, -74.0499 4.65, -74.0499 4.6501, -74.05 4.6501, -74.05 4.65))', 4326)),
(15, '110040002-001', 12, 1, 2, '110040002-01', 380000000, 1, 36.0, 170, ST_GeomFromText('POLYGON((-74.06 4.66, -74.0599 4.66, -74.0599 4.6601, -74.06 4.6601, -74.06 4.66))', 4326)),
(16, '110040003-001', 15, 1, 3, '110040003-01', 500000000, 1, 45.0, 180, ST_GeomFromText('POLYGON((-74.07 4.67, -74.0699 4.67, -74.0699 4.6701, -74.07 4.6701, -74.07 4.67))', 4326)),
(17, '110040004-001', 14, 0, 2, '110040004-01', 450000000, 1, 42.0, 180, ST_GeomFromText('POLYGON((-74.08 4.68, -74.0799 4.68, -74.0799 4.6801, -74.08 4.6801, -74.08 4.68))', 4326)),
(18, '110040005-001', 11, 0, 1, '110040005-01', 320000000, 0, 33.0, 170, ST_GeomFromText('POLYGON((-74.09 4.69, -74.0899 4.69, -74.0899 4.6901, -74.09 4.6901, -74.09 4.69))', 4326)),

-- Construcciones en Mosquera Cundinamarca (7) — más al oeste de Bogotá
(19, '252420001-001', 1, 0, 0, '252420001-01', 8000000,   0, 3.5,  100, ST_GeomFromText('POLYGON((-74.22 4.70, -74.2199 4.70, -74.2199 4.7001, -74.22 4.7001, -74.22 4.70))', 4326)),
(20, '252420002-001', 2, 0, 0, '252420002-01', 14000000,  0, 6.0,  120, ST_GeomFromText('POLYGON((-74.23 4.71, -74.2299 4.71, -74.2299 4.7101, -74.23 4.7101, -74.23 4.71))', 4326)),
(21, '252420003-001', 3, 0, 1, '252420003-01', 25000000,  1, 9.5,  130, ST_GeomFromText('POLYGON((-74.24 4.72, -74.2399 4.72, -74.2399 4.7201, -74.24 4.7201, -74.24 4.72))', 4326)),
(22, '252420004-001', 5, 0, 1, '252420004-01', 90000000,  1, 15.0, 140, ST_GeomFromText('POLYGON((-74.25 4.73, -74.2499 4.73, -74.2499 4.7301, -74.25 4.7301, -74.25 4.73))', 4326)),
(23, '252420005-001', 1, 0, 0, '252420005-01', 6500000,   0, 3.0,  100, ST_GeomFromText('POLYGON((-74.20 4.68, -74.1999 4.68, -74.1999 4.6801, -74.20 4.6801, -74.20 4.68))', 4326)),
(24, '252420006-001', 4, 0, 1, '252420006-01', 70000000,  1, 12.0, 140, ST_GeomFromText('POLYGON((-74.21 4.69, -74.2099 4.69, -74.2099 4.6901, -74.21 4.6901, -74.21 4.69))', 4326)),
(25, '252420007-001', 2, 0, 0, '252420007-01', 13000000,  0, 5.8,  120, ST_GeomFromText('POLYGON((-74.19 4.67, -74.1899 4.67, -74.1899 4.6701, -74.19 4.6701, -74.19 4.67))', 4326));

CREATE INDEX IF NOT EXISTS sidx_construcciones_shape
    ON catastro.construcciones USING gist(shape);

RAISE NOTICE 'seed: 25 construcciones de demostración insertadas';

END IF;
END
$seed_construcciones$;

-- -----------------------------------------------------------------------------
-- DATOS — lotes
-- Polígonos más grandes que las construcciones (cada lote puede tener
-- 0+ construcciones encima). Mismo guard que arriba, por la misma razón.
-- -----------------------------------------------------------------------------

DO $seed_lotes$
BEGIN
IF EXISTS (SELECT 1 FROM catastro.lotes) THEN
    RAISE NOTICE 'seed: catastro.lotes ya tiene datos, no se toca (dump real presente)';
ELSE

INSERT INTO catastro.lotes VALUES
-- Lotes en Bogotá D.C. (distrito 11)
(1, '110010001-01', 'N', NULL, 1, '110010001', 11, ST_GeomFromText('POLYGON((-74.105 4.545, -74.095 4.545, -74.095 4.555, -74.105 4.555, -74.105 4.545))', 4326)),
(2, '110020001-01', 'N', NULL, 1, '110020001', 11, ST_GeomFromText('POLYGON((-74.075 4.645, -74.065 4.645, -74.065 4.655, -74.075 4.655, -74.075 4.645))', 4326)),
(3, '110030001-01', 'N', NULL, 1, '110030001', 11, ST_GeomFromText('POLYGON((-74.085 4.695, -74.075 4.695, -74.075 4.705, -74.085 4.705, -74.085 4.695))', 4326)),
(4, '110040001-01', 'N', NULL, 1, '110040001', 11, ST_GeomFromText('POLYGON((-74.055 4.645, -74.045 4.645, -74.045 4.655, -74.055 4.655, -74.055 4.645))', 4326)),
(5, '110040002-01', 'N', NULL, 1, '110040002', 11, ST_GeomFromText('POLYGON((-74.065 4.655, -74.055 4.655, -74.055 4.665, -74.065 4.665, -74.065 4.655))', 4326)),

-- Lotes en Mosquera (distrito 25)
(6, '252420001-01', 'N', NULL, 1, '252420001', 25, ST_GeomFromText('POLYGON((-74.225 4.695, -74.215 4.695, -74.215 4.705, -74.225 4.705, -74.225 4.695))', 4326)),
(7, '252420002-01', 'N', NULL, 1, '252420002', 25, ST_GeomFromText('POLYGON((-74.235 4.705, -74.225 4.705, -74.225 4.715, -74.235 4.715, -74.235 4.705))', 4326)),
(8, '252420003-01', 'N', NULL, 1, '252420003', 25, ST_GeomFromText('POLYGON((-74.245 4.715, -74.235 4.715, -74.235 4.725, -74.245 4.725, -74.245 4.715))', 4326));

CREATE INDEX IF NOT EXISTS sidx_lotes_shape
    ON catastro.lotes USING gist(shape);

RAISE NOTICE 'seed: 8 lotes de demostración insertados';

END IF;
END
$seed_lotes$;

-- -----------------------------------------------------------------------------
-- ANALYZE para que el optimizador tenga estadísticas decentes desde el primer
-- query. Va FUERA del guard a propósito: si el dump real está presente también
-- las necesita (pg_restore no deja estadísticas, y sin ellas el planner elige
-- mal la primera consulta espacial sobre 2.4M filas).
--
-- Era `VACUUM ANALYZE`. Se bajó a `ANALYZE`: una tabla recién cargada no tiene
-- tuplas muertas que recuperar, así que el VACUUM sólo añadía un barrido de
-- 522 MB al arranque a cambio de nada. Y `VACUUM` no puede correr dentro de un
-- bloque de transacción, mientras que esto tiene que poder ejecutarse también
-- desde un cliente que envuelva el archivo entero.
-- -----------------------------------------------------------------------------
ANALYZE catastro.construcciones;
ANALYZE catastro.lotes;
