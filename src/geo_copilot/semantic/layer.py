"""
Capa Semántica - Mapeo entre conceptos de negocio y esquemas físicos.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


class EntityField(BaseModel):
    """Campo de una entidad."""
    column: str
    type: str
    description: str = ""
    unit: str | None = None
    primary_key: bool = False
    sensitive: bool = False
    enum: list[str] | None = None


class EntityMetric(BaseModel):
    """Métrica calculable de una entidad."""
    name: str
    expression: str
    description: str = ""


class Entity(BaseModel):
    """Entidad del modelo semántico."""
    name: str
    description: str
    aliases: list[str]
    table: str
    schema_name: str
    geometry_column: str
    geometry_type: str
    srid: int
    fields: dict[str, EntityField]
    metrics: list[EntityMetric] = []


class Relationship(BaseModel):
    """Relación entre entidades."""
    name: str
    from_entity: str
    to_entity: str
    type: str
    description: str = ""


class SemanticLayer:
    """
    Capa semántica que mapea conceptos de negocio a esquemas físicos.

    Permite traducir consultas en lenguaje natural a SQL al proporcionar:
    - Mapeo de nombres de negocio a tablas/columnas
    - Aliases para búsqueda flexible
    - Métricas predefinidas
    - Relaciones espaciales entre entidades
    """

    def __init__(self, config_path: str | Path):
        """
        Inicializar la capa semántica desde un archivo YAML.

        Args:
            config_path: Ruta al archivo de configuración YAML
        """
        self.config_path = Path(config_path)
        self._entities: dict[str, Entity] = {}
        self._relationships: dict[str, Relationship] = {}
        self._alias_map: dict[str, str] = {}
        self._analysis_templates: dict[str, dict] = {}
        self._metadata: dict = {}

        self._load_config()

    def _load_config(self) -> None:
        """Cargar configuración desde YAML."""
        if not self.config_path.exists():
            logger.warning(f"Archivo de capa semántica no encontrado: {self.config_path}")
            return

        # C3d-5: YAML malformado no debe tumbar la construcción del
        # SemanticLayer. Antes un yaml.YAMLError propagaba fuera de
        # __init__ y rompía todo el arranque; ahora se degrada a "sin
        # entidades YAML" (la hidratación desde BD sigue disponible).
        try:
            with open(self.config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            logger.error(
                f"YAML de capa semántica inválido ({self.config_path}): {exc} — "
                f"se continúa sin entidades YAML"
            )
            return

        # Manejar archivo YAML vacío
        if config is None:
            logger.warning(f"Archivo de capa semántica vacío: {self.config_path}")
            return

        # Cargar entidades
        for name, entity_data in config.get("entities", {}).items():
            entity = Entity(
                name=name,
                description=entity_data.get("description", ""),
                aliases=entity_data.get("aliases", []),
                table=entity_data.get("table", ""),
                schema_name=entity_data.get("schema", "public"),
                geometry_column=entity_data.get("geometry_column", "geom"),
                geometry_type=entity_data.get("geometry_type", "GEOMETRY"),
                srid=entity_data.get("srid", 4326),
                fields={
                    fname: EntityField(
                        column=fdata.get("column", fname),
                        type=fdata.get("type", "string"),
                        description=fdata.get("description", ""),
                        unit=fdata.get("unit"),
                        primary_key=fdata.get("primary_key", False),
                        sensitive=fdata.get("sensitive", False),
                        enum=fdata.get("enum")
                    )
                    for fname, fdata in entity_data.get("fields", {}).items()
                },
                metrics=[
                    EntityMetric(**m) for m in entity_data.get("metrics", [])
                ]
            )
            self._entities[name] = entity

            # Construir mapa de aliases
            self._alias_map[name.lower()] = name
            for alias in entity.aliases:
                self._alias_map[alias.lower()] = name

        # Cargar relaciones
        for name, rel_data in config.get("relationships", {}).items():
            self._relationships[name] = Relationship(
                name=name,
                from_entity=rel_data.get("from_entity", ""),
                to_entity=rel_data.get("to_entity", ""),
                type=rel_data.get("type", ""),
                description=rel_data.get("description", "")
            )

        # Cargar plantillas de análisis
        self._analysis_templates = config.get("analysis_templates", {})

        # Cargar metadata
        self._metadata = config.get("metadata", {})

        logger.info(
            f"Capa semántica cargada: {len(self._entities)} entidades, "
            f"{len(self._relationships)} relaciones"
        )

    async def hydrate_from_database(self, db_pool: Any) -> int:
        """Carga entidades desde el schema REAL de la BD conectada.

        Reemplaza/extiende las entidades cargadas del YAML con lo que
        realmente existe en la BD. Las entidades del YAML que NO existan
        en la BD se DESCARTAN (probablemente apuntan a tablas obsoletas).
        Las que sí existen, se merge: schema/columnas/srid vienen de la
        BD, aliases/description/sensitive/enum se preservan del YAML.

        Devuelve el número de entidades hidratadas. 0 si no hay conexión.
        """
        from geo_copilot.semantic.introspector import (
            SchemaIntrospector,
            table_to_entity_dict,
        )

        introspector = SchemaIntrospector(db_pool)
        if not await introspector.is_connected():
            logger.warning(
                "[SemanticLayer] No DB connection — keeping YAML entities as-is"
            )
            return 0

        # Snapshot del YAML actual (preservamos overrides por nombre o alias).
        yaml_overrides: dict[str, Entity] = dict(self._entities)

        # C3d-5: build ATÓMICO. Antes se hacía clear() de self._entities/
        # _alias_map ANTES de reconstruir; si list_tables() o el loop
        # fallaban a mitad, la capa quedaba con 0 entidades y sin fallback
        # al YAML. Ahora construimos en dicts locales y solo hacemos el swap
        # si todo el build tuvo éxito; ante cualquier error, self queda
        # intacto (entidades YAML preservadas).
        new_entities: dict[str, Entity] = {}
        new_alias_map: dict[str, str] = {}

        try:
            tables = await introspector.list_tables()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"[SemanticLayer] list_tables falló: {exc} — "
                f"se conservan las entidades YAML"
            )
            return 0

        # Filtra solo tablas con geometría — son las que el GISAgent puede usar.
        geo_tables = [t for t in tables if t.geometry_column]

        for t in geo_tables:
            entity_dict = table_to_entity_dict(t)
            name = entity_dict["name"]

            # Si el usuario ya tenía override en YAML para esta tabla
            # (matching por schema.tabla o por nombre semántico), preservamos
            # los aliases y descripciones del YAML.
            override = self._find_yaml_override(yaml_overrides, t.schema, t.name)
            if override:
                entity_dict["aliases"] = list(set(
                    entity_dict["aliases"] + override.aliases
                ))
                entity_dict["description"] = override.description or entity_dict["description"]
                # Preservar metadata del YAML (sensitive, enum, unit,
                # description) en los campos introspectados.
                #
                # C3d-6 (security-adjacent): el introspector llave
                # ``entity_dict["fields"]`` por nombre de columna FÍSICO
                # (``c.name``), mientras que ``override.fields`` se llave por
                # nombre SEMÁNTICO (la clave del YAML). Antes el match era
                # ``fname (semántico) in fields (físico)`` → casi nunca
                # coincidía, así que el flag ``sensitive: true`` (p. ej. en
                # ``propietario``→``nombre_prop``) NO se propagaba y la
                # columna sensible quedaba expuesta en el contexto LLM.
                # Ahora matcheamos por la columna física (``override_field.column``).
                for override_field in override.fields.values():
                    phys = override_field.column
                    if phys in entity_dict["fields"]:
                        entity_dict["fields"][phys]["sensitive"] = override_field.sensitive
                        entity_dict["fields"][phys]["unit"] = override_field.unit
                        entity_dict["fields"][phys]["enum"] = override_field.enum
                        if override_field.description:
                            entity_dict["fields"][phys]["description"] = (
                                override_field.description
                            )

            entity = Entity(
                name=name,
                description=entity_dict["description"],
                aliases=entity_dict["aliases"],
                table=entity_dict["table"],
                schema_name=entity_dict["schema_name"],
                geometry_column=entity_dict["geometry_column"],
                geometry_type=entity_dict["geometry_type"],
                srid=entity_dict["srid"],
                fields={
                    fname: EntityField(**fdata)
                    for fname, fdata in entity_dict["fields"].items()
                },
                metrics=[
                    EntityMetric(**m) for m in entity_dict["metrics"]
                ],
            )
            new_entities[name] = entity
            new_alias_map[name.lower()] = name
            for alias in entity.aliases:
                new_alias_map[alias.lower()] = name

        # Swap atómico: solo aquí mutamos el estado de la capa.
        self._entities = new_entities
        self._alias_map = new_alias_map

        logger.info(
            f"[SemanticLayer] Hydrated from DB: {len(self._entities)} entities "
            f"({len(geo_tables)} geo tables found)"
        )
        return len(self._entities)

    def _find_yaml_override(
        self,
        yaml_entities: dict[str, Entity],
        schema: str,
        table: str,
    ) -> Entity | None:
        """Busca un override en el YAML que coincida con la tabla real."""
        for ent in yaml_entities.values():
            if ent.schema_name == schema and ent.table == table:
                return ent
        # Fallback: match por nombre de tabla solo (case insensitive).
        for ent in yaml_entities.values():
            if ent.table.lower() == table.lower():
                return ent
        return None

    def get_entity(self, name: str) -> Entity | None:
        """
        Obtener una entidad por nombre o alias.

        Args:
            name: Nombre o alias de la entidad

        Returns:
            Entity si existe, None si no
        """
        # Buscar por nombre directo
        if name in self._entities:
            return self._entities[name]

        # Buscar por alias
        canonical_name = self._alias_map.get(name.lower())
        if canonical_name:
            return self._entities.get(canonical_name)

        return None

    def find_entity(self, query: str) -> list[Entity]:
        """
        Buscar entidades que coincidan con una consulta.

        Args:
            query: Término de búsqueda

        Returns:
            Lista de entidades que coinciden
        """
        query_lower = query.lower()
        results = []

        for name, entity in self._entities.items():
            # Buscar en nombre
            if query_lower in name.lower():
                results.append(entity)
                continue

            # Buscar en aliases
            if any(query_lower in alias.lower() for alias in entity.aliases):
                results.append(entity)
                continue

            # Buscar en descripción
            if query_lower in entity.description.lower():
                results.append(entity)

        return results

    def get_table_reference(self, entity_name: str) -> str | None:
        """
        Obtener referencia completa a la tabla (schema.table).

        Args:
            entity_name: Nombre de la entidad

        Returns:
            Referencia a la tabla o None
        """
        entity = self.get_entity(entity_name)
        if entity:
            return f"{entity.schema_name}.{entity.table}"
        return None

    def get_field_column(self, entity_name: str, field_name: str) -> str | None:
        """
        Obtener nombre de columna física para un campo.

        Args:
            entity_name: Nombre de la entidad
            field_name: Nombre del campo

        Returns:
            Nombre de la columna o None
        """
        entity = self.get_entity(entity_name)
        if entity and field_name in entity.fields:
            return entity.fields[field_name].column
        return None

    def get_relationships_for_entity(self, entity_name: str) -> list[Relationship]:
        """
        Obtener relaciones donde participa una entidad.

        Args:
            entity_name: Nombre de la entidad

        Returns:
            Lista de relaciones
        """
        results = []
        for rel in self._relationships.values():
            if rel.from_entity == entity_name or rel.to_entity == entity_name:
                results.append(rel)
        return results

    def get_analysis_template(self, template_name: str) -> dict | None:
        """
        Obtener una plantilla de análisis.

        Args:
            template_name: Nombre de la plantilla

        Returns:
            Plantilla o None
        """
        return self._analysis_templates.get(template_name)

    def list_entities(self) -> list[str]:
        """Listar nombres de todas las entidades."""
        return list(self._entities.keys())

    def list_analysis_templates(self) -> list[str]:
        """Listar nombres de plantillas de análisis."""
        return list(self._analysis_templates.keys())

    def get_context_for_llm(self, entities: list[str] | None = None) -> str:
        """
        Generar contexto sobre entidades para incluir en prompts de LLM.

        Args:
            entities: Lista de entidades específicas, o None para todas

        Returns:
            Texto con contexto semántico
        """
        if entities is None:
            entities = self.list_entities()

        context_parts = ["## Entidades Disponibles\n"]

        for entity_name in entities:
            entity = self.get_entity(entity_name)
            if not entity:
                continue

            context_parts.append(f"\n### {entity.name}")
            context_parts.append(f"- Descripción: {entity.description}")
            context_parts.append(f"- Tabla: {entity.schema_name}.{entity.table}")
            context_parts.append(f"- Geometría: {entity.geometry_type} ({entity.geometry_column})")
            # F1.3: exponer SRID + unidades para que el LLM responda áreas/
            # distancias con las unidades correctas (no m² cuando es grados).
            from geo_copilot.core.formatters import crs_units_note
            context_parts.append(f"- CRS: {crs_units_note(entity.srid)}")
            context_parts.append(f"- Aliases: {', '.join(entity.aliases)}")

            # Campos principales
            context_parts.append("- Campos:")
            for fname, field in entity.fields.items():
                if not field.sensitive:  # No incluir campos sensibles
                    context_parts.append(f"  - {fname}: {field.description} ({field.type})")

            # Métricas
            if entity.metrics:
                context_parts.append("- Métricas:")
                for metric in entity.metrics:
                    context_parts.append(f"  - {metric.name}: {metric.description}")

        return "\n".join(context_parts)

    def to_dict(self) -> dict[str, Any]:
        """Exportar capa semántica como diccionario."""
        return {
            "entities": {
                name: {
                    "description": e.description,
                    "table": f"{e.schema_name}.{e.table}",
                    "geometry": e.geometry_type,
                    "fields": list(e.fields.keys()),
                    "aliases": e.aliases
                }
                for name, e in self._entities.items()
            },
            "relationships": {
                name: {
                    "from": r.from_entity,
                    "to": r.to_entity,
                    "type": r.type
                }
                for name, r in self._relationships.items()
            },
            "templates": list(self._analysis_templates.keys())
        }
