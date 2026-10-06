"""
Schema introspector — descubre el catálogo real de la BD conectada.

Reemplaza el YAML hardcoded como fuente PRIMARIA del semantic layer.
El YAML queda como overrides opcionales (aliases custom, descripciones
humanas, métricas predefinidas). Si NO hay BD conectada el YAML actúa
como fallback.

Diseño:

- `IntrospectedTable`: dataclass con info real de una tabla (schema,
  nombre, columnas, geometría detectada vía pg_catalog/geometry_columns).
- `SchemaIntrospector`: consulta `information_schema` + `geometry_columns`
  + `pg_catalog` para descubrir TODO lo que la BD expone al usuario
  conectado (respeta GRANT-s — solo ve lo que la sesión puede ver).
- `to_entity()`: convierte una `IntrospectedTable` a un `Entity` del
  semantic layer existente, generando aliases razonables desde el nombre
  de la tabla. Si hay override en YAML, se merge encima.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Modelo de la introspección
# =============================================================================
@dataclass
class IntrospectedColumn:
    """Columna real de la BD (vista vía information_schema)."""
    name: str
    data_type: str             # text, integer, geometry, …
    is_nullable: bool
    is_primary_key: bool = False
    is_geometry: bool = False
    geometry_type: str | None = None  # POLYGON, POINT, …
    srid: int | None = None


@dataclass
class IntrospectedTable:
    """Tabla real (schema + nombre) con sus columnas."""
    schema: str
    name: str
    columns: list[IntrospectedColumn] = field(default_factory=list)
    geometry_column: str | None = None
    geometry_type: str | None = None
    srid: int | None = None
    estimated_rows: int | None = None  # via pg_class.reltuples

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


# =============================================================================
# Introspector
# =============================================================================
# Schemas que NO queremos exponer al usuario (internos de Postgres / PostGIS).
_INTERNAL_SCHEMAS = frozenset({
    "pg_catalog", "information_schema", "pg_toast",
    "topology",  # PostGIS internal (topology.layer, topology.topology)
    "tiger", "tiger_data",  # PostGIS geocoder
})


class SchemaIntrospector:
    """Descubre tablas/columnas reales de la BD usando un pool asyncpg.

    Uso típico::

        introspector = SchemaIntrospector(db_pool)
        tables = await introspector.list_tables()         # todas las tablas user
        construcciones = await introspector.describe("catastro", "construcciones")
        full = await introspector.full_schema()           # dict completo
    """

    def __init__(self, db_pool: Any):
        """`db_pool` es un asyncpg.Pool. Si es None, todos los métodos
        devuelven listas/dicts vacíos (modo "sin BD")."""
        self.db_pool = db_pool

    async def is_connected(self) -> bool:
        if self.db_pool is None:
            return False
        try:
            async with self.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:  # sonda de salud sobre un pool inyectado (asyncpg o sustituto): cualquier fallo = no conectado
            logger.debug("[introspector] sonda de conexión falló", exc_info=True)
            return False

    # -------------------------------------------------------------------------
    # Listado de schemas/tablas
    # -------------------------------------------------------------------------
    async def list_schemas(self) -> list[str]:
        """Schemas user-visibles (excluye internos de PG/PostGIS)."""
        if self.db_pool is None:
            return []
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN (
                    'pg_catalog', 'information_schema', 'pg_toast',
                    'topology', 'tiger', 'tiger_data'
                )
                  AND schema_name NOT LIKE 'pg_temp_%'
                  AND schema_name NOT LIKE 'pg_toast_temp_%'
                ORDER BY schema_name
                """
            )
        return [r["schema_name"] for r in rows]

    async def list_tables(self, schema: str | None = None) -> list[IntrospectedTable]:
        """Lista todas las tablas (opcionalmente filtradas por schema).

        Cada tabla viene con su `columns`, `geometry_column` (si tiene),
        y `estimated_rows` (`pg_class.reltuples`, barato).
        """
        if self.db_pool is None:
            return []

        async with self.db_pool.acquire() as conn:
            # Tablas + estimación rápida de filas.
            sql = """
                SELECT
                    n.nspname AS schema_name,
                    c.relname AS table_name,
                    c.reltuples::bigint AS estimated_rows
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'  -- ordinary table
                  AND n.nspname NOT IN (
                    'pg_catalog', 'information_schema', 'pg_toast',
                    'topology', 'tiger', 'tiger_data'
                  )
                  AND n.nspname NOT LIKE 'pg_temp_%'
                  AND n.nspname NOT LIKE 'pg_toast_temp_%'
                  AND has_table_privilege(c.oid, 'SELECT')  -- solo lo accesible
            """
            if schema:
                sql += " AND n.nspname = $1 "
                sql += " ORDER BY n.nspname, c.relname"
                rows = await conn.fetch(sql, schema)
            else:
                sql += " ORDER BY n.nspname, c.relname"
                rows = await conn.fetch(sql)

        # Hidratamos columnas para cada tabla.
        tables: list[IntrospectedTable] = []
        for r in rows:
            table = IntrospectedTable(
                schema=r["schema_name"],
                name=r["table_name"],
                estimated_rows=r["estimated_rows"],
            )
            table.columns = await self._fetch_columns(table.schema, table.name)
            geom_col = next((c for c in table.columns if c.is_geometry), None)
            if geom_col:
                table.geometry_column = geom_col.name
                table.geometry_type = geom_col.geometry_type
                table.srid = geom_col.srid
            tables.append(table)
        return tables

    async def describe(
        self, schema: str, name: str,
    ) -> IntrospectedTable | None:
        """Describe una tabla específica con sus columnas + geometría."""
        if self.db_pool is None:
            return None
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT n.nspname, c.relname, c.reltuples::bigint AS estimated_rows
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind = 'r'
                  AND has_table_privilege(c.oid, 'SELECT')
                """,
                schema, name,
            )
        if not row:
            return None
        table = IntrospectedTable(
            schema=row["nspname"],
            name=row["relname"],
            estimated_rows=row["estimated_rows"],
        )
        table.columns = await self._fetch_columns(schema, name)
        geom_col = next((c for c in table.columns if c.is_geometry), None)
        if geom_col:
            table.geometry_column = geom_col.name
            table.geometry_type = geom_col.geometry_type
            table.srid = geom_col.srid
        return table

    # -------------------------------------------------------------------------
    # Columnas + geometría (combina information_schema + geometry_columns)
    # -------------------------------------------------------------------------
    async def _fetch_columns(self, schema: str, table: str) -> list[IntrospectedColumn]:
        async with self.db_pool.acquire() as conn:
            # information_schema.columns: tipos básicos + nullable.
            col_rows = await conn.fetch(
                """
                SELECT
                    column_name,
                    data_type,
                    udt_name,
                    is_nullable
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema, table,
            )
            # geometry_columns: nos dice qué columnas SON geometría + type + SRID.
            # (Vista provista por PostGIS).
            geom_rows = await conn.fetch(
                """
                SELECT f_geometry_column AS col, type, srid
                FROM public.geometry_columns
                WHERE f_table_schema = $1 AND f_table_name = $2
                """,
                schema, table,
            )
            geom_lookup = {
                g["col"]: (g["type"], g["srid"]) for g in geom_rows
            }
            # Primary key vía pg_index.
            pk_rows = await conn.fetch(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = ($1 || '.' || $2)::regclass AND i.indisprimary
                """,
                schema, table,
            )
            pk_set = {r["attname"] for r in pk_rows}

        cols: list[IntrospectedColumn] = []
        for r in col_rows:
            name = r["column_name"]
            data_type = r["data_type"]
            udt = r["udt_name"]
            is_geom = (udt == "geometry") or (name in geom_lookup)
            geom_info = geom_lookup.get(name)
            cols.append(IntrospectedColumn(
                name=name,
                data_type="geometry" if is_geom else data_type,
                is_nullable=(r["is_nullable"] == "YES"),
                is_primary_key=(name in pk_set),
                is_geometry=is_geom,
                geometry_type=geom_info[0] if geom_info else None,
                srid=geom_info[1] if geom_info else None,
            ))
        return cols

    # -------------------------------------------------------------------------
    # Full schema dump — útil para el endpoint /metadata/tables
    # -------------------------------------------------------------------------
    async def full_schema(self) -> dict[str, Any]:
        """Estructura serializable: lista de schemas → tablas → columnas.

        Pensada para que el frontend la consuma vía REST y construya un
        árbol. NO incluye datos, solo metadata estructural.
        """
        tables = await self.list_tables()
        by_schema: dict[str, list[dict[str, Any]]] = {}
        for t in tables:
            by_schema.setdefault(t.schema, []).append({
                "name": t.name,
                "qualified_name": t.qualified_name,
                "estimated_rows": t.estimated_rows,
                "geometry_column": t.geometry_column,
                "geometry_type": t.geometry_type,
                "srid": t.srid,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.data_type,
                        "nullable": c.is_nullable,
                        "primary_key": c.is_primary_key,
                        "is_geometry": c.is_geometry,
                        "geometry_type": c.geometry_type,
                        "srid": c.srid,
                    }
                    for c in t.columns
                ],
            })

        return {
            "schemas": [
                {
                    "name": schema,
                    "tables": sorted(tables, key=lambda x: x["name"]),
                }
                for schema, tables in sorted(by_schema.items())
            ],
            "total_tables": sum(len(v) for v in by_schema.values()),
            "total_schemas": len(by_schema),
        }


# =============================================================================
# Conversión a Entity (modelo del semantic layer existente)
# =============================================================================
def table_to_entity_dict(table: IntrospectedTable) -> dict[str, Any]:
    """Convierte una tabla introspectada al dict que `Entity` espera.

    Genera aliases razonables a partir del nombre de la tabla:
    - 'construcciones' → ['construccion', 'construcción', 'edificacion']
    - 'lotes' → ['lote', 'lotes']
    Si el usuario quiere aliases custom, se merge con el YAML override.
    """
    # Generar aliases desde el nombre. Heurística simple:
    # - quitar _N años, sufijos, plurales obvios
    name = table.name.lower()
    aliases = [name]
    if name.endswith("es"):
        aliases.append(name[:-2])  # construcciones → construccion
    if name.endswith("s"):
        aliases.append(name[:-1])  # lotes → lote

    # Description genérica si no hay YAML override.
    description = (
        f"Tabla {table.qualified_name} "
        f"({table.estimated_rows:,} filas estimadas)"
        if table.estimated_rows and table.estimated_rows > 0
        else f"Tabla {table.qualified_name}"
    )

    fields_dict: dict[str, dict[str, Any]] = {}
    for c in table.columns:
        if c.is_geometry:
            continue  # geometría va en geometry_column, no en fields
        fields_dict[c.name] = {
            "column": c.name,
            "type": _pg_to_semantic_type(c.data_type),
            "description": f"Columna {c.name}",
            "primary_key": c.is_primary_key,
        }

    # Si PostGIS no reporta srid/geometry_type es porque la columna se creó
    # sin typmod (GEOMETRY sin restricción). NO inventamos POLYGON/4326: el
    # sentinel real es "GEOMETRY"/0 — los consumidores deben tratarlo como
    # untyped y, si necesitan SRID, exigir ST_SetSRID explícito.
    if table.srid is None or table.srid == 0:
        logger.warning(
            f"[introspector] {table.qualified_name} sin SRID declarado en "
            "PostGIS — entidad expuesta con srid=0 (untyped)."
        )
    if not table.geometry_type:
        logger.warning(
            f"[introspector] {table.qualified_name} sin geometry_type en "
            "PostGIS — entidad expuesta como GEOMETRY (genérico)."
        )

    return {
        "name": name,
        "description": description,
        "aliases": list(dict.fromkeys(aliases)),
        "table": table.name,
        "schema_name": table.schema,
        "geometry_column": table.geometry_column or "geom",
        "geometry_type": (table.geometry_type or "GEOMETRY").upper(),
        "srid": table.srid or 0,
        "fields": fields_dict,
        "metrics": [
            {
                "name": "cantidad",
                "expression": "COUNT(*)",
                "description": f"Número de {name}",
            },
        ],
    }


def _pg_to_semantic_type(pg_type: str) -> str:
    """Mapea tipos PG a nombres semánticos compactos."""
    t = pg_type.lower()
    if "int" in t:
        return "integer"
    if "float" in t or "numeric" in t or "double" in t or "real" in t:
        return "float"
    if "text" in t or "char" in t or "varchar" in t:
        return "string"
    if "bool" in t:
        return "boolean"
    if "date" in t or "time" in t:
        return "date"
    if "geometry" in t:
        return "geometry"
    return "string"


__all__ = [
    "IntrospectedColumn",
    "IntrospectedTable",
    "SchemaIntrospector",
    "table_to_entity_dict",
]
