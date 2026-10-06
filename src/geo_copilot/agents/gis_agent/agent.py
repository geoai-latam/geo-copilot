"""
Agente GIS/SQL - Spatial Analyst

Especialista en traducir intenciones analíticas en consultas PostGIS
y análisis Python seguros.
"""

from typing import Any

from geo_copilot.agents.base import AgentResponse, BaseAgent
from geo_copilot.agents.gis_agent.sql_generator import SQLGenerator

# Sprint F: eliminado `SQLTemplates` — el LLM construye SQL completo via
# `sql_generator.generate()` viendo el schema. Los templates hardcoded
# (proximity/aggregation/coverage/intersection) limitaban el dominio a 4
# operaciones canónicas y exigían helpers _get_*_field que decidían campos
# por keywords.
from geo_copilot.agents.gis_agent.sql_validator import SQLValidator
from geo_copilot.core.config import settings
from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm
from geo_copilot.security.hitl import HITLManager
from geo_copilot.semantic.layer import SemanticLayer

logger = get_logger(__name__)

# F4: la ejecución del SQL y el esquema viven en sus módulos (mixins).
from geo_copilot.agents.gis_agent.aprobado import AprobadoMixin
from geo_copilot.agents.gis_agent.ejecucion_sql import EjecucionSQLMixin
from geo_copilot.agents.gis_agent.esquema import EsquemaMixin

#: Tablas geoespaciales que el router ve por nombre (las demás se cuentan).
_TABLAS_EN_RESUMEN = 200

# SEC-02: cache del chequeo de disponibilidad del rol de mínimos privilegios
# `gis_readonly`. None = sin chequear; True/False = si current_user puede
# `SET ROLE gis_readonly` (el rol existe Y hay membresía). Se resuelve una vez
# para no meter un query extra en cada ejecución de SQL.
_GIS_READONLY_OK: bool | None = None


class GisReadonlyUnavailable(RuntimeError):
    """No se pudo confirmar el rol de mínimos privilegios (R0.6, AUD-04)."""


async def _gis_readonly_available(db_pool) -> bool:
    """¿Puede la conexión bajar al rol de solo-lectura `gis_readonly`? (SEC-02).

    `pg_has_role(current_user, 'gis_readonly', 'MEMBER')` es True solo si el rol
    existe y current_user es miembro (puede `SET ROLE`).

    R0.6 (auditoría 2026-07-26, AUD-04): antes CUALQUIER excepción —incluido un
    fallo transitorio del pool— fijaba ``False`` de forma PERMANENTE en el
    global de módulo, con un simple warning. A partir de ahí se saltaba el
    ``SET LOCAL ROLE`` y **todo el SQL del LLM corría como el usuario de
    login**, que además era superusuario. Verificado contra el contenedor:
    ``rolsuper=t`` y una transacción ``READ ONLY`` NO contiene a un
    superusuario (``pg_read_file('/etc/passwd')`` devuelve el fichero).

    Ahora:
      - Sólo se cachea el resultado si la consulta se pudo responder.
      - Un error transitorio NO se cachea: se propaga y el intento falla, para
        reintentarse limpio en la siguiente llamada.
      - Un ``False`` legítimo (el rol no existe) se cachea, pero el llamador
        debe FALLAR CERRADO en vez de ejecutar con privilegios plenos.
    """
    global _GIS_READONLY_OK
    if _GIS_READONLY_OK is None:
        try:
            async with db_pool.acquire() as conn:
                _GIS_READONLY_OK = bool(await conn.fetchval(
                    "SELECT pg_has_role(current_user, 'gis_readonly', 'MEMBER')"
                ))
        except Exception as exc:
            # NO se cachea: puede ser el pool, la red o un reinicio de la BD.
            # Cachear aquí era lo que degradaba el sistema para siempre.
            logger.error(
                "SEC-02/R0.6: no se pudo confirmar el rol gis_readonly (%s). "
                "Se aborta la ejecución en vez de correr con privilegios plenos.",
                exc,
            )
            raise GisReadonlyUnavailable(
                "No se pudo confirmar el rol de mínimos privilegios para ejecutar SQL."
            ) from exc
    return _GIS_READONLY_OK


class GISAgent(EjecucionSQLMixin, EsquemaMixin, AprobadoMixin, BaseAgent):
    """
    Agente especializado en análisis espacial y generación de SQL.

    Capacidades:
    - Generación de consultas PostGIS desde lenguaje natural
    - Uso de plantillas optimizadas para casos comunes
    - Validación de seguridad y optimización de queries
    - Análisis espaciales: proximidad, agregación, cobertura, hotspots
    """

    def __init__(
        self,
        semantic_layer: SemanticLayer | None = None,
        hitl_manager: HITLManager | None = None,
        llm_client: LLMClient | None = None,
        db_connection: Any = None,
        db_pool: Any = None,
        agent_hub: Any = None,
    ):
        super().__init__(
            name="GISAgent",
            description="Especialista en análisis espacial y consultas PostGIS"
        )

        self.semantic_layer = semantic_layer
        self.hitl_manager = hitl_manager or HITLManager(timeout=settings.hitl_timeout)
        self.llm_client = llm_client or LLMClient.from_settings(settings)
        self.db_connection = db_connection
        self.db_pool = db_pool  # asyncpg pool for direct execution
        # A2A (2026-05-31): hub para invocar capacidades de otros agentes.
        # ``None`` cuando el GIS Agent se usa standalone (tests legacy).
        self.agent_hub = agent_hub

        # Componentes especializados — el SQL generator necesita el LLM.
        # Antes se construía con `SQLGenerator(semantic_layer)` sin pasar
        # llm_client, y caía silenciosamente a `_generate_basic_sql` que
        # devolvía un comentario placeholder "No se pudo generar SQL". El
        # camino LLM nunca se ejecutaba.
        self.sql_generator = SQLGenerator(
            semantic_layer=semantic_layer,
            llm_client=self.llm_client,
            agent_hub=agent_hub,
        )
        # S0.2: el SQL del modelo solo puede leer las tablas que el semantic
        # layer conoce. Se consulta en cada validación (el layer se hidrata
        # después de construir el agente).
        self.sql_validator = SQLValidator(
            allowed_tables=self._tablas_del_semantic_layer, max_limit=_tope_sql(),
        )

        self._register_tools()

    def _tablas_del_semantic_layer(self) -> set[str]:
        # S2.2: + las tablas del workspace de la sesión en curso (las fija el
        # nodo SQL; una sesión nunca ve las de otra).
        from geo_copilot.platform.workspace.context import tablas_de_sesion

        tablas = set(tablas_de_sesion.get())
        layer = self.semantic_layer
        if layer is None:
            return tablas
        for nombre in layer.list_entities():
            entidad = layer.get_entity(nombre)
            if entidad is not None and entidad.table:
                tablas.add(f"{entidad.schema_name or 'public'}.{entidad.table}".lower())
        return tablas

    def _register_tools(self) -> None:
        """Registrar herramientas disponibles para el agente.

        Sprint F: solo dos herramientas — `generate_spatial_query` (LLM
        construye SQL completo) y `execute_query`. Antes había además
        `proximity_analysis`, `territorial_aggregation`, `coverage_analysis`,
        `spatial_intersection` — todas usaban templates SQL hardcoded que
        limitaban a 4 patrones de análisis. El LLM puede construir cualquier
        consulta PostGIS viendo el schema.
        """
        self.register_tool(
            "generate_spatial_query",
            self.generate_spatial_query,
            "Generar consulta SQL espacial desde lenguaje natural via LLM",
        )
        self.register_tool(
            "execute_query",
            self.execute_query,
            "Ejecutar consulta SQL validada",
        )

    def get_capabilities(self) -> dict:
        """Retornar capacidades del agente."""
        return {
            "analysis_types": [
                "proximity",
                "aggregation",
                "coverage",
                "intersection",
                "buffer",
                "hotspot",
                "temporal_change"
            ],
            "spatial_functions": [
                "ST_Buffer",
                "ST_Intersects",
                "ST_Within",
                "ST_Contains",
                "ST_Distance",
                "ST_Area",
                "ST_Centroid",
                "ST_Union"
            ],
            "output_formats": [
                "geojson",
                "table",
                "statistics"
            ]
        }

    # ==========================================================================
    # A2A — capacidades públicas para que otros agentes consulten al GISAgent.
    # ==========================================================================

    # Tope conservador para where_clause — protección contra prompts
    # adversarios que intenten gastar tokens/CPU del parser SQL.
    _MAX_WHERE_LEN: int = 2000

    async def process(self, query: str, context: dict | None = None) -> AgentResponse:
        """Procesar solicitud de análisis espacial generando SQL via LLM.

        El LLM ve la query NL + el schema (semantic layer) + el contexto y
        construye SQL PostGIS completo. Sin templates específicos por
        analysis_type — el LLM expresa proximity/aggregation/coverage/
        intersection/buffer/cualquier patrón directamente en SQL.
        """
        context = context or {}
        logger.info(f"GISAgent processing: {query[:100]}...")

        # Validación liviana: el LLM decide si puede o no en su prompt.
        # `_analyze_query` ahora solo extrae las entidades mencionadas para
        # darle pistas al SQL generator; ya NO clasifica por analysis_type
        # ni decide qué template usar.
        semantic_context = ""
        if self.semantic_layer:
            semantic_context = self.semantic_layer.get_context_for_llm()

        analysis = await self._analyze_query(query, semantic_context)
        if not analysis.get("can_process", False):
            return AgentResponse(
                success=False,
                message=analysis.get("reason", "No se pudo procesar la consulta"),
                data=None,
            )

        sql_result = await self.generate_spatial_query(
            query=query,
            entities=analysis.get("entities", []),
            context=context,
        )

        return AgentResponse(
            success=sql_result.get("success", False),
            message=sql_result.get("message", "Query generated"),
            data=sql_result,
            requires_hitl=sql_result.get("requires_hitl", True),
            hitl_context=sql_result.get("hitl_context"),
        )

    async def _analyze_query(self, query: str, semantic_context: str) -> dict:
        """Triage liviano: ¿el query es procesable y qué entidades menciona?

        Sprint F: simplificado. Antes pedía `analysis_type` + 12 campos
        (target_entity, source_entity, data_entity, admin_entity, etc.) para
        despachar a templates. Ahora el LLM SQL generator ve la query NL
        completa y construye SQL libre — solo necesitamos saber si vale la
        pena procesar (vs. degradar a `direct_response` desde upstream) y
        las entidades para darle pista.
        """
        system_prompt = f"""Eres un analista GIS. Decide si la consulta del usuario
se puede traducir a una query SQL espacial sobre el schema disponible.

{semantic_context}

Responde en JSON estricto:
{{
  "can_process": true|false,
  "reason": "<si false: por qué; si true: cadena vacía>",
  "entities": ["<nombres de entidades del schema mencionadas>"]
}}"""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=query),
        ]

        try:
            response = await self.llm_client.chat(messages)
            parsed = parse_json_from_llm(response.content, default=None)
            if not isinstance(parsed, dict) or "can_process" not in parsed:
                logger.warning(
                    f"[GISAgent] _analyze_query: JSON inválido. Raw: {response.content[:200]}"
                )
                # Si el LLM no devuelve estructura válida, intentamos
                # procesar igual — el sql_generator también es LLM y dará
                # su propio error si no puede.
                return {"can_process": True, "entities": []}
            return parsed
        except Exception as e:
            logger.error(f"Error analyzing query: {e}", exc_info=True)
            return {"can_process": False, "reason": str(e), "entities": []}

    async def generate_spatial_query(
        self,
        query: str,
        entities: list[str],
        context: dict | None = None
    ) -> dict[str, Any]:
        """
        Generar consulta SQL espacial desde lenguaje natural.

        Args:
            query: Descripción del análisis deseado
            entities: Entidades involucradas
            context: Contexto adicional

        Returns:
            Diccionario con SQL generado y metadata. Incluye ``a2a_log``
            con el registro de las llamadas A2A que el SQLGenerator hizo
            al DataAgent (para validar entidades / pedir sugerencias).
        """
        # A2A: el SQL generator consulta al DataAgent antes de generar.
        # Capturamos el log para exponerlo en la respuesta del agente.
        a2a_log: list[dict] = []
        sql = await self.sql_generator.generate(
            query, entities, context, a2a_log=a2a_log,
        )

        # Validar
        validation = self.sql_validator.validate(sql)

        if not validation["is_valid"]:
            return {
                "success": False,
                "message": "SQL validation failed",
                "errors": validation["errors"],
                "sql": sql
            }

        # Optimizar
        optimized_sql = self.sql_validator.optimize(sql)

        return {
            "success": True,
            "message": "SQL generated and validated",
            "sql": optimized_sql,
            "original_sql": sql,
            "validation": validation,
            "a2a_log": a2a_log,
            "requires_hitl": True,
            "hitl_context": {
                "type": "sql_execution",
                "sql": optimized_sql,
                "entities": entities,
            },
        }

    # Sprint F: eliminados `_get_name_field`, `_get_id_field`,
    # `_get_numeric_fields`. Eran heurísticas (keywords `nombre/name/nom_mun`
    # y `type in ["int","integer","float","number"]`) que decidían qué
    # campo usar al construir SQL con templates. Ahora que el LLM construye
    # SQL completo viendo el schema, esa decisión la hace el LLM con todo
    # el contexto (no por primer match de keyword).

    # =========================================================================
    # Métodos para integración directa con el grafo (sin semantic layer)
    # =========================================================================

    # R3.2: eliminado `execute_sql_with_pool` — ejecutaba SQL SIN transacción
    # READ ONLY, SIN statement_timeout, SIN validación y SIN cap de LIMIT (un
    # bypass completo de los guardrails de `_execute_sql`). No tenía callers;
    # toda ejecución de SQL debe pasar por `_execute_sql`.


def _tope_sql() -> int:
    """Filas máximas por consulta SQL (SQL_RESULT_LIMIT). F2: 10.000 por defecto.

    Con el workspace una capa grande ya no viaja entera al navegador (va por
    teselas), así que el tope de 1000 solo truncaba: "trae las construcciones a
    menos de 300 m" (3132) volvía cortada y el agente la sustituía por un COUNT.
    """
    from geo_copilot.core.config import get_settings

    return int(getattr(get_settings(), "sql_result_limit", 1000) or 1000)
