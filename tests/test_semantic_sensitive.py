"""Regresión C3d-6 (security-adjacent): el flag `sensitive` del YAML debe
propagarse a la columna FÍSICA tras hidratar desde la BD.

El introspector llave los campos por nombre de columna físico; el YAML
los llave por nombre semántico. El match anterior (semántico vs físico)
nunca coincidía, así que `sensitive: true` no se propagaba y la columna
sensible quedaba expuesta en el contexto LLM.
"""

import tempfile
from pathlib import Path

import pytest

from geo_copilot.semantic.introspector import IntrospectedColumn, IntrospectedTable
from geo_copilot.semantic.layer import SemanticLayer

_YAML = """
version: "1.0"
entities:
  parcela:
    description: "Unidad catastral"
    aliases: ["predio"]
    table: cat_parcelas
    schema: catastro
    geometry_column: geom
    geometry_type: POLYGON
    srid: 4326
    fields:
      # clave SEMÁNTICA 'propietario' → columna FÍSICA 'nombre_prop'
      propietario: {column: nombre_prop, type: string, sensitive: true}
      area: {column: area_m2, type: float, unit: "m2"}
"""


def _write(content):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def _fake_table():
    return IntrospectedTable(
        schema="catastro",
        name="cat_parcelas",
        columns=[
            IntrospectedColumn(name="id_parcela", data_type="text", is_nullable=False,
                               is_primary_key=True),
            IntrospectedColumn(name="nombre_prop", data_type="text", is_nullable=True),
            IntrospectedColumn(name="area_m2", data_type="double precision", is_nullable=True),
            IntrospectedColumn(name="geom", data_type="geometry", is_nullable=True,
                               is_geometry=True, geometry_type="POLYGON", srid=4326),
        ],
        geometry_column="geom",
        geometry_type="POLYGON",
        srid=4326,
    )


@pytest.mark.asyncio
async def test_sensitive_flag_propagates_to_physical_column(monkeypatch):
    path = _write(_YAML)
    layer = SemanticLayer(path)

    class _Introspector:
        def __init__(self, pool):
            pass

        async def is_connected(self):
            return True

        async def list_tables(self):
            return [_fake_table()]

    import geo_copilot.semantic.introspector as intro_mod

    monkeypatch.setattr(intro_mod, "SchemaIntrospector", _Introspector)

    hydrated = await layer.hydrate_from_database(db_pool=object())
    assert hydrated == 1

    entity = layer.get_entity("parcela") or layer.get_entity("cat_parcelas")
    assert entity is not None
    # La columna física 'nombre_prop' debe quedar marcada sensible.
    assert entity.fields["nombre_prop"].sensitive is True
    # y la unidad de 'area_m2' preservada.
    assert entity.fields["area_m2"].unit == "m2"

    Path(path).unlink(missing_ok=True)
