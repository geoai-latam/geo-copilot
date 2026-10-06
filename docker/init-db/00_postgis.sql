-- Activar la extensión PostGIS en la base recién creada.
--
-- La imagen ``postgis/postgis:15-3.3`` provee la extensión pero NO la
-- activa automáticamente en bases distintas de ``template_postgis``.
-- Sin esto, ``geometry_columns`` y todas las funciones ``ST_*`` no
-- existen y el orquestador falla al pedir el schema.
--
-- Se ejecuta una sola vez, en el primer arranque (cuando el volumen
-- ``postgis_data`` está vacío). ``IF NOT EXISTS`` lo hace idempotente
-- por si re-ejecutas manualmente.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Confirmar versiones en el log de arranque.
SELECT 'PostGIS ' || PostGIS_Version() AS active_extension;
