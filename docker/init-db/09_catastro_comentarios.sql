-- F5 (V5): el ALCANCE de las tablas del catastro como comentario (el generador de SQL lo lee).
-- Sin esto, «lotes del catastro de Bogotá» se tradujo en filtros inventados (`lotdistrit = 11001`,
-- luego `= 1`) y la respuesta fue 0 lotes donde había 3. Solo se dice lo que es un hecho de la
-- fuente (IDECA, Bogotá D.C. con su zona rural); no se inventa el significado de ninguna columna.
-- Idempotente; en una BD existente: sh docker/migrations/2026-09-27_catastro_comentarios.sh
DO $$
BEGIN
    IF to_regclass('catastro.lotes') IS NOT NULL THEN
        COMMENT ON TABLE catastro.lotes IS
            'Lotes catastrales de Bogotá D.C. (IDECA, incluida la zona rural): TODOS los registros son de Bogotá; para «de Bogotá» no hace falta ningún filtro.';
    END IF;
    IF to_regclass('catastro.construcciones') IS NOT NULL THEN
        COMMENT ON TABLE catastro.construcciones IS
            'Construcciones catastrales de Bogotá D.C. (IDECA, incluida la zona rural): TODOS los registros son de Bogotá; para «de Bogotá» no hace falta ningún filtro.';
    END IF;
END
$$;
