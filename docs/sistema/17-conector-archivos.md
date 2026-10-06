# 17 · Archivos geoespaciales con DuckDB (GeoParquet, FlatGeobuf, CSV…)

`archivos-mcp` (T5.6, `services/archivos_mcp`) lee archivos geoespaciales **donde están**
(disco, S3, HTTP) con DuckDB-spatial y filtra **en el origen**. DuckDB es la fuente, no el
almacén (decisión D1): al workspace solo llega el resultado.

## 1. Herramientas

| Tool | Qué hace |
|---|---|
| `archivos_list` | Las fuentes configuradas: qué contienen, formato, cuántos elementos, columna de geometría, extensión (EPSG:4326) y columnas con tipo |
| `archivos_query` | Elementos de una fuente, con el filtro aplicado en DuckDB: `where` (SQL sobre sus columnas), `aoi` (un área: el núcleo pone la geometría del dibujo, la selección o la capa que el usuario nombró, p. ej. `@Área 1`) y `columns` |

- **Hasta 5 000 elementos:** vuelven como capa en la respuesta.
- **Más de 5 000:** vuelven como **GeoParquet por referencia** (`feature_ref` a
  `/resultados/<id>.parquet`). El hub lo descarga **del propio servidor** con su credencial, y
  solo desde las rutas que declara su configuración (`recursos.prefixes`). No pasa por la guarda
  de Internet del workspace, que bloquea la red interna. Luego lo materializa en el workspace.
  Los resultados viven una hora en un `tmpfs`.
- **Por defecto** se traen hasta 50 000 elementos (máximo 200 000). Los hechos dicen cuántos
  cumplen y si vino todo; si no, lo marcan como MUESTRA.

## 2. Seguridad

- **Las fuentes las fija la configuración** (`ARCHIVOS_SOURCES`); el LLM no da rutas.
- **El `where` pasa por el mismo validador del núcleo** (`geo_sql_guard`), contra una única
  tabla. Rechaza varias sentencias, catálogos del sistema y funciones fuera de la lista
  permitida, como `read_csv` o cualquier otra lectura de archivos.
- **El contenedor:**
  - sistema de archivos de solo lectura;
  - datos montados `:ro`;
  - extensiones de DuckDB instaladas en la imagen: no descarga nada al correr;
  - `uid` propio, `cap_drop: ALL`;
  - clave con scope `archivos:read`, también para `/resultados/`.

## 3. Configurar una fuente

```yaml
ARCHIVOS_SOURCES: >-
  [{"id":"predios_chapinero_teusaquillo","descripcion":"Predios de Chapinero y Teusaquillo — GeoParquet",
    "uri":"/datos/predios_chapinero.parquet","formato":"geoparquet"},
   {"id":"sedes","descripcion":"Sedes (CSV)","uri":"s3://bucket/sedes.csv","formato":"csv","lon":"lon","lat":"lat"}]
```

- **Formatos:**
  - `geoparquet`/`parquet` (`read_parquet`);
  - `flatgeobuf`/`geojson` (GDAL, `ST_Read`);
  - `csv` con columnas `lon`/`lat`.
- **CRS:** `crs` si no es EPSG:4326; se reproyecta al leer.
- **Remotos (S3/HTTP):** DuckDB los lee con `httpfs`. Las credenciales van en el entorno del
  servicio, nunca en el YAML.

**Datos de demostración:** `python scripts/demo_geoparquet.py` exporta 22 387 lotes del catastro
de Chapinero y Teusaquillo a `data/archivos/predios_chapinero.parquet` (6,3 MB, no versionado).

```bash
docker compose --env-file .env -f docker/docker-compose.yml --profile connectors up -d archivos-mcp
```

## 4. Cómo llega el turno aquí

El router no conoce los archivos. Lo que acaba en `archivos_query` llega por los traspasos al
bucle ReAct, que ve todos los servicios y decide:
- `load_external` **sin URL** con servicios conectados → bucle;
- el generador SQL de la BD interna **inventa una tabla** (p. ej. `archivos.predios`) y con
  servicios conectados → bucle. Esto se suma al caso en que responde que no la tiene.

Desde el propio bucle no se reabre otro bucle (`_desde_bucle`).

## 5. Pruebas

| Capa | Dónde |
|---|---|
| Servidor sin red: listar, filtro y recorte en origen, CSV, `where` peligroso, columnas, `feature_ref` | `tests/test_archivos_mcp.py` |
| Hub: descarga del GeoParquet del servidor y materialización (servidor real en proceso) | `tests/test_archivos_mcp.py` |
| Traspasos al bucle y sin anidar | `tests/test_hitl_cutover.py`, `tests/test_data_agent_via_mcp.py` |
| V5 (Chrome, 2026-09-27) | «carga los predios del GeoParquet que caen en @Área 1» → 2 664 de 22 387 = PostGIS sobre el mismo rectángulo; «todos, sin filtro» → 22 387 (GeoParquet por referencia) |
