"""
Tests para la capa semántica.
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from geo_copilot.semantic.layer import SemanticLayer


@pytest.fixture
def sample_semantic_config():
    """Configuración semántica de ejemplo para tests."""
    return {
        "version": "1.0",
        "entities": {
            "parcela": {
                "description": "Unidad catastral de terreno",
                "aliases": ["predio", "lote", "terreno"],
                "table": "cat_parcelas",
                "schema": "catastro",
                "geometry_column": "geom",
                "geometry_type": "POLYGON",
                "srid": 4326,
                "fields": {
                    "id": {
                        "column": "id_parcela",
                        "type": "string",
                        "description": "ID único",
                        "primary_key": True
                    },
                    "area": {
                        "column": "area_m2",
                        "type": "float",
                        "unit": "m2",
                        "description": "Área en metros cuadrados"
                    },
                    "propietario": {
                        "column": "nombre_prop",
                        "type": "string",
                        "sensitive": True
                    }
                },
                "metrics": [
                    {
                        "name": "area_total",
                        "expression": "SUM(area_m2)",
                        "description": "Área total"
                    }
                ]
            },
            "municipio": {
                "description": "División administrativa",
                "aliases": ["ciudad", "localidad"],
                "table": "div_municipios",
                "schema": "admin",
                "geometry_column": "geom",
                "geometry_type": "MULTIPOLYGON",
                "srid": 4326,
                "fields": {
                    "codigo": {
                        "column": "cod_mun",
                        "type": "string",
                        "primary_key": True
                    },
                    "nombre": {
                        "column": "nom_mun",
                        "type": "string"
                    }
                }
            }
        },
        "relationships": {
            "parcela_municipio": {
                "from_entity": "parcela",
                "to_entity": "municipio",
                "type": "spatial_within",
                "description": "Parcelas dentro de municipio"
            }
        },
        "analysis_templates": {
            "proximidad": {
                "description": "Análisis de proximidad",
                "parameters": [
                    {"name": "distance", "type": "float"}
                ]
            }
        }
    }


@pytest.fixture
def semantic_layer(sample_semantic_config):
    """Crear capa semántica de prueba."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(sample_semantic_config, f)
        temp_path = f.name

    layer = SemanticLayer(temp_path)
    yield layer

    # Cleanup
    Path(temp_path).unlink()


class TestSemanticLayer:
    """Tests para SemanticLayer."""

    def test_load_entities(self, semantic_layer):
        """Test carga de entidades."""
        entities = semantic_layer.list_entities()
        assert len(entities) == 2
        assert "parcela" in entities
        assert "municipio" in entities

    def test_get_entity_by_name(self, semantic_layer):
        """Test obtener entidad por nombre."""
        entity = semantic_layer.get_entity("parcela")
        assert entity is not None
        assert entity.name == "parcela"
        assert entity.table == "cat_parcelas"
        assert entity.geometry_type == "POLYGON"

    def test_get_entity_by_alias(self, semantic_layer):
        """Test obtener entidad por alias."""
        entity = semantic_layer.get_entity("predio")
        assert entity is not None
        assert entity.name == "parcela"

        entity = semantic_layer.get_entity("lote")
        assert entity is not None
        assert entity.name == "parcela"

    def test_get_entity_not_found(self, semantic_layer):
        """Test entidad no encontrada."""
        entity = semantic_layer.get_entity("no_existe")
        assert entity is None

    def test_find_entity_by_keyword(self, semantic_layer):
        """Test buscar entidades por palabra clave."""
        results = semantic_layer.find_entity("catastral")
        assert len(results) == 1
        assert results[0].name == "parcela"

    def test_get_table_reference(self, semantic_layer):
        """Test obtener referencia completa de tabla."""
        ref = semantic_layer.get_table_reference("parcela")
        assert ref == "catastro.cat_parcelas"

        ref = semantic_layer.get_table_reference("municipio")
        assert ref == "admin.div_municipios"

    def test_get_field_column(self, semantic_layer):
        """Test obtener nombre de columna física."""
        column = semantic_layer.get_field_column("parcela", "area")
        assert column == "area_m2"

        column = semantic_layer.get_field_column("parcela", "id")
        assert column == "id_parcela"

    def test_entity_fields(self, semantic_layer):
        """Test campos de entidad."""
        entity = semantic_layer.get_entity("parcela")
        assert "id" in entity.fields
        assert "area" in entity.fields
        assert "propietario" in entity.fields

        # Verificar propiedades de campo
        area_field = entity.fields["area"]
        assert area_field.column == "area_m2"
        assert area_field.type == "float"
        assert area_field.unit == "m2"

        # Verificar campo sensible
        prop_field = entity.fields["propietario"]
        assert prop_field.sensitive is True

    def test_entity_metrics(self, semantic_layer):
        """Test métricas de entidad."""
        entity = semantic_layer.get_entity("parcela")
        assert len(entity.metrics) == 1
        assert entity.metrics[0].name == "area_total"

    def test_get_relationships(self, semantic_layer):
        """Test obtener relaciones."""
        relationships = semantic_layer.get_relationships_for_entity("parcela")
        assert len(relationships) == 1
        assert relationships[0].name == "parcela_municipio"
        assert relationships[0].type == "spatial_within"

    def test_get_analysis_template(self, semantic_layer):
        """Test obtener plantilla de análisis."""
        template = semantic_layer.get_analysis_template("proximidad")
        assert template is not None
        assert "parameters" in template

    def test_get_context_for_llm(self, semantic_layer):
        """Test generar contexto para LLM."""
        context = semantic_layer.get_context_for_llm()
        assert "parcela" in context
        assert "municipio" in context
        assert "catastro.cat_parcelas" in context
        # Campo sensible no debe estar
        assert "propietario" not in context

    def test_to_dict(self, semantic_layer):
        """Test exportar como diccionario."""
        data = semantic_layer.to_dict()
        assert "entities" in data
        assert "relationships" in data
        assert "templates" in data
        assert len(data["entities"]) == 2


class TestSemanticLayerEdgeCases:
    """Tests de casos borde."""

    def test_empty_config_file(self):
        """Test archivo de configuración vacío."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("")
            temp_path = f.name

        layer = SemanticLayer(temp_path)
        assert len(layer.list_entities()) == 0

        Path(temp_path).unlink()

    def test_nonexistent_file(self):
        """Test archivo inexistente."""
        layer = SemanticLayer("/path/to/nonexistent.yaml")
        assert len(layer.list_entities()) == 0

    def test_case_insensitive_alias(self, semantic_layer):
        """Test aliases son case-insensitive."""
        assert semantic_layer.get_entity("PREDIO") is not None
        assert semantic_layer.get_entity("Predio") is not None
        assert semantic_layer.get_entity("predio") is not None
