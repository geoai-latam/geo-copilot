"""
Generador de SQL PostGIS desde lenguaje natural.

Utiliza el LLM y la capa semántica para traducir consultas
en lenguaje natural a SQL espacial.
"""


from typing import Any

from geo_copilot.agents.gis_agent.sql_validator import quote_ident, quote_qualified
from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.prompts import cargar_prompt
from geo_copilot.semantic.layer import SemanticLayer

logger = get_logger(__name__)


def escape_sql_comment(text: str, max_length: int = 200) -> str:
    """
    Escape text for safe inclusion in SQL comments.

    Prevents SQL injection via comment manipulation by removing
    characters that could close comments or start new SQL statements.

    Args:
        text: The text to escape
        max_length: Maximum length of the resulting string

    Returns:
        Sanitized text safe for SQL comments
    """
    if not text:
        return ""

    sanitized = text
    # Strip control characters first.
    for ch in ("\n", "\r"):
        sanitized = sanitized.replace(ch, " ")
    for ch in ("\x00", "\x07", "\x08"):
        sanitized = sanitized.replace(ch, "")
    sanitized = sanitized.replace(";", ",")

    # Neutralize comment-starting sequences. A single pass of str.replace can
    # leave residual `--`/`*/` at the seam between consecutive replacements
    # (e.g. `----` → `- -- -`), so we iterate until the result is stable.
    # The loop is bounded: each iteration strictly reduces the number of
    # occurrences in any contiguous run.
    for token, replacement in (("*/", "* /"), ("--", "- -")):
        while token in sanitized:
            sanitized = sanitized.replace(token, replacement)

    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "..."

    return sanitized


class SQLGenerator:
    """
    Genera consultas SQL PostGIS desde lenguaje natural.

    Utiliza:
    - Capa semántica para mapear entidades
    - LLM para interpretar la intención
    - Plantillas para casos comunes
    """

    def __init__(
        self,
        semantic_layer: SemanticLayer | None = None,
        llm_client: LLMClient | None = None,
        agent_hub: Any = None,
    ):
        self.semantic_layer = semantic_layer
        self.llm_client = llm_client
        # A2A (2026-05-31): hub para invocar capacidades de otros agentes
        # — en particular ``DataAgent.lookup_entity`` antes de generar SQL.
        self.agent_hub = agent_hub

    async def generate(
        self,
        query: str,
        entities: list[str] | None = None,
        context: dict | None = None,
        *,
        a2a_log: list | None = None,
    ) -> str:
        """
        Generar SQL desde una descripción en lenguaje natural.

        A2A (2026-05-31): si hay ``agent_hub``, pregunta al ``data_agent``
        si cada entidad existe ANTES de pasar al LLM. Si alguna no existe
        pero tiene sugerencias (fuzzy match), las inyectamos como hint al
        prompt. El LLM, así, no aluciona nombres de tablas inexistentes.

        Args:
            query: Descripción del análisis deseado
            entities: Entidades específicas a usar
            context: Contexto adicional (filtros, límites, etc.)
            a2a_log: Lista opcional donde appendear registros de cada A2A
                call (para que el caller los exponga via state.a2a_log).

        Returns:
            SQL generado
        """
        context = context or {}

        # Pre-flight A2A: validar entidades contra el DataAgent.
        entity_hints = await self._resolve_entities_via_a2a(entities, a2a_log)

        # Construir contexto de entidades
        entity_context = self._build_entity_context(entities)

        # Construir prompt del sistema
        system_prompt = self._build_system_prompt(
            entity_context, context, entity_hints=entity_hints,
        )

        # R2.2 (agentic, no fallbacks): sin LLM no se genera SQL — antes se caía
        # en silencio a un SELECT template que ignoraba la intención del usuario
        # (y elegía "los primeros 5 campos" por heurística). Fallo honesto.
        if not self.llm_client:
            raise ValueError(
                "No hay cliente LLM configurado: el SQL lo genera el LLM, "
                "no un template."
            )

        # Generar con LLM
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=query)
        ]

        # R2.2: un fallo del LLM se propaga (el caller tiene su ruta de error
        # honesta y el sql_corrector cubre los errores de EJECUCIÓN). No se
        # sustituye el juicio del LLM por un template.
        response = await self.llm_client.chat(messages, temperature=0.1)
        return self._extract_sql(response.content)

    async def _resolve_entities_via_a2a(
        self,
        entities: list[str] | None,
        a2a_log: list | None,
    ) -> dict[str, dict]:
        """A2A pre-flight: validar cada entidad contra el ``DataAgent``.

        Returns:
            Dict ``{user_entity_name: lookup_result_dict}`` con la
            respuesta de ``DataAgent.lookup_entity`` por cada entidad.
            Vacío si no hay hub o no hay entidades para validar.

        El caller usa este dict para construir hints al LLM (entidades
        confirmadas + sugerencias para typos).
        """
        if not self.agent_hub or not entities:
            return {}

        hints: dict[str, dict] = {}
        for ent in entities:
            if not ent:
                continue
            success, payload = await self.agent_hub.call(
                caller="sql_generator",
                target="data_agent",
                method="lookup_entity",
                name=ent,
            )
            if not success:
                logger.debug(f"[A2A] sql_generator skip {ent!r}: {payload}")
                continue
            # ``payload`` es un EntityLookupResult; lo serializamos.
            payload_dict = (
                payload.to_dict() if hasattr(payload, "to_dict") else dict(payload)
            )
            hints[ent] = payload_dict
            if a2a_log is not None:
                a2a_log.append({
                    "from": "sql_generator",
                    "to": "data_agent",
                    "method": "lookup_entity",
                    "query": ent,
                    "exists": payload_dict.get("exists"),
                    "canonical_name": payload_dict.get("canonical_name"),
                    "suggestions": payload_dict.get("suggestions") or [],
                })

            if payload_dict.get("exists"):
                logger.info(
                    f"[A2A] sql_generator → data_agent: '{ent}' "
                    f"confirmed as {payload_dict.get('canonical_name')!r}"
                )
            else:
                sugs = payload_dict.get("suggestions") or []
                if sugs:
                    logger.info(
                        f"[A2A] sql_generator → data_agent: '{ent}' NOT FOUND. "
                        f"DataAgent suggests {sugs}"
                    )
                else:
                    logger.warning(
                        f"[A2A] sql_generator → data_agent: '{ent}' NOT FOUND. "
                        f"No fuzzy suggestions."
                    )
        return hints

    def _render_entity_hints(self, entity_hints: dict[str, dict] | None) -> str:
        """Render el bloque de hints A2A para el prompt del SQL generator."""
        if not entity_hints:
            return ""
        lines = ["\n\n## VALIDACIÓN A2A DE ENTIDADES (consulta al DataAgent)"]
        any_correction = False
        for user_name, info in entity_hints.items():
            if info.get("exists"):
                canonical = info.get("canonical_name") or user_name
                table = info.get("table") or "?"
                if canonical.lower() != user_name.lower():
                    lines.append(
                        f"- '{user_name}' → entidad confirmada '{canonical}' (tabla {table})"
                    )
                else:
                    lines.append(f"- '{user_name}' → confirmada (tabla {table})")
            else:
                suggestions = info.get("suggestions") or []
                if suggestions:
                    any_correction = True
                    sug_str = ", ".join(f"'{s}'" for s in suggestions)
                    lines.append(
                        f"- '{user_name}' NO EXISTE. El DataAgent sugiere "
                        f"posibles correcciones: {sug_str}. "
                        f"USA UNA DE ESAS — NO inventes tablas."
                    )
                else:
                    lines.append(
                        f"- '{user_name}': el catálogo A2A no la confirmó y no "
                        f"hay sugerencias, PERO puede estar desincronizado o "
                        f"llamarse distinto en el SCHEMA. Revisa el schema y "
                        f"genera SQL contra la tabla más plausible. Solo si "
                        f"NINGUNA tabla del schema corresponde, dilo "
                        f"honestamente — NO fabriques un conteo 0 ni datos."
                    )
        if any_correction:
            lines.append(
                "\n⚠️ REGLA: si una entidad mencionada por el usuario tiene un "
                "typo y el DataAgent sugiere correcciones, usa la sugerencia "
                "MÁS PRÓXIMA al texto original. Esto evita SQL contra tablas "
                "inexistentes."
            )
        return "\n".join(lines)

    def _build_entity_context(self, entities: list[str] | None) -> str:
        """Construir contexto de entidades para el prompt."""
        if not self.semantic_layer:
            return ""

        if entities:
            # Usar entidades específicas
            entity_list = entities
        else:
            # Usar todas las entidades
            entity_list = self.semantic_layer.list_entities()

        context_parts = []

        for entity_name in entity_list:
            entity = self.semantic_layer.get_entity(entity_name)
            if not entity:
                continue

            fields_info = []
            for name, field in entity.fields.items():
                if not field.sensitive:
                    fields_info.append(f"  - {field.column} ({field.type}): {field.description}")

            context_parts.append(f"""
### {entity.name}
- Tabla: {entity.schema_name}.{entity.table}
- Geometría: {entity.geometry_column} ({entity.geometry_type})
- SRID: {entity.srid}
- Campos:
{chr(10).join(fields_info)}
""")

        return "\n".join(context_parts)

    def _build_system_prompt(
        self,
        entity_context: str,
        context: dict,
        *,
        entity_hints: dict[str, dict] | None = None,
    ) -> str:
        """Construir el prompt del sistema para generación de SQL.

        A2A: si recibimos ``entity_hints`` (resultado de
        ``DataAgent.lookup_entity`` para cada entidad mencionada en la
        query), inyectamos un bloque de hints al prompt. Esto evita que
        el LLM genere SQL contra tablas que NO existen — si el usuario
        escribió "constsrucciones" (typo), el hint le dice "el DataAgent
        sugiere 'construcciones' (tabla catastro.construcciones)" y el
        LLM genera SQL correcto.
        """
        filters_info = ""
        if context.get("filters"):
            # Sanitize filters before including in prompt (defense in depth)
            safe_filters = escape_sql_comment(str(context['filters']), max_length=500)
            filters_info = f"\nFiltros a aplicar: {safe_filters}"

        limit = context.get("limit", 1000)

        # A2A hints: resumen de qué entidades existen / cuáles tienen typo.
        a2a_block = self._render_entity_hints(entity_hints)

        return cargar_prompt("sql_generador_postgis").format(
            a2a_block=a2a_block, limit=limit, entity_context=entity_context, filters_info=filters_info,
        )

    def _extract_sql(self, content: str) -> str:
        """Extraer SQL de la respuesta del LLM."""
        # Buscar bloques de código SQL
        if "```sql" in content:
            start = content.find("```sql") + 6
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        if "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        # Si no hay bloques de código, retornar todo el contenido
        # limpiando líneas que no parecen SQL
        lines = []
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("//"):
                lines.append(line)

        return "\n".join(lines)

    # R2.2: eliminado `_generate_basic_sql` — era el fallback template que se
    # ejecutaba cuando el LLM fallaba, ignorando la intención del usuario y
    # eligiendo campos por heurística ("primeros 5 no sensibles"). La política
    # es fallo honesto; ver generate().

    def generate_count_query(self, entity_name: str) -> str:
        """Generar query de conteo para una entidad."""
        if not self.semantic_layer:
            return f"SELECT COUNT(*) FROM {entity_name};"

        entity = self.semantic_layer.get_entity(entity_name)
        if not entity:
            return f"-- Entidad no encontrada: {entity_name}"

        return f"""
-- Conteo de registros
SELECT COUNT(*) as total
FROM {quote_qualified(entity.schema_name, entity.table)};
"""

    def generate_extent_query(self, entity_name: str) -> str:
        """Generar query para obtener extent de una entidad."""
        if not self.semantic_layer:
            return f"SELECT ST_Extent(geom) FROM {entity_name};"

        entity = self.semantic_layer.get_entity(entity_name)
        if not entity:
            return f"-- Entidad no encontrada: {entity_name}"

        return f"""
-- Extent (bounding box) de la capa
SELECT
    ST_XMin(extent) as xmin,
    ST_YMin(extent) as ymin,
    ST_XMax(extent) as xmax,
    ST_YMax(extent) as ymax
FROM (
    SELECT ST_Extent({quote_ident(entity.geometry_column)}) as extent
    FROM {quote_qualified(entity.schema_name, entity.table)}
) bounds;
"""

    # R2.2: eliminado `generate_sample_query` — template con la misma heurística
    # de campos ("primeros 5"); sin callers en src. Las muestras las pide el LLM
    # con SQL propio.
