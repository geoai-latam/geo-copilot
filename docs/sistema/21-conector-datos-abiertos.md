# 21 · Datos abiertos (datos.gov.co y CKAN) — `datos-abiertos-mcp`

Miles de conjuntos de alcaldías y entidades (instituciones educativas, sedes de salud, equipamientos…)
viven en **datos.gov.co** (Socrata), no en servicios GIS; Bogotá publica los suyos en **Datos Abiertos
Bogotá** (CKAN). `datos-abiertos-mcp` (`services/datos_abiertos_mcp/`, puerto 9900) los pone al alcance
del agente.

## Herramientas

| Herramienta | Qué hace | Sale |
|---|---|---|
| `datos_buscar(texto, portal?)` | Conjuntos que responden al texto: entidad, fecha, columnas (y cuáles son de ubicación); en CKAN, cada recurso y con qué herramienta se trae. Dice qué portales no se consultaron. | hechos |
| `datos_describir(id, portal?)` | Socrata: columnas con tipo, filas, licencia y los **valores exactos** de las columnas de texto con pocos valores (con cuántas filas). CKAN: recursos. | hechos |
| `datos_consultar(id, select?, where?, order?, columna_geo? \| lat+lon?, bbox?, area?, max_filas?)` | SoQL ejecutado EN el portal; capa si hay ubicación, tabla si no; conteo con el mismo filtro. | capa o tabla |
| `datos_traer_recurso(url, lat?, lon?)` | Recurso GeoJSON o CSV de un portal CKAN configurado, en el CRS que declara. | capa o tabla |

Los recursos WFS/WMS/OGC de CKAN se traen con `ogc-mcp` y los Esri REST con `arcgis`: los hechos de la
búsqueda lo dicen y el LLM compone.

`DATOS_PORTALES` (JSON) cambia los portales; por defecto `datos.gov.co` (Socrata) y `bogota` (CKAN).

## Comportamientos medidos (2026-10-15)

- `within_box(col, lat_norte, lon_oeste, lat_sur, lon_este)` filtra una columna de ubicación; latitud y
  longitud de texto se filtran con `::number`. Sin `$order`, las páginas de SODA no son estables: `:id`.
- Valores exactos: en Envigado `tipo_ie` = «Oficial» (23) / «No Oficial» (59). Sin ellos, el agente filtró
  `like '%oficial%'` (atrapa «No Oficial»: dijo 82) o `= 'OFICIAL'` (0).
- Los colegios privados de Bogotá (CKAN) vienen en GeoJSON con `crs` **EPSG:3857**: se declara tal cual y
  el núcleo los reproyecta. Sin `crs` y con coordenadas fuera de lon/lat, error: no se adivina la proyección.
- Coordenadas fuera de rango o vacías no se corrigen: se cuentan en los hechos.

Verdades (`tests/test_datos_abiertos_mcp_vivo.py`): Envigado 82 filas, 70 en el bbox; colegios privados de
Bogotá 1 143. V5 (`e2e-integration/datos-abiertos-mcp.spec.ts`): D1 23 oficiales y D2 1 143 en Bogotá, 3/3.

## Probar

- Unidad: `pytest tests/test_datos_abiertos_mcp.py`.
- En vivo: `pytest -m integration tests/test_datos_abiertos_mcp_vivo.py`.
