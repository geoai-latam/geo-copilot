"""
Tests para el Agente de Datos.
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from geo_copilot.agents.data_agent import DataAgent
from geo_copilot.security.hitl import HITLManager, HITLStatus
from geo_copilot.semantic.layer import SemanticLayer


@pytest.fixture
def sample_semantic_config():
    """Configuración semántica de ejemplo."""
    return {
        "version": "1.0",
        "entities": {
            "parcela": {
                "description": "Unidad catastral",
                "aliases": ["predio", "lote"],
                "table": "cat_parcelas",
                "schema": "catastro",
                "geometry_column": "geom",
                "geometry_type": "POLYGON",
                "srid": 4326,
                "fields": {
                    "id": {"column": "id_parcela", "type": "string"},
                    "area": {"column": "area_m2", "type": "float"}
                },
                "metrics": [
                    {"name": "area_total", "expression": "SUM(area_m2)", "description": ""}
                ]
            },
            "municipio": {
                "description": "División administrativa",
                "aliases": ["ciudad"],
                "table": "div_municipios",
                "schema": "admin",
                "geometry_column": "geom",
                "geometry_type": "MULTIPOLYGON",
                "srid": 4326,
                "fields": {
                    "codigo": {"column": "cod_mun", "type": "string"},
                    "nombre": {"column": "nom_mun", "type": "string"}
                }
            }
        },
        "relationships": {
            "parcela_municipio": {
                "from_entity": "parcela",
                "to_entity": "municipio",
                "type": "spatial_within",
                "description": "Parcelas en municipio"
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
    Path(temp_path).unlink()


@pytest.fixture
def hitl_manager():
    """Crear gestor HITL de prueba."""
    return HITLManager(timeout=5)


@pytest.fixture
def data_agent(semantic_layer, hitl_manager):
    """Crear agente de datos de prueba."""
    return DataAgent(
        semantic_layer=semantic_layer,
        hitl_manager=hitl_manager,
        llm_client=None  # Sin LLM para tests unitarios
    )


class TestDataAgent:
    """Tests para DataAgent."""

    def test_agent_initialization(self, data_agent):
        """Test inicialización del agente."""
        assert data_agent.name == "DataAgent"
        assert len(data_agent.get_tools()) > 0

    def test_agent_capabilities(self, data_agent):
        """Test capacidades del agente."""
        caps = data_agent.get_capabilities()
        assert "internal" in caps
        assert "external" in caps
        assert "validation" in caps
        assert "search_catalog" in caps["internal"]

    def test_registered_tools(self, data_agent):
        """Test herramientas registradas."""
        tools = data_agent.get_tools()
        assert "search_internal_catalog" in tools
        assert "search_open_data" in tools
        assert "profile_dataset" in tools
        assert "validate_data_quality" in tools

    @pytest.mark.asyncio
    async def test_search_internal_catalog(self, data_agent):
        """Test búsqueda en catálogo interno."""
        results = await data_agent.search_internal_catalog(
            keywords=["parcela"],
            entities=[]
        )

        assert "found_entities" in results
        assert len(results["found_entities"]) >= 1

        # Verificar que encontró parcela
        entity_names = [e["name"] for e in results["found_entities"]]
        assert "parcela" in entity_names

    @pytest.mark.asyncio
    async def test_search_by_alias(self, data_agent):
        """Test búsqueda por alias."""
        results = await data_agent.search_internal_catalog(
            keywords=["predio"],
            entities=[]
        )

        assert len(results["found_entities"]) >= 1
        # predio es alias de parcela
        entity_names = [e["name"] for e in results["found_entities"]]
        assert "parcela" in entity_names

    @pytest.mark.asyncio
    async def test_suggest_joins(self, data_agent):
        """Test sugerencias de joins."""
        suggestions = await data_agent.suggest_joins(
            entities=["parcela", "municipio"]
        )

        assert "spatial_joins" in suggestions
        assert len(suggestions["spatial_joins"]) > 0

    @pytest.mark.asyncio
    async def test_profile_dataset(self, data_agent):
        """El profile de una entidad debe contener todos los datos clave
        del semantic layer, no solo las claves top-level vacías.

        Antes (auditoría 2026-05-24): este test solo verificaba `"structure"
        in profile`, `"fields" in profile`, `entity == "parcela"`. Pasaba
        aunque structure/fields fueran dicts vacíos. Ahora valida tipos,
        SRID, geometría, campos críticos."""
        profile = await data_agent.profile_dataset(entity_name="parcela")

        # Identidad básica.
        assert profile["entity"] == "parcela"

        # `structure` debe tener la info real de la tabla — no estar vacío.
        structure = profile.get("structure")
        assert isinstance(structure, dict) and structure, (
            "structure debe ser un dict NO vacío"
        )
        # Schema + tabla físicos referenciados (sin esto el SQL no se puede ejecutar).
        assert structure.get("table"), "falta `table` en structure"
        assert "." in structure["table"], (
            f"table debe ser schema.tabla (ej. 'catastro.parcelas'), "
            f"recibí {structure['table']!r}"
        )
        # Geometría: tipo y SRID — críticos para análisis espacial.
        assert structure.get("geometry_type"), "falta geometry_type"
        assert structure["geometry_type"].upper() in (
            "POLYGON", "MULTIPOLYGON", "POINT", "LINESTRING",
            "MULTIPOINT", "MULTILINESTRING", "MULTI POLYGON",
        ), f"geometry_type inválido: {structure['geometry_type']}"
        srid = structure.get("srid")
        assert isinstance(srid, int) and srid > 0, (
            f"srid debe ser int positivo, recibí {srid!r}"
        )

        # `fields` debe contener el dict de campos con info de tipo.
        fields = profile.get("fields")
        assert isinstance(fields, dict) and fields, "fields debe ser dict NO vacío"
        # Cada campo declara al menos `type` y `description`.
        for field_name, field_info in fields.items():
            assert isinstance(field_info, dict), (
                f"field {field_name} debe ser dict, recibí {type(field_info)}"
            )
            assert "type" in field_info, (
                f"field {field_name} sin `type` — el LLM no podrá usar el campo"
            )

    @pytest.mark.asyncio
    async def test_profile_nonexistent_entity(self, data_agent):
        """Test perfilado de entidad inexistente."""
        profile = await data_agent.profile_dataset(
            entity_name="no_existe"
        )

        assert "error" in profile


class TestDataAgentValidation:
    """Tests de validación de datos."""

    @pytest.mark.asyncio
    async def test_validate_empty_data(self, data_agent):
        """Test validación de datos vacíos."""
        result = await data_agent.validate_data_quality(
            data={},
            schema=None
        )

        assert result["valid"] is False
        assert "Empty dataset" in result["errors"]

    @pytest.mark.asyncio
    async def test_validate_valid_data(self, data_agent):
        """Test validación de datos válidos."""
        data = [
            {"id": 1, "name": "Test 1", "value": 100},
            {"id": 2, "name": "Test 2", "value": 200},
        ]

        result = await data_agent.validate_data_quality(
            data=data,
            schema=None
        )

        assert "valid" in result


class TestHITLManager:
    """Tests para el gestor HITL."""

    @pytest.mark.asyncio
    async def test_approve_request(self, hitl_manager):
        """Test aprobar solicitud."""
        import asyncio

        # Crear tarea para responder
        async def respond_after_delay():
            await asyncio.sleep(0.1)
            requests = hitl_manager.get_pending_requests()
            if requests:
                await hitl_manager.approve(requests[0].id, approved_by="test_user")

        # Iniciar respuesta
        asyncio.create_task(respond_after_delay())

        # Solicitar aprobación
        from geo_copilot.security.hitl import HITLActionType
        response = await hitl_manager.request_approval(
            action_type=HITLActionType.DATA_IMPORT,
            title="Test Request",
            description="Test description"
        )

        assert response.status == HITLStatus.APPROVED
        assert response.approved_by == "test_user"

    @pytest.mark.asyncio
    async def test_reject_request(self, hitl_manager):
        """Test rechazar solicitud."""
        import asyncio

        async def respond_after_delay():
            await asyncio.sleep(0.1)
            requests = hitl_manager.get_pending_requests()
            if requests:
                await hitl_manager.reject(requests[0].id, feedback="Not approved")

        asyncio.create_task(respond_after_delay())

        from geo_copilot.security.hitl import HITLActionType
        response = await hitl_manager.request_approval(
            action_type=HITLActionType.SQL_EXECUTION,
            title="SQL Query",
            description="SELECT * FROM test"
        )

        assert response.status == HITLStatus.REJECTED
        assert "Not approved" in response.feedback

    @pytest.mark.asyncio
    async def test_request_timeout(self):
        """Test timeout de solicitud."""
        manager = HITLManager(timeout=1)  # 1 segundo timeout

        from geo_copilot.security.hitl import HITLActionType
        response = await manager.request_approval(
            action_type=HITLActionType.CODE_EXECUTION,
            title="Timeout Test",
            description="This will timeout"
        )

        assert response.status == HITLStatus.EXPIRED

    def test_get_pending_requests(self, hitl_manager):
        """Test obtener solicitudes pendientes."""
        requests = hitl_manager.get_pending_requests()
        assert isinstance(requests, list)


class TestDataAgentTools:
    """Tests para herramientas individuales del agente."""

    @pytest.mark.asyncio
    async def test_catalog_search_tool(self, semantic_layer):
        """Test herramienta de búsqueda en catálogo."""
        from geo_copilot.agents.data_agent.tools.catalog_search import search_internal_catalog

        results = await search_internal_catalog(
            semantic_layer=semantic_layer,
            keywords=["parcela", "municipio"]
        )

        assert "matches" in results
        assert results["total_found"] >= 2

    @pytest.mark.asyncio
    async def test_suggest_joins_tool(self, semantic_layer):
        """Test herramienta de sugerencias de joins."""
        from geo_copilot.agents.data_agent.tools.catalog_search import suggest_joins

        suggestions = await suggest_joins(
            semantic_layer=semantic_layer,
            entity_names=["parcela", "municipio"]
        )

        assert "spatial_joins" in suggestions
        assert "sql_examples" in suggestions

    @pytest.mark.asyncio
    async def test_validation_tool(self):
        """Test herramienta de validación."""
        from geo_copilot.agents.data_agent.tools.validation import validate_data_quality

        data = [
            {"id": 1, "value": 100},
            {"id": 2, "value": None},  # Null value
            {"id": 3, "value": 300},
        ]

        result = await validate_data_quality(data)

        assert "valid" in result
        assert "checks" in result
        assert "score" in result

    @pytest.mark.asyncio
    async def test_schema_check_tool(self):
        """Test verificación de esquema."""
        from geo_copilot.agents.data_agent.tools.validation import check_schema

        data = [{"id": 1, "name": "test", "value": 100}]
        schema = {"id": "int", "name": "string", "value": "number"}

        result = await check_schema(data, schema)

        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_schema_check_missing_field(self):
        """Test esquema con campo faltante."""
        from geo_copilot.agents.data_agent.tools.validation import check_schema

        data = [{"id": 1, "name": "test"}]  # Missing 'value'
        schema = {"id": "int", "name": "string", "value": "number"}

        result = await check_schema(data, schema)

        assert result["valid"] is False
        assert any("Missing field" in e for e in result["errors"])


class TestListEntities:
    """Regresión B1: ``list_entities`` usaba ``entity.category`` inexistente.

    ``Entity`` (semantic/layer.py) no tiene campo ``category``; toda llamada
    a ``DataAgent.list_entities()`` lanzaba ``AttributeError``. Estos tests
    fijan el contrato sin ese campo.
    """

    @pytest.mark.asyncio
    async def test_list_entities_returns_all(self, data_agent):
        result = await data_agent.list_entities()

        assert result["success"] is True
        assert result["total"] == 2
        names = {e["name"] for e in result["entities"]}
        assert names == {"parcela", "municipio"}
        # Cada entrada expone los campos reales del modelo Entity.
        for entry in result["entities"]:
            assert "table" in entry
            assert "geometry_type" in entry
            assert "fields_count" in entry

    @pytest.mark.asyncio
    async def test_list_entities_search_term_filters(self, data_agent):
        result = await data_agent.list_entities(search_term="parcela")

        assert result["success"] is True
        assert result["total"] == 1
        assert result["entities"][0]["name"] == "parcela"

    @pytest.mark.asyncio
    async def test_list_entities_without_semantic_layer(self, hitl_manager):
        agent = DataAgent(semantic_layer=None, hitl_manager=hitl_manager)

        result = await agent.list_entities()

        assert result["success"] is False
        assert result["entities"] == []
