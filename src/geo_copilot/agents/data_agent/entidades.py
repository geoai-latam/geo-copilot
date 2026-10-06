"""Las ENTIDADES de la capa semántica: listarlas y resolver un nombre (A2A, con sugerencias).

Salió de `DataAgent` (F4 del plan de calidad: agent.py tenía 971 líneas), tal cual.
"""

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.semantic.layer import SemanticLayer

logger = get_logger("geo_copilot.agents.data_agent.agent")


# =============================================================================
# A2A (2026-05-31): tipo de retorno del lookup de entidad.
#
# Cuando otro agente (GIS Agent, SymbologyAgent, etc.) necesita validar
# una entidad mid-execution, llama a ``DataAgent.lookup_entity`` vía el
# AgentHub. La respuesta es estructurada — el caller decide si usar el
# nombre canónico, mostrar la sugerencia al usuario, o abortar.
# =============================================================================

@dataclass
class EntityLookupResult:
    """Resultado de ``DataAgent.lookup_entity`` para callers A2A."""
    query: str
    exists: bool
    canonical_name: str | None = None
    table: str | None = None
    geometry_type: str | None = None
    geometry_column: str | None = None
    srid: int | None = None
    fields: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    # Cuando ``exists=False``: lista de nombres reales que más se parecen
    # al query. Vacía si no hay matches razonables.
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "exists": self.exists,
            "canonical_name": self.canonical_name,
            "table": self.table,
            "geometry_type": self.geometry_type,
            "geometry_column": self.geometry_column,
            "srid": self.srid,
            "fields": list(self.fields),
            "aliases": list(self.aliases),
            "suggestions": list(self.suggestions),
        }


class EntidadesMixin:
    """Las ENTIDADES de la capa semántica: listarlas y resolver un nombre (A2A, con sugerencias)."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        semantic_layer: SemanticLayer | None

    async def list_available_entities(
        self,
        *,
        only_with_geometry: bool = False,
        max_entities: int = 50,
    ) -> list[dict[str, Any]]:
        """Devolver la lista estructurada de entidades del semantic layer.

        Capacidad A2A para discovery: Router/Planner/Insights pueden pedir
        "qué hay disponible" sin tener que parsear el ``schema_info`` (que
        es un blob de texto para el LLM, no estructurado).

        Defensa de entrada:
        - ``max_entities`` se coerciona a int (None/no-parseable → 50).
        - ``max_entities <= 0`` retorna lista vacía (antes devolvía 1 por
          bug de off-by-check).
        - Sin semantic_layer → ``[]``.

        Args:
            only_with_geometry: Si True, filtrar entidades sin columna
                de geometría (sirve para el GISAgent que solo procesa
                tablas espaciales).
            max_entities: Tope para no devolver listas enormes. ``<= 0``
                devuelve lista vacía explícitamente.

        Returns:
            Lista de dicts con ``{name, table, geometry_type,
            geometry_column, srid, field_count, aliases, description}``.
        """
        if not self.semantic_layer:
            logger.debug("[A2A] list_available_entities: sin semantic_layer")
            return []

        # Coerción / validación de max_entities.
        try:
            cap = int(max_entities)
        except (TypeError, ValueError):
            logger.debug(
                f"[A2A] list_available_entities: max_entities={max_entities!r} "
                f"no parseable → default 50"
            )
            cap = 50
        if cap <= 0:
            return []

        out: list[dict[str, Any]] = []
        # Usar API pública para no acoplar a ``_entities`` privado.
        for ent_name in self.semantic_layer.list_entities():
            entity = self.semantic_layer.get_entity(ent_name)
            if entity is None:
                continue

            has_geom = bool(entity.geometry_column) and (
                (entity.geometry_type or "").upper() != ""
            )
            if only_with_geometry and not has_geom:
                continue

            out.append({
                "name": ent_name,
                "table": f"{entity.schema_name}.{entity.table}",
                "geometry_type": entity.geometry_type,
                "geometry_column": entity.geometry_column,
                "srid": entity.srid,
                "field_count": len(entity.fields),
                "aliases": list(entity.aliases),
                "description": entity.description,
            })
            if len(out) >= cap:
                break
        return out

    async def lookup_entity(  # noqa: C901, PLR0912
        self,
        name: str,
        *,
        suggestion_cutoff: float = 0.6,
        max_suggestions: int = 3,
    ) -> EntityLookupResult:
        """Resolver una entidad por nombre o alias, con fuzzy fallback.

        Lookup order:

        1. **Match exacto** por nombre canónico O alias case-insensitive
           (``SemanticLayer.get_entity`` ya cubre ambos via ``_alias_map``).
        2. **Match case-insensitive** del nombre canónico como fallback
           directo (cubre casos donde get_entity es estricto).
        3. **Fuzzy match** sobre nombres + aliases vía
           ``difflib.get_close_matches`` con cutoff configurable.

        Defensa de entrada:
        - ``name`` vacío o ``None`` → ``exists=False`` con query="" (sin crash).
        - ``suggestion_cutoff`` clamped a [0.0, 1.0].
        - Sin semantic layer → ``exists=False`` con suggestions=[] (sin crash).

        Args:
            name: Texto del usuario. Puede tener typo, case raro, espacios
                envolventes. ``None``/empty se tolera sin error.
            suggestion_cutoff: Umbral de similitud [0,1] para fuzzy match.
                0.6 captura typos comunes sin sugerir cualquier cosa.
                Clamped — valores fuera de rango se ajustan al límite.
            max_suggestions: Cuántas sugerencias devolver al fallar
                (clamped a [1, 10]).
        """
        # Defensa de entrada: no crash con None/empty.
        safe_name = (name or "").strip() if isinstance(name, str) else ""
        result = EntityLookupResult(query=safe_name, exists=False)

        if not self.semantic_layer:
            logger.debug(f"[A2A] lookup_entity({name!r}): sin semantic_layer")
            return result

        if not safe_name:
            logger.debug("[A2A] lookup_entity: empty/None name")
            return result

        # Clamp params a rangos sanos.
        cutoff = max(0.0, min(1.0, float(suggestion_cutoff)))
        max_sugs = max(1, min(10, int(max_suggestions)))

        # Paso 1: match exacto (canonical o alias, ambos via get_entity).
        entity = self.semantic_layer.get_entity(safe_name)
        # Paso 2: case-insensitive fallback (get_entity es case-sensitive
        # en canonical; ``_alias_map`` ya guarda lower-case aliases).
        if entity is None:
            entity = self.semantic_layer.get_entity(safe_name.lower())

        # Paso 2.5: match por NOMBRE DE TABLA. Maneja el caso en que el router
        # LLM emite el nombre de tabla o schema.tabla ('lotes', 'catastro.lotes',
        # 'catastro_lotes') mientras el canónico es otro — la causa raíz del
        # falso-negativo A2A intermitente (E0). Determinista, sin round-trip a BD.
        if entity is None:
            cand = safe_name.lower()
            tail = cand.split(".")[-1]
            for ent_name in self.semantic_layer.list_entities():
                e = self.semantic_layer.get_entity(ent_name)
                if e is None:
                    continue
                tbl = (e.table or "").lower()
                if not tbl:
                    continue
                schema = (e.schema_name or "").lower()
                if cand in (tbl, f"{schema}.{tbl}", f"{schema}_{tbl}") or tail == tbl:
                    entity = e
                    break

        if entity is not None:
            return EntityLookupResult(
                query=safe_name,
                exists=True,
                canonical_name=entity.name,
                table=f"{entity.schema_name}.{entity.table}",
                geometry_type=entity.geometry_type,
                geometry_column=entity.geometry_column,
                srid=entity.srid,
                fields=list(entity.fields.keys()),
                aliases=list(entity.aliases),
            )

        # Paso 3: fuzzy match sobre nombres + aliases. Iteramos via la API
        # pública ``list_entities() + get_entity()`` para no acoplarnos al
        # atributo privado ``_entities`` del SemanticLayer.
        pool: list[str] = []
        name_to_canonical: dict[str, str] = {}
        for ent_name in self.semantic_layer.list_entities():
            ent = self.semantic_layer.get_entity(ent_name)
            if ent is None:
                continue
            pool.append(ent_name)
            name_to_canonical[ent_name.lower()] = ent_name
            for alias in ent.aliases:
                pool.append(alias)
                name_to_canonical[alias.lower()] = ent_name

        matches = difflib.get_close_matches(
            safe_name, pool, n=max_sugs * 2, cutoff=cutoff,
        )
        # Mapear matches → nombre canónico, dedupe preservando orden.
        seen: set[str] = set()
        suggestions: list[str] = []
        for m in matches:
            canonical = name_to_canonical.get(m.lower(), m)
            if canonical not in seen:
                seen.add(canonical)
                suggestions.append(canonical)
            if len(suggestions) >= max_sugs:
                break
        result.suggestions = suggestions
        logger.debug(
            f"[A2A] lookup_entity({safe_name!r}) MISS — suggestions={suggestions}"
        )
        return result

    async def list_entities(
        self,
        search_term: str | None = None
    ) -> dict[str, Any]:
        """
        Listar todas las entidades disponibles en el catálogo.

        Args:
            search_term: Filtrar por término de búsqueda

        Returns:
            Lista de entidades disponibles
        """
        if not self.semantic_layer:
            return {
                "success": False,
                "message": "Semantic layer not configured",
                "entities": []
            }

        entities = []
        for entity_name in self.semantic_layer.list_entities():
            entity = self.semantic_layer.get_entity(entity_name)
            if not entity:
                continue

            if search_term:
                search_lower = search_term.lower()
                name_match = search_lower in entity.name.lower()
                desc_match = entity.description and search_lower in entity.description.lower()
                if not name_match and not desc_match:
                    continue

            entities.append({
                "name": entity.name,
                "description": entity.description or "",
                "table": f"{entity.schema_name}.{entity.table}",
                "geometry_type": entity.geometry_type or "N/A",
                "fields_count": len(entity.fields)
            })

        return {
            "success": True,
            "message": f"Found {len(entities)} entities",
            "entities": entities,
            "total": len(entities)
        }
