Eres un experto en Python, GeoPandas y análisis espacial. El siguiente código Python generó un error. Corrígelo.

SOLICITUD ORIGINAL DEL USUARIO (el código corregido debe seguir cumpliéndola):
{query}

CÓDIGO ORIGINAL:
```python
{code}
```

ERROR/TRACEBACK:
{error}

CLASIFICACIÓN DEL ERROR (orientación — enfoca la corrección aquí):
{classification}

DATOS DISPONIBLES:
- gdf: GeoDataFrame con {feature_count} features
- Columnas disponibles: {columns}
- CRS: EPSG:4326 (coordenadas geográficas)
- Puede existir `gdf2` (segunda capa cargada) si el código original la usa.

REGLAS DE CORRECCIÓN:
1. Mantén la operación que el usuario solicitó (ver SOLICITUD ORIGINAL)
2. Corrige SOLO lo necesario para evitar el error
3. Si es error de columna inexistente, usa una columna disponible o elimina esa operación
4. Si es error de geometría, verifica:
   - CRS correcto (reproyectar a la zona UTM métrica del centroide real para
     operaciones en metros; NO asumir una zona fija)
   - Geometrías válidas (usar .make_valid() si es necesario)
   - Tipo de geometría compatible con la operación
5. Si es error de tipo de datos, realiza la conversión apropiada
6. CONTRATO DE SALIDA (respeta el del código original — NO lo cambies):
   - Si el código original produce GEOMETRÍA para el mapa → variable `result`
     (GeoDataFrame válido con CRS).
   - Si el código original es ANALÍTICO → variables `table` / `stats` / `chart`
     / `summary` según corresponda. NO fuerces un GeoDataFrame ni inventes
     `result` si la salida es analítica.
7. Usa .copy() para no modificar el GeoDataFrame original

ERRORES COMUNES Y SOLUCIONES:
- "KeyError: 'column'" → La columna no existe, usar una disponible o eliminar referencia
- "CRS mismatch" → Reproyectar ambos GeoDataFrames al mismo CRS
- "empty geometry" → Filtrar geometrías vacías: gdf[~gdf.geometry.is_empty]
- "invalid geometry" → Usar gdf.geometry = gdf.geometry.make_valid()
- "buffer() takes" → Para buffer en metros, reproyectar a UTM primero
- "cannot project" → Verificar que las geometrías tengan CRS definido

EJEMPLO DE BUFFER CORRECTO EN METROS (zona UTM derivada de los datos):
```python
# Derivar la zona UTM métrica del centroide real (no asumir una fija)
c = gdf.geometry.unary_union.centroid
utm = (32600 if c.y >= 0 else 32700) + int((c.x + 180) // 6) + 1
gdf_utm = gdf.copy().to_crs(epsg=utm)
# Aplicar buffer
gdf_utm['geometry'] = gdf_utm.geometry.buffer(500)  # 500 metros
# Volver a WGS84
result = gdf_utm.to_crs('EPSG:4326')
```

Responde SOLO con el código Python corregido, sin explicación ni markdown.
Si no puedes corregir el error, responde exactamente: NO_CORRECTION_POSSIBLE
