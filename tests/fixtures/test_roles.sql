-- Rol de solo lectura para los tests de integración (S0.5 del plan de plataforma).
--
-- `gc_test` es el POSTGRES_USER de la imagen oficial, y la imagen lo crea como
-- SUPERUSER: conectarse con él hacía que "el validador bloquea el SQL
-- destructivo" fuera la única defensa probada. En producción la app ejecuta el
-- SQL del modelo con `SET LOCAL ROLE gis_readonly`; aquí los tests se conectan
-- con un rol equivalente, así que un bypass del validador también fallaría por
-- permisos.
--
-- Va en un archivo aparte, no en `seed_catastro.sql`: ese es copia byte a byte
-- de `docker/init-db/02_seed_demo.sql` y no debe divergir.
CREATE ROLE gc_reader LOGIN PASSWORD 'gc_reader_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA catastro TO gc_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA catastro TO gc_reader;

-- Rol de aplicación de tests (espejo de `geo_app`): LOGIN y NOINHERIT, así que
-- fuera de un `SET LOCAL ROLE` no escribe nada. Asume `geo_workspace` para
-- escribir en ws_* y `gis_readonly` para leer (el workspace, S2.1).
CREATE ROLE gc_app LOGIN PASSWORD 'gc_app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT geo_workspace TO gc_app;
GRANT gis_readonly TO gc_app;
GRANT geo_plataforma TO gc_app;  -- F6 (docker/init-db/10_plataforma.sql)
GRANT USAGE ON SCHEMA catastro TO gis_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA catastro TO gis_readonly;
