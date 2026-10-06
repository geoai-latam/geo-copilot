"""
Tests del SchemaIntrospector — descubrimiento dinámico del schema de la BD.

Dos niveles:

1. **Unit tests** (siempre corren): verifican que el introspector maneja
   bien el caso `db_pool=None` (sin conexión) sin crash. Mock-free.

2. **Integration tests** (`@pytest.mark.integration`): contra la BD real
   con schema `catastro` cargado. Skipean si Docker no está arriba.

El segundo nivel es CRÍTICO: garantiza que el reemplazo del YAML hardcoded
funciona contra el dump real del usuario (2.4M construcciones + 933K lotes).
"""

from __future__ import annotations

import pytest

from geo_copilot.semantic.introspector import (
    IntrospectedColumn,
    IntrospectedTable,
    SchemaIntrospector,
    table_to_entity_dict,
)


# =============================================================================
# Unit tests (sin BD)
# =============================================================================
@pytest.mark.asyncio
async def test_introspector_without_db_pool_returns_empty():
    """Con `db_pool=None` el introspector responde estructura vacía sin crash."""
    intro = SchemaIntrospector(db_pool=None)
    assert await intro.is_connected() is False
    assert await intro.list_schemas() == []
    assert await intro.list_tables() == []
    assert await intro.describe("foo", "bar") is None
    full = await intro.full_schema()
    assert full["schemas"] == []
    assert full["total_tables"] == 0
    assert full["total_schemas"] == 0


def test_table_to_entity_dict_generates_aliases_from_plural():
    """`construcciones` → debe generar aliases `construccion` (singular)."""
    table = IntrospectedTable(
        schema="catastro",
        name="construcciones",
        columns=[
            IntrospectedColumn(name="objectid", data_type="integer", is_nullable=False, is_primary_key=True),
            IntrospectedColumn(name="connpisos", data_type="smallint", is_nullable=True),
            IntrospectedColumn(name="shape", data_type="geometry", is_nullable=True, is_geometry=True, geometry_type="POLYGON", srid=4326),
        ],
        geometry_column="shape",
        geometry_type="POLYGON",
        srid=4326,
        estimated_rows=2_409_199,
    )
    entity = table_to_entity_dict(table)

    assert entity["name"] == "construcciones"
    assert entity["schema_name"] == "catastro"
    assert entity["table"] == "construcciones"
    assert entity["geometry_column"] == "shape"
    assert entity["geometry_type"] == "POLYGON"
    assert entity["srid"] == 4326
    # Aliases: incluyen el singular `construccion`.
    assert "construccion" in entity["aliases"]
    # La columna geométrica NO va en `fields` (va en geometry_column).
    assert "shape" not in entity["fields"]
    # Las demás columnas SÍ.
    assert "objectid" in entity["fields"]
    assert "connpisos" in entity["fields"]
    # PK detectada.
    assert entity["fields"]["objectid"]["primary_key"] is True
    # Métrica `cantidad` siempre presente.
    assert any(m["name"] == "cantidad" for m in entity["metrics"])


def test_table_to_entity_dict_for_lotes():
    """`lotes` → aliases `lote`, `lotes`."""
    table = IntrospectedTable(
        schema="catastro",
        name="lotes",
        columns=[
            IntrospectedColumn(name="objectid", data_type="integer", is_nullable=False, is_primary_key=True),
            IntrospectedColumn(name="shape", data_type="geometry", is_nullable=True, is_geometry=True, geometry_type="POLYGON", srid=4326),
        ],
        geometry_column="shape",
        geometry_type="POLYGON",
        srid=4326,
    )
    entity = table_to_entity_dict(table)
    assert "lote" in entity["aliases"]


def test_table_to_entity_dict_description_with_row_count():
    """La description menciona el count de filas si > 0."""
    table = IntrospectedTable(
        schema="catastro",
        name="construcciones",
        estimated_rows=2_409_199,
    )
    entity = table_to_entity_dict(table)
    assert "2,409,199" in entity["description"]


def test_table_to_entity_dict_no_geometry_uses_sentinels():
    """Tabla sin geometría → geometry_column='geom' (default técnico),
    srid=0 + type='GEOMETRY' (sentinels honestos).

    Decisión 2026-05-25 (ver ``introspector.py:353-366``): el introspector
    ya NO inventa ``srid=4326`` ni ``type='POLYGON'`` cuando PostGIS no los
    reporta. Devuelve ``srid=0`` / ``type='GEOMETRY'`` con un WARNING en
    logs, para que los consumidores (SQL gen, simbología) sepan que la
    geometría es untyped y, si necesitan SRID, exijan ``ST_SetSRID``
    explícito. Test viejo asertaba ``srid==4326`` — quedó desactualizado.
    """
    table = IntrospectedTable(schema="public", name="metadata", columns=[])
    entity = table_to_entity_dict(table)
    assert entity["geometry_column"] == "geom"
    assert entity["srid"] == 0
    assert entity["geometry_type"] == "GEOMETRY"


# =============================================================================
# Integration tests — requieren PostGIS levantado con seed_catastro.sql
# =============================================================================
pytestmark_integration = pytest.mark.integration


@pytest_asyncio_mark := pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_list_schemas_includes_catastro(postgis_pool):
    """El seed crea schema `catastro` — debe aparecer en list_schemas."""
    intro = SchemaIntrospector(postgis_pool)
    schemas = await intro.list_schemas()
    assert "catastro" in schemas, f"schema catastro no encontrado en: {schemas}"
    # NO debe incluir internos.
    assert "pg_catalog" not in schemas
    assert "information_schema" not in schemas
    assert "topology" not in schemas


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_list_tables_finds_construcciones_and_lotes(postgis_pool):
    """Tablas reales del seed deben aparecer con geometría detectada."""
    intro = SchemaIntrospector(postgis_pool)
    tables = await intro.list_tables(schema="catastro")
    names = {t.name for t in tables}
    assert "construcciones" in names
    assert "lotes" in names

    constr = next(t for t in tables if t.name == "construcciones")
    # Geometry detectada en columna `shape`.
    assert constr.geometry_column == "shape"
    # El seed (como el dump real) declara `shape geometry` SIN typmod, y el
    # introspector no inventa el tipo: reporta el genérico (introspector.py,
    # "NO inventamos POLYGON/4326"). Este test esperaba POLYGON y nunca corrió.
    assert constr.geometry_type == "GEOMETRY"
    assert constr.srid == 4326
    # Tiene columnas user.
    col_names = {c.name for c in constr.columns}
    assert "objectid" in col_names
    assert "connpisos" in col_names
    assert "shape" in col_names
    # Estimated rows positivo (el seed tiene 25).
    assert constr.estimated_rows is not None and constr.estimated_rows >= 0


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_describe_returns_full_table_metadata(postgis_pool):
    intro = SchemaIntrospector(postgis_pool)
    table = await intro.describe("catastro", "construcciones")
    assert table is not None
    assert table.qualified_name == "catastro.construcciones"

    # Primary key detectada.
    pk_cols = [c for c in table.columns if c.is_primary_key]
    assert any(c.name == "objectid" for c in pk_cols), (
        f"objectid debió ser detectado como PK: {[c.name for c in pk_cols]}"
    )

    # Geometry column con SRID válido.
    geom_cols = [c for c in table.columns if c.is_geometry]
    assert len(geom_cols) == 1
    assert geom_cols[0].srid == 4326


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_describe_nonexistent_table_returns_none(postgis_pool):
    intro = SchemaIntrospector(postgis_pool)
    result = await intro.describe("catastro", "tabla_que_no_existe_xyz")
    assert result is None


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_full_schema_returns_serializable_tree(postgis_pool):
    """`full_schema()` devuelve estructura JSON-serializable para el endpoint REST."""
    import json
    intro = SchemaIntrospector(postgis_pool)
    full = await intro.full_schema()

    # Estructura top-level.
    assert "schemas" in full
    assert "total_tables" in full
    assert "total_schemas" in full
    assert full["total_tables"] > 0

    # Schema catastro presente.
    schema_names = [s["name"] for s in full["schemas"]]
    assert "catastro" in schema_names

    # Cada tabla tiene los campos requeridos.
    catastro = next(s for s in full["schemas"] if s["name"] == "catastro")
    constr = next(t for t in catastro["tables"] if t["name"] == "construcciones")
    assert "qualified_name" in constr
    assert "geometry_column" in constr
    assert "columns" in constr
    assert all("type" in c for c in constr["columns"])

    # Es serializable a JSON sin custom encoders.
    json_str = json.dumps(full)
    assert json_str  # smoke check
