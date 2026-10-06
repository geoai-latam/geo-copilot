"""El CATÁLOGO interno: buscar en él, perfilar un dataset, validar su calidad y sugerir joins.

Salió de `DataAgent` (F4 del plan de calidad: agent.py tenía 971 líneas), tal cual.
"""

from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.semantic.layer import SemanticLayer

logger = get_logger("geo_copilot.agents.data_agent.agent")


class CatalogoMixin:
    """El CATÁLOGO interno: buscar en él, perfilar un dataset, validar su calidad y sugerir joins."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        semantic_layer: SemanticLayer | None

    async def search_internal_catalog(
        self,
        keywords: list[str] | None = None,
        entities: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Buscar en el catálogo interno de datos.

        Args:
            keywords: Palabras clave de búsqueda
            entities: Nombres de entidades a buscar

        Returns:
            Diccionario con resultados de búsqueda
        """
        results: dict[str, Any] = {
            "found_entities": [],
            "suggested_tables": [],
            "metadata": []
        }

        if not self.semantic_layer:
            logger.warning("No semantic layer configured")
            return results

        # Buscar por entidades específicas
        if entities:
            for entity_name in entities:
                entity = self.semantic_layer.get_entity(entity_name)
                if entity:
                    results["found_entities"].append({
                        "name": entity.name,
                        "description": entity.description,
                        "table": f"{entity.schema_name}.{entity.table}",
                        "geometry_type": entity.geometry_type,
                        "fields": list(entity.fields.keys()),
                        "aliases": entity.aliases
                    })

        # Buscar por keywords
        if keywords:
            for keyword in keywords:
                found = self.semantic_layer.find_entity(keyword)
                for entity in found:
                    if entity.name not in [e["name"] for e in results["found_entities"]]:
                        results["found_entities"].append({
                            "name": entity.name,
                            "description": entity.description,
                            "table": f"{entity.schema_name}.{entity.table}",
                            "geometry_type": entity.geometry_type,
                            "fields": list(entity.fields.keys())
                        })

        logger.info(f"Internal catalog search found {len(results['found_entities'])} entities")
        return results

    async def profile_dataset(
        self,
        table_name: str | None = None,
        entity_name: str | None = None
    ) -> dict[str, Any]:
        """
        Perfilar un dataset para entender su estructura y calidad.

        Args:
            table_name: Nombre de la tabla a perfilar
            entity_name: Nombre de la entidad semántica

        Returns:
            Perfil del dataset
        """
        profile: dict[str, Any] = {
            "structure": {},
            "quality": {},
            "statistics": {},
            "recommendations": [],
            "fields": {}
        }

        # Obtener información de la capa semántica
        if entity_name and self.semantic_layer:
            entity = self.semantic_layer.get_entity(entity_name)
            if entity:
                fields_info = {
                    name: {
                        "type": field.type,
                        "description": field.description
                    }
                    for name, field in entity.fields.items()
                }

                profile["entity"] = entity_name
                profile["structure"] = {
                    "table": f"{entity.schema_name}.{entity.table}",
                    "geometry_type": entity.geometry_type,
                    "srid": entity.srid,
                    "fields": fields_info
                }

                profile["fields"] = fields_info

                profile["quality"] = {
                    "completeness": "Pending DB connection",
                    "null_counts": {},
                    "unique_counts": {}
                }

                profile["recommendations"] = [
                    "Configure database connection to enable full profiling",
                    f"Entity has {len(entity.metrics)} predefined metrics available"
                ]
            else:
                profile["error"] = f"Entity '{entity_name}' not found in semantic layer"
        else:
            profile["error"] = "No entity_name provided or semantic_layer not configured"

        return profile

    async def validate_data_quality(
        self,
        data: dict[str, Any],
        schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Validar la calidad de un dataset.

        Args:
            data: Datos a validar
            schema: Esquema esperado

        Returns:
            Resultados de validación
        """
        validation_results: dict[str, Any] = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "metrics": {}
        }

        if not data:
            validation_results["valid"] = False
            validation_results["errors"].append("Empty dataset")
            return validation_results

        if schema:
            validation_results["warnings"].append(
                "Schema validation pending Great Expectations integration"
            )

        return validation_results

    async def suggest_joins(
        self,
        entities: list[str]
    ) -> dict[str, Any]:
        """
        Sugerir posibles uniones entre entidades.

        Args:
            entities: Lista de nombres de entidades

        Returns:
            Sugerencias de joins
        """
        suggestions: dict[str, Any] = {
            "spatial_joins": [],
            "attribute_joins": [],
            "recommendations": []
        }

        if not self.semantic_layer:
            return suggestions

        for entity_name in entities:
            relationships = self.semantic_layer.get_relationships_for_entity(entity_name)
            for rel in relationships:
                suggestions["spatial_joins"].append({
                    "from": rel.from_entity,
                    "to": rel.to_entity,
                    "type": rel.type,
                    "description": rel.description
                })

        if len(entities) > 1:
            suggestions["recommendations"].append(
                "Consider using ST_Intersects for spatial joins between polygon entities"
            )
            suggestions["recommendations"].append(
                "Add ST_Simplify to geometry results for better performance"
            )

        return suggestions
