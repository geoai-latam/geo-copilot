"""
Herramientas de búsqueda en catálogo interno.
"""

from typing import Any

from geo_copilot.core.logging import get_logger
from geo_copilot.semantic.layer import SemanticLayer

logger = get_logger(__name__)


async def search_internal_catalog(
    semantic_layer: SemanticLayer,
    keywords: list[str] | None = None,
    entity_types: list[str] | None = None,
    geometry_types: list[str] | None = None
) -> dict[str, Any]:
    """
    Buscar datasets en el catálogo interno.

    Args:
        semantic_layer: Capa semántica cargada
        keywords: Palabras clave para buscar
        entity_types: Tipos de entidad (parcela, municipio, etc.)
        geometry_types: Tipos de geometría (POINT, POLYGON, etc.)

    Returns:
        Diccionario con resultados de búsqueda
    """
    results: dict[str, Any] = {
        "matches": [],
        "total_found": 0,
        "search_criteria": {
            "keywords": keywords or [],
            "entity_types": entity_types or [],
            "geometry_types": geometry_types or []
        }
    }

    # Buscar por keywords
    if keywords:
        for keyword in keywords:
            found_entities = semantic_layer.find_entity(keyword)
            for entity in found_entities:
                # Filtrar por geometry_type si se especificó
                if geometry_types and entity.geometry_type not in geometry_types:
                    continue

                # Filtrar por entity_type si se especificó
                if entity_types:
                    if not any(et.lower() in entity.name.lower() for et in entity_types):
                        continue

                # Evitar duplicados
                if entity.name not in [m["name"] for m in results["matches"]]:
                    results["matches"].append({
                        "name": entity.name,
                        "description": entity.description,
                        "table": f"{entity.schema_name}.{entity.table}",
                        "geometry_type": entity.geometry_type,
                        "srid": entity.srid,
                        "fields": list(entity.fields.keys()),
                        "aliases": entity.aliases,
                        "metrics": [m.name for m in entity.metrics]
                    })

    # Si no hay keywords, listar todas las entidades
    if not keywords and not entity_types and not geometry_types:
        for entity_name in semantic_layer.list_entities():
            listada = semantic_layer.get_entity(entity_name)
            if listada:
                results["matches"].append({
                    "name": listada.name,
                    "description": listada.description,
                    "table": f"{listada.schema_name}.{listada.table}",
                    "geometry_type": listada.geometry_type
                })

    results["total_found"] = len(results["matches"])
    logger.info(f"Catalog search found {results['total_found']} matches")

    return results


async def profile_dataset(
    semantic_layer: SemanticLayer,
    entity_name: str,
    db_connection: Any | None = None
) -> dict[str, Any]:
    """
    Generar perfil de un dataset.

    Args:
        semantic_layer: Capa semántica
        entity_name: Nombre de la entidad a perfilar
        db_connection: Conexión a la base de datos (opcional)

    Returns:
        Perfil del dataset
    """
    entity = semantic_layer.get_entity(entity_name)
    if not entity:
        return {
            "error": f"Entity not found: {entity_name}",
            "available_entities": semantic_layer.list_entities()
        }

    profile: dict[str, Any] = {
        "entity": entity.name,
        "description": entity.description,
        "structure": {
            "table": f"{entity.schema_name}.{entity.table}",
            "geometry_column": entity.geometry_column,
            "geometry_type": entity.geometry_type,
            "srid": entity.srid
        },
        "fields": {},
        "metrics_available": [],
        "relationships": [],
        "statistics": None
    }

    # Detallar campos
    for name, field in entity.fields.items():
        profile["fields"][name] = {
            "column": field.column,
            "type": field.type,
            "description": field.description,
            "unit": field.unit,
            "is_primary_key": field.primary_key,
            "is_sensitive": field.sensitive
        }
        if field.enum:
            profile["fields"][name]["allowed_values"] = field.enum

    # Métricas disponibles
    profile["metrics_available"] = [
        {"name": m.name, "description": m.description, "expression": m.expression}
        for m in entity.metrics
    ]

    # Relaciones
    relationships = semantic_layer.get_relationships_for_entity(entity_name)
    profile["relationships"] = [
        {
            "name": r.name,
            "related_entity": r.to_entity if r.from_entity == entity_name else r.from_entity,
            "type": r.type,
            "description": r.description
        }
        for r in relationships
    ]

    # Si hay conexión a BD, obtener estadísticas
    if db_connection:
        # TODO: Ejecutar queries de estadísticas
        profile["statistics"] = {
            "note": "Connect to database to get live statistics"
        }

    return profile


async def suggest_joins(
    semantic_layer: SemanticLayer,
    entity_names: list[str]
) -> dict[str, Any]:
    """
    Sugerir joins posibles entre entidades.

    Args:
        semantic_layer: Capa semántica
        entity_names: Lista de entidades a relacionar

    Returns:
        Sugerencias de joins
    """
    suggestions: dict[str, Any] = {
        "entities": entity_names,
        "spatial_joins": [],
        "relationships": [],
        "sql_examples": []
    }

    # Verificar que todas las entidades existan
    valid_entities = []
    for name in entity_names:
        entity = semantic_layer.get_entity(name)
        if entity:
            valid_entities.append(entity)
        else:
            suggestions["warnings"] = suggestions.get("warnings", [])
            suggestions["warnings"].append(f"Entity not found: {name}")

    if len(valid_entities) < 2:
        suggestions["error"] = "Need at least 2 valid entities to suggest joins"
        return suggestions

    # Buscar relaciones definidas
    for entity in valid_entities:
        relationships = semantic_layer.get_relationships_for_entity(entity.name)
        for rel in relationships:
            if rel.from_entity in entity_names and rel.to_entity in entity_names:
                if rel.name not in [r["name"] for r in suggestions["relationships"]]:
                    suggestions["relationships"].append({
                        "name": rel.name,
                        "from": rel.from_entity,
                        "to": rel.to_entity,
                        "type": rel.type,
                        "description": rel.description
                    })

    # Sugerir joins espaciales basados en geometrías
    for i, entity1 in enumerate(valid_entities):
        for entity2 in valid_entities[i + 1:]:
            join_type = _suggest_spatial_join_type(entity1.geometry_type, entity2.geometry_type)
            suggestions["spatial_joins"].append({
                "entity1": entity1.name,
                "entity2": entity2.name,
                "suggested_function": join_type,
                "example": _generate_join_example(entity1, entity2, join_type)
            })

    return suggestions


def _suggest_spatial_join_type(geom1: str, geom2: str) -> str:
    """Sugerir tipo de join espacial según geometrías."""
    if geom1 == "POINT":
        if geom2 in ["POLYGON", "MULTIPOLYGON"]:
            return "ST_Within"
        return "ST_DWithin"
    elif geom1 in ["POLYGON", "MULTIPOLYGON"]:
        if geom2 == "POINT":
            return "ST_Contains"
        return "ST_Intersects"
    elif geom1 == "LINESTRING":
        return "ST_Intersects"
    return "ST_Intersects"


def _generate_join_example(entity1, entity2, join_func: str) -> str:
    """Generar ejemplo SQL de join."""
    return f"""SELECT
    a.*,
    b.{list(entity2.fields.keys())[0] if entity2.fields else 'id'}
FROM {entity1.schema_name}.{entity1.table} a
JOIN {entity2.schema_name}.{entity2.table} b
    ON {join_func}(a.{entity1.geometry_column}, b.{entity2.geometry_column})
LIMIT 100;"""
