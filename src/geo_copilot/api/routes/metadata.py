"""
Endpoints para metadatos y configuración.

100% dinámico desde la BD - NO usa archivos YAML.
El sistema descubre automáticamente las tablas de PostgreSQL/PostGIS.
"""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from geo_copilot.api.dependencies import get_db_pool
from geo_copilot.api.models import EntitiesResponse
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# Fallos de introspección contra la BD: errores del servidor PostgreSQL,
# pool cerrado/conexión inválida (InterfaceError), red (OSError) y timeouts
# de ``acquire``/``command_timeout``.
_DB_ERRORS: tuple[type[Exception], ...] = (
    asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError,
)

from geo_copilot.api.auth import require_principal

router = APIRouter(
    prefix="/metadata",
    tags=["Metadata"],
    dependencies=[Depends(require_principal)],
)


async def get_entities_from_database(db_pool) -> list[dict]:
    """Obtener entidades directamente de la BD mediante introspección."""
    if not db_pool:
        return []

    try:
        async with db_pool.acquire() as conn:
            # Query para obtener tablas con geometría
            query = """
                SELECT DISTINCT
                    c.table_schema,
                    c.table_name,
                    gc.type as geometry_type,
                    gc.srid,
                    gc.f_geometry_column as geom_column
                FROM information_schema.columns c
                LEFT JOIN geometry_columns gc
                    ON gc.f_table_schema = c.table_schema
                    AND gc.f_table_name = c.table_name
                WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema', 'topology')
                GROUP BY c.table_schema, c.table_name, gc.type, gc.srid, gc.f_geometry_column
                ORDER BY c.table_schema, c.table_name
            """
            rows = await conn.fetch(query)

            entities = []
            for row in rows:
                table_name = f"{row['table_schema']}.{row['table_name']}"
                entities.append({
                    "name": table_name,
                    "display_name": row['table_name'],
                    "description": f"Tabla {row['table_name']} en esquema {row['table_schema']}",
                    "category": row['table_schema'],  # Usar schema como categoría
                    "geometry_type": row['geometry_type'] or "none",
                    "aliases": [row['table_name']],
                    "field_count": 0,
                    "schema": row['table_schema'],
                    "has_geometry": row['geometry_type'] is not None,
                    "srid": row['srid'],
                    "geom_column": row['geom_column'],
                })

            return entities
    except _DB_ERRORS as e:
        logger.error(f"Error getting entities from database: {e}")
        return []


async def get_entity_details_from_database(db_pool, entity_name: str) -> dict | None:
    """Obtener detalles de una entidad desde la BD."""
    if not db_pool:
        return None

    # Parsear schema.table
    parts = entity_name.split(".")
    if len(parts) == 2:
        schema_name, table_name = parts
    else:
        schema_name = "public"
        table_name = entity_name

    try:
        async with db_pool.acquire() as conn:
            # Obtener columnas de la tabla
            columns_query = """
                SELECT
                    c.column_name,
                    c.data_type,
                    c.udt_name,
                    c.is_nullable,
                    gc.type as geometry_type,
                    gc.srid
                FROM information_schema.columns c
                LEFT JOIN geometry_columns gc
                    ON gc.f_table_schema = c.table_schema
                    AND gc.f_table_name = c.table_name
                    AND gc.f_geometry_column = c.column_name
                WHERE c.table_schema = $1 AND c.table_name = $2
                ORDER BY c.ordinal_position
            """
            rows = await conn.fetch(columns_query, schema_name, table_name)

            if not rows:
                return None

            fields = []
            geometry_column = None
            geometry_type = None
            srid = None

            for row in rows:
                field = {
                    "name": row["column_name"],
                    "display_name": row["column_name"],
                    "type": row["udt_name"] or row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                }
                fields.append(field)

                if row["geometry_type"]:
                    geometry_column = row["column_name"]
                    geometry_type = row["geometry_type"]
                    srid = row["srid"]

            return {
                "name": entity_name,
                "display_name": table_name,
                "description": f"Tabla {table_name} en esquema {schema_name}",
                "category": schema_name,
                "schema": schema_name,
                "table": table_name,
                "geometry_type": geometry_type or "none",
                "geometry_column": geometry_column,
                "srid": srid,
                "fields": fields,
                "field_count": len(fields),
            }
    except _DB_ERRORS as e:
        logger.error(f"Error getting entity details: {e}")
        return None


@router.get(
    "/entities",
    response_model=EntitiesResponse,
    summary="List available entities",
    description="Get entities from database schema (dynamic discovery).",
)
async def list_entities(
    db_pool=Depends(get_db_pool),
) -> EntitiesResponse:
    """
    Listar entidades disponibles desde la BD.

    100% dinámico - descubre tablas directamente de PostgreSQL/PostGIS.
    """
    entities = await get_entities_from_database(db_pool)

    if not entities:
        logger.warning("No entities found - check database connection")

    return EntitiesResponse(entities=entities, total=len(entities))


@router.get(
    "/entities/{entity_name:path}",
    response_model=dict,
    summary="Get entity details",
    description="Get detailed information about a specific entity from database.",
)
async def get_entity(
    entity_name: str,
    db_pool=Depends(get_db_pool),
) -> dict:
    """Obtener detalles de una entidad desde la BD."""
    entity = await get_entity_details_from_database(db_pool, entity_name)

    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_name}' not found")

    return entity


@router.get(
    "/categories",
    response_model=list[dict],
    summary="List entity categories",
    description="Get all database schemas as categories.",
)
async def list_categories(
    db_pool=Depends(get_db_pool),
) -> list[dict]:
    """Listar esquemas de BD como categorías."""
    if not db_pool:
        return []

    try:
        async with db_pool.acquire() as conn:
            query = """
                SELECT DISTINCT table_schema
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'topology')
                ORDER BY table_schema
            """
            rows = await conn.fetch(query)
            return [
                {"value": row["table_schema"], "name": row["table_schema"].upper()}
                for row in rows
            ]
    except _DB_ERRORS as e:
        logger.error(f"Error getting categories: {e}")
        return []


@router.get(
    "/search",
    response_model=list[dict],
    summary="Search entities",
    description="Search entities by keyword in table names.",
)
async def search_entities(
    q: str,
    db_pool=Depends(get_db_pool),
) -> list[dict]:
    """Buscar entidades por palabra clave en la BD."""
    if not db_pool:
        return []

    try:
        async with db_pool.acquire() as conn:
            query = """
                SELECT DISTINCT
                    c.table_schema,
                    c.table_name,
                    gc.type as geometry_type
                FROM information_schema.columns c
                LEFT JOIN geometry_columns gc
                    ON gc.f_table_schema = c.table_schema
                    AND gc.f_table_name = c.table_name
                WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema', 'topology')
                AND (c.table_name ILIKE $1 OR c.column_name ILIKE $1)
                GROUP BY c.table_schema, c.table_name, gc.type
                ORDER BY c.table_schema, c.table_name
                LIMIT 20
            """
            rows = await conn.fetch(query, f"%{q}%")

            return [
                {
                    "name": f"{row['table_schema']}.{row['table_name']}",
                    "display_name": row['table_name'],
                    "description": f"Tabla en esquema {row['table_schema']}",
                    "category": row['table_schema'],
                    "geometry_type": row['geometry_type'] or "none",
                }
                for row in rows
            ]
    except _DB_ERRORS as e:
        logger.error(f"Error searching entities: {e}")
        return []


@router.get(
    "/tables",
    summary="Full schema tree — schemas → tablas → columnas",
    description=(
        "Devuelve la estructura COMPLETA del catálogo de la BD conectada "
        "(introspectada en runtime, sin hardcoding). Frontend la usa para "
        "renderizar el árbol del panel BD. Incluye columnas con tipo, "
        "primary keys, columnas de geometría con SRID y type."
    ),
)
async def list_tables(db_pool=Depends(get_db_pool)) -> dict:
    """Schema completo descubierto en runtime — no hay YAML hardcoded.

    Estructura devuelta:

    ```json
    {
      "schemas": [
        {
          "name": "catastro",
          "tables": [
            {
              "name": "construcciones",
              "qualified_name": "catastro.construcciones",
              "estimated_rows": 2409199,
              "geometry_column": "shape",
              "geometry_type": "POLYGON",
              "srid": 4326,
              "columns": [
                {"name": "objectid", "type": "integer", "primary_key": true, ...},
                ...
              ]
            }
          ]
        }
      ],
      "total_tables": 2,
      "total_schemas": 1,
      "connected": true
    }
    ```

    Si la BD no está conectada, devuelve estructura vacía con
    `connected=false` — el frontend muestra "Sin conexión" sin crashear.
    """
    from geo_copilot.semantic.introspector import SchemaIntrospector

    if not db_pool:
        return {
            "schemas": [],
            "total_tables": 0,
            "total_schemas": 0,
            "connected": False,
        }

    introspector = SchemaIntrospector(db_pool)
    try:
        full = await introspector.full_schema()
        full["connected"] = True
        return full
    except Exception as e:
        logger.error(f"[/metadata/tables] introspection failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="Schema introspection failed — check DB connection",
        ) from e
