-- EPSG:9377 (MAGNA-SIRGAS / Origen-Nacional) en spatial_ref_sys.
--
-- Es el CRS proyectado OFICIAL de Colombia desde 2020 (resolución IGAC 471 de
-- 2020), en metros. Las imágenes de PostGIS usadas aquí no lo traen (3.3 del
-- compose de tests y 3.7 de desarrollo: `count = 0`), mientras que el validador
-- SQL y la nota de unidades del prompt —que consultan la base EPSG de pyproj—
-- lo dan por bueno para medir en metros. Resultado: el sistema recomendaba un
-- CRS que la base no podía usar (`ST_Transform(geom, 9377)` → "Cannot find SRID").
-- Visto al construir el workspace (S2.1).
--
-- Definición generada con pyproj (WKT1_GDAL) y proj4 con +towgs84 nulo
-- (MAGNA-SIRGAS ≈ WGS84 a nivel submétrico). Idempotente.

\set ON_ERROR_STOP on

INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text)
SELECT 9377, 'EPSG', 9377,
       'PROJCS["MAGNA-SIRGAS 2018 / Origen-Nacional",GEOGCS["MAGNA-SIRGAS 2018",DATUM["Marco_Geocentrico_Nacional_de_Referencia_2018",SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],AUTHORITY["EPSG","1329"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","20046"]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",4],PARAMETER["central_meridian",-73],PARAMETER["scale_factor",0.9992],PARAMETER["false_easting",5000000],PARAMETER["false_northing",2000000],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","9377"]]',
       '+proj=tmerc +lat_0=4 +lon_0=-73 +k=0.9992 +x_0=5000000 +y_0=2000000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs'
WHERE NOT EXISTS (SELECT 1 FROM spatial_ref_sys WHERE srid = 9377);
