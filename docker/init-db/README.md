# Inicialización de la base de datos

Cualquier archivo `.sql`, `.sql.gz` o `.sh` que pongas aquí, el contenedor
de Postgres lo ejecutará **una sola vez**, en orden alfabético, la primera
vez que arranque (cuando el volumen `postgis18_data` aún está vacío).

## Qué corre, y en qué orden

| Archivo | Qué hace |
|---|---|
| `00_postgis.sql` | Activa PostGIS en la BD `geo_copilot`. |
| `01_restore_catastro.sh` | Restaura `02_catastro_real.dump` **si está**. Si no, avisa y sigue. |
| `02_seed_demo.sql` | Datos de **demostración**: 25 construcciones y 8 lotes. Se aparta solo si ya hay datos. |
| `03_gis_readonly.sql` | Rol de solo-lectura `gis_readonly` sobre `catastro` y `public`. |
| `04_geo_app_role.sql` | Rol de aplicación `geo_app` (NOSUPERUSER), miembro del anterior. |
| `05_geo_app_password.sh` | Le pone contraseña a `geo_app` desde `GEO_APP_PASSWORD`. |
| `07_srid_colombia.sql` | Registra **EPSG:9377** (MAGNA-SIRGAS Origen-Nacional, CRS oficial de Colombia) en `spatial_ref_sys`: las imágenes de PostGIS usadas no lo traían y `ST_Transform(geom, 9377)` fallaba aunque el validador lo recomendaba. Idempotente. |
| `06_workspace.sql` | Workspace espacial (S2.1): rol `geo_workspace` (escribe solo en esquemas `ws_*`), catálogo `ws_meta.datasets`, y `geo_app` puede asumir el rol. Idempotente. **Esquema de la aplicación**: en una BD existente lo pone al día `python -m geo_copilot.migraciones` (ver abajo). |
| `08_mcp_lector.sh` | Rol de LOGIN `mcp_lector` (solo lectura del catastro) para el servidor MCP de SQL, con `MCP_SQL_LECTOR_PASSWORD`. En una BD existente: `docker/migrations/2026-09-27_mcp_lector.sh`. |
| `09_catastro_comentarios.sql` | El alcance de las tablas del catastro como comentario (lo lee el generador de SQL). Idempotente. |
| `10_plataforma.sql` | F6: esquema `plataforma` (dueños de sesión, conexiones por organización, pins, auditoría) y dueño en `ws_meta.proyectos`. Idempotente. **Esquema de la aplicación**, como 06. |

El orden **no** es decorativo. `03_gis_readonly.sql:26` hace
`GRANT USAGE ON SCHEMA catastro` sin comprobar que el esquema exista, y el
entrypoint de Postgres ejecuta los `.sql` con `ON_ERROR_STOP=1`: si nadie
creó `catastro` antes, el bootstrap entero aborta ahí. Por eso el seed va en
`02_`, entre el restore y los GRANT — y no después de `04_`.

## El seed de demostración (`02_seed_demo.sql`)

Un clon limpio **no** trae el dump real: pesa 522 MB y está en `.gitignore`.
Sin el seed, `docker compose up` levanta una base vacía y ninguna consulta
del README devuelve nada. Con él, el stack arranca con datos y no hay que
pedirle nada al usuario.

* **25 construcciones y 8 lotes** sintéticos sobre coordenadas reales de
  Bogotá y Cundinamarca. No hay propietarios, direcciones ni datos de
  personas.
* El esquema es el **mismo** del dump real — verificado con
  `pg_restore --schema-only` sobre el dump: mismas columnas, mismos tipos,
  mismo `CHECK (st_srid(shape) = 4326)`.
* **No pisa nada.** Los `INSERT` y los `CREATE INDEX` van dentro de un guard
  de "tabla vacía": si `01_restore_catastro.sh` ya cargó el dump real, el
  seed no inserta ni una fila y no construye un segundo índice GiST sobre
  2,4 M de geometrías.
* Es copia byte a byte de `tests/fixtures/seed_catastro.sql` (el initdb sólo
  ejecuta lo que vive en este directorio, así que no se puede compartir por
  referencia). `tests/test_seed_demo.py` falla si las dos divergen.

## Casos de uso

### Cargar TU dump real (~500 MB)

El stack ya espera un dump en formato custom (`pg_dump -F c`) llamado
`02_catastro_real.dump`; `01_restore_catastro.sh` lo restaura con
`pg_restore`. Lo pones así:

```powershell
# El nombre importa: es el que busca 01_restore_catastro.sh
copy C:\ruta\a\tu\catastro.dump docker\init-db\02_catastro_real.dump

# Destruir el volumen viejo (si tenías datos anteriores) y rebuild
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up --build
```

**Tarda 5-10 minutos** la primera vez (Postgres importa el dump). Después
la BD persiste en el volumen `postgis18_data`.

Con el dump presente, el seed de demostración se aparta solo: no hace falta
borrarlo ni renombrarlo.

⚠️ Los dumps están en `.gitignore` (`*_real.sql`, `*_real.dump`, `*.dump`,
`geocopilot.sql`). NO se commitean — cada quien pone el suyo.

### Cargar un dump genérico

```bash
# Para un dump SQL plain (texto):
pg_dump -h <host> -U <user> -d <db_original> -F p > docker/init-db/00_seed.sql

# Para un dump custom (binario, pg_dump -F c):
# Renómbralo a .dump y usa pg_restore en un .sh:
#   docker/init-db/01_restore.sh:
#     #!/bin/bash
#     pg_restore -U geo_user -d geo_copilot /docker-entrypoint-initdb.d/dump.dump
```

Después `docker compose up --build` y postgis ejecuta los scripts al
arrancar por primera vez.

### Cargar varios scripts

Nombra los archivos con prefijo numérico para forzar el orden:

```
docker/init-db/
├── 01_schemas.sql       # CREATE SCHEMA …
├── 02_tables.sql        # CREATE TABLE …
├── 03_indexes.sql       # CREATE INDEX …
└── 99_seed_data.sql     # INSERT INTO …
```

## Cambiar 06 o 10 (esquema de la aplicación)

`06_workspace.sql` y `10_plataforma.sql` son el esquema que es de la **aplicación** (`ws_meta`,
`plataforma`) y desde F7 tiene versión (Alembic, `src/geo_copilot/migraciones/`). Este directorio
solo crea los volúmenes NUEVOS; las BD existentes se ponen al día con
`python -m geo_copilot.migraciones` (en producción, el servicio `migrar` antes de la app). Por eso
**editar 06/10 no basta**: cada cambio del SQL exige una revisión nueva
`src/geo_copilot/migraciones/versions/000N_*.py` (los comentarios `--` y las líneas vacías no
cuentan para la huella: corregir una cabecera no la exige) que

* lleve el cambio a las BD existentes con su SQL **congelado** dentro (idempotente, sin `\set`;
  las revisiones no leen este directorio y la imagen no lo trae), y
* declare en `INIT_DB` la huella del archivo que deja aplicado:
  `python -c "from geo_copilot import migraciones as m; print(m.huella(m.script_sql('10_plataforma.sql')))"`.

`tests/test_f7_auditoria_migraciones.py` falla si 06/10 cambian sin esa revisión, y si aparece aquí
un archivo nuevo que no sea de dominio ni lo recoja una revisión. Detalle:
`docs/sistema/09-configuracion-y-deploy.md` §9.5.

## Importante

* Estos scripts **no se vuelven a ejecutar** en arranques posteriores. Si
  cambias el SQL después del primer `up` (salvo 06/10, que van por una
  migración: ver arriba), tienes que destruir el volumen:
  ```bash
  docker compose -f docker/docker-compose.yml down -v   # ⚠️ borra datos
  docker compose -f docker/docker-compose.yml up --build
  ```
* Los scripts corren como superusuario contra la BD `geo_copilot` (el
  default del compose). Si haces `CREATE DATABASE` adicional, créala
  como `OWNER geo_user`.
* Un `.sql` que falle **aborta la inicialización entera**: el entrypoint los
  corre con `psql -v ON_ERROR_STOP=1`. Escríbelos idempotentes
  (`IF NOT EXISTS`, `DO $$ … $$` con guardas) si quieres poder repetirlos.
* La imagen `postgis/postgis` **trae** la extensión, pero no la activa en
  bases distintas de `template_postgis`: por eso existe `00_postgis.sql`. Sin
  ese `CREATE EXTENSION` no hay `geometry_columns` ni funciones `ST_*`.
