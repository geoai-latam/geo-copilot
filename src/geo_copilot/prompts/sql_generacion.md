Genera una consulta SQL para PostgreSQL/PostGIS.

SCHEMA DE LA BASE DE DATOS:
{schema_info}

{contexto_conversacion}

REGLAS OBLIGATORIAS:
- Usa comillas dobles para nombres de columna: SELECT "CODIGO" FROM tabla
- GEOMETRÍA PARA EL MAPA (clave para que se renderice): en SELECTs sobre una tabla CON
  geometría, SIEMPRE incluye la geometría como GeoJSON: ST_AsGeoJSON(ST_Transform("columna_geometria", 4326)) AS geom_geojson
  IMPORTANTE: usa el nombre EXACTO de la columna de geometría del schema (ej: "geom", "shape", "the_geom").
  - Si la consulta AGREGA pero igual debe verse en el mapa (ej. "cuenta los lotes y píntalos",
    o un paso posterior la transforma/colorea), NO uses solo COUNT/SUM (1 fila sin geometría →
    mapa vacío): incluye TAMBIÉN ST_AsGeoJSON por fila, o agrupa y devuelve una geometría por grupo.
  - Solo omite la geometría si es un conteo/estadística PURO que NO se mostrará en el mapa.
- SIEMPRE incluir LIMIT {_tope} al final de la consulta (protección obligatoria)
  Si el usuario pide una cantidad específica, usa esa cantidad en el LIMIT
- NO uses funciones que no existan en PostGIS
- Un FILTRO usa valores que los datos TIENEN (columnas y valores del esquema). Si el pedido acota
  por un lugar o una categoría que no es valor de ninguna columna (p. ej. «la localidad de X» en una
  tabla sin columna de localidad), NO lo aproximes con un código, prefijo o patrón supuesto
  («LIKE '11%' -- típico de X») NI LO OMITAS: una consulta sin ese filtro devuelve otra cosa (V5:
  «los lotes de más de 1000 m² en Chapinero» sin el filtro contó los 43.249 de todo Bogotá y se narró
  como Chapinero). Si en el contexto hay un dataset del workspace con el límite de ese lugar,
  crúzalo (ST_Intersects); si no, responde SOLO con un comentario SQL que diga qué falta (p. ej.
  «-- catastro.lotes no tiene localidad: hace falta el límite de X para filtrar por área»).
- IMPORTANTE: Cuando uses ORDER BY para encontrar máximos/mínimos, SIEMPRE agrega NULLS LAST para evitar que valores NULL aparezcan primero:
  * ORDER BY "columna" DESC NULLS LAST (para máximos)
  * ORDER BY "columna" ASC NULLS LAST (para mínimos)
  * También considera agregar WHERE "columna" IS NOT NULL si solo quieres registros con valores

⚠️ REGLA CRÍTICA - DISTINGUIR VISUALIZACIÓN vs DATOS:
- Los COLORES (rojo, azul, verde, amarillo, etc.) son INSTRUCCIONES DE VISUALIZACIÓN, NO filtros de datos
- NUNCA uses colores en WHERE - los colores son para cómo se mostrará en el mapa, no para filtrar
- Ejemplos INCORRECTOS:
  * WHERE "TIPO_DOMIN" = 'rojo' ❌ (rojo NO es un valor de datos)
  * WHERE "color" = 'azul' ❌
- Ejemplos CORRECTOS:
  * "muéstrame el predio más grande de color rojo" → SELECT ... ORDER BY ST_Area(...) DESC LIMIT 1
    (el color rojo se usará para visualización, NO en el WHERE)
  * "predios en zona residencial de color verde" → WHERE "USO_SUELO" = 'RESIDENCIAL'
    (filtrar por USO_SUELO, el verde es para visualización)
- Si el usuario menciona un color, IGNÓRALO en el SQL - otro agente se encargará de la simbología

REGLAS CRÍTICAS PARA DISTANCIAS Y UNIDADES:
- Las geometrías pueden estar en SRID 4326 (grados) o en sistemas PROYECTADOS (metros)
- Para usar ::geography (que requiere lat/lon), SIEMPRE transforma primero a 4326:
  * ST_DWithin(ST_Transform("geom", 4326)::geography, ST_Transform(ref, 4326)::geography, metros)
  * ST_Distance(ST_Transform("geom", 4326)::geography, ST_Transform(ref, 4326)::geography)
  * ST_Buffer(ST_Transform("geom", 4326)::geography, metros)::geometry
- PROXIMIDAD SOBRE TABLAS GRANDES (miles/millones de filas): el ::geography sobre la
  columna NO usa el índice espacial y la consulta recorre toda la tabla (en el catastro,
  >30 s y timeout). Pon SIEMPRE antes un PREFILTRO INDEXABLE sobre la columna CRUDA con
  `&&` y ST_Expand en las unidades de su SRID (en 4326: grados ≈ metros / 100000 · 1.5),
  y deja el ST_DWithin geography para la medida exacta:
  WHERE t."geom" && ST_Expand((SELECT ST_Union("geom") FROM tabla WHERE "CODIGO" = 'X'), 0.003)
    AND ST_DWithin(
      t."geom"::geography,
      (SELECT ST_Union("geom") FROM tabla WHERE "CODIGO" = 'X')::geography,
      200
    )
  (con columnas en otro SRID, transforma ANTES a 4326 y usa ST_Expand en grados igual.)
  Si la referencia son VARIAS geometrías (una manzana, un barrio), úsalas TODAS con
  ST_Union, no una sola con LIMIT 1.
- Para columnas de texto largo como códigos catastrales, usa comparación de strings:
  WHERE "CODIGO" = '256490100000000250010000000000' (NO como integer)

REGLAS CRÍTICAS PARA ÁREA (ST_Area):
- Para área en METROS² fiable en CUALQUIER zona del mundo, usa el método GEODÉSICO:
  * ST_Area(ST_Transform("geom", 4326)::geography) AS area_m2
- NUNCA uses ST_Area sobre SRID 3857 (Web Mercator): distorsiona el área lejos del
  ecuador y da valores erróneos. Tampoco ST_Area directo sobre 4326 (daría grados²,
  sin sentido físico).
- Alternativa válida SOLO si la zona es única: ST_Transform a un UTM proyectado
  apropiado para esa zona — NUNCA 3857.
- Ejemplo correcto (filtrar por área > 500 m²):
  WHERE ST_Area(ST_Transform("geom", 4326)::geography) > 500

REGLAS PARA ESTADÍSTICAS Y GRÁFICAS:
- Si el usuario pide estadísticas, distribución, conteo, o gráfica: usar GROUP BY
- Si hace referencia a datos anteriores ("esos predios", "esa información"), COPIAR el WHERE del SQL anterior
- Ejemplo: "dame estadísticas de esos predios por pisos" → GROUP BY "NUMERO_PIS" + mismo WHERE del SQL anterior
- Para estadísticas NO incluir geometría, solo los campos agregados
- Ejemplo de estadística:
  SELECT "NUMERO_PIS", COUNT(*) AS cantidad
  FROM public.predios
  WHERE ST_DWithin(...)  -- MISMO FILTRO que el SQL anterior
  GROUP BY "NUMERO_PIS"
  ORDER BY cantidad DESC
  LIMIT {_tope};

CONSULTA DEL USUARIO: "{query}"

Responde SOLO con el SQL, sin explicación ni markdown.
