"""Regresión C3d-5: resiliencia del SemanticLayer.

1. YAML malformado no debe tumbar __init__ (degrada a sin entidades).
2. hydrate_from_database es atómico: un fallo de DB no debe dejar la capa
   con 0 entidades — conserva las del YAML.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from geo_copilot.semantic.layer import SemanticLayer

_VALID_YAML = """
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
      id: {column: id_parcela, type: string}
"""


def _write(content: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def test_malformed_yaml_does_not_crash_init():
    path = _write("entities: [this is : not : valid : yaml")
    # No debe lanzar; degrada a sin entidades.
    layer = SemanticLayer(path)
    assert layer.list_entities() == []
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_hydrate_failure_preserves_yaml_entities():
    path = _write(_VALID_YAML)
    layer = SemanticLayer(path)
    assert "parcela" in layer.list_entities()

    # Pool cuya introspección está "conectada" pero list_tables revienta.
    class _Introspector:
        def __init__(self, pool):
            pass

        async def is_connected(self):
            return True

        async def list_tables(self):
            raise RuntimeError("DB cayó a mitad")

    import geo_copilot.semantic.introspector as intro_mod

    orig = intro_mod.SchemaIntrospector
    intro_mod.SchemaIntrospector = _Introspector
    try:
        hydrated = await layer.hydrate_from_database(db_pool=object())
    finally:
        intro_mod.SchemaIntrospector = orig

    # El fallo no debe vaciar la capa.
    assert hydrated == 0
    assert "parcela" in layer.list_entities()
    Path(path).unlink(missing_ok=True)
