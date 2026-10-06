"""
Agente de Datos - Data Hunter

Especialista en descubrimiento, validación e integración de datos
geoespaciales de múltiples fuentes (internas y externas).
"""


from geo_copilot.agents.base import AgentResponse, BaseAgent
from geo_copilot.core.config import settings
from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm
from geo_copilot.security.hitl import HITLManager
from geo_copilot.semantic.layer import SemanticLayer

logger = get_logger(__name__)

# F4: el catálogo, las entidades y los portales abiertos viven en sus módulos (mixins).
from geo_copilot.agents.data_agent.busqueda_abierta import (
    BusquedaAbiertaMixin,
    _session_id_ctx,
)
from geo_copilot.agents.data_agent.catalogo import CatalogoMixin
from geo_copilot.agents.data_agent.entidades import (  # noqa: F401
    EntidadesMixin,
    EntityLookupResult,
)

# T5.2: el núcleo ya no tiene conectores externos. El discovery va por DiscoveryAgent sobre el
# servidor MCP de ArcGIS; cargar un servicio, por el nodo data_agent (mismo servidor). Lo usa (y lo
# sustituyen las pruebas) `busqueda_abierta`, que lo busca aquí.
from geo_copilot.agents.data_agent.tools.external_apis import search_open_data_portals  # noqa: F401


class DataAgent(CatalogoMixin, EntidadesMixin, BusquedaAbiertaMixin, BaseAgent):
    """
    Agente especializado en descubrimiento y preparación de datos.

    Capacidades:
    - Búsqueda en catálogo interno (semantic layer)
    - Búsqueda en ArcGIS Hub (vía el servidor MCP de ArcGIS)
    - Validación de calidad de datos
    - Perfilado de datasets
    """

    def __init__(
        self,
        semantic_layer: SemanticLayer | None = None,
        hitl_manager: HITLManager | None = None,
        llm_client: LLMClient | None = None
    ):
        super().__init__(
            name="DataAgent",
            description="Especialista en descubrimiento y validación de datos geoespaciales"
        )

        self.semantic_layer = semantic_layer
        self.hitl_manager = hitl_manager or HITLManager(timeout=settings.hitl_timeout)
        self.llm_client = llm_client or LLMClient.from_settings(settings)

        # Registrar herramientas
        self._register_tools()

    def _register_tools(self) -> None:
        """Registrar herramientas disponibles para el agente."""
        # Herramientas internas
        self.register_tool(
            "search_internal_catalog",
            self.search_internal_catalog,
            "Buscar datasets en el catálogo interno de datos"
        )
        self.register_tool(
            "profile_dataset",
            self.profile_dataset,
            "Analizar y perfilar un dataset para entender su estructura y calidad"
        )
        self.register_tool(
            "validate_data_quality",
            self.validate_data_quality,
            "Validar la calidad de un dataset"
        )
        self.register_tool(
            "suggest_joins",
            self.suggest_joins,
            "Sugerir posibles uniones entre datasets"
        )

        # Herramientas externas
        self.register_tool(
            "search_open_data",
            self.search_open_data,
            "Buscar en portales de datos abiertos (datos.gov.co, IGAC, etc.)"
        )

    def get_capabilities(self) -> dict:
        """Retornar capacidades del agente."""
        return {
            "internal": [
                "search_catalog",
                "profile_datasets",
                "suggest_joins",
                "validate_quality"
            ],
            "external": {
                "arcgis_hub": ["búsqueda en ArcGIS Hub (servidor MCP de ArcGIS)"],
            },
            "validation": [
                "quality_checks",
                "schema_validation",
                "human_review"
            ],
        }

    async def process(
        self,
        query: str,
        context: dict | None = None,
        session_id: str | None = None
    ) -> AgentResponse:
        """
        Procesar una solicitud de búsqueda/descubrimiento de datos.

        Args:
            query: Consulta en lenguaje natural
            context: Contexto adicional
            session_id: ID de sesión para notificaciones HITL

        Returns:
            AgentResponse con los datos encontrados o acciones necesarias
        """
        # AGT-3 + R4.8: el session_id vive en un ContextVar (no en estado de
        # instancia — el agente es compartido) y se RESETEA al salir; sin el
        # reset, el valor se filtraba a trabajo posterior del mismo task.
        token = _session_id_ctx.set(session_id)
        try:
            return await self._process_impl(query, context)
        finally:
            _session_id_ctx.reset(token)

    async def _process_impl(
        self,
        query: str,
        context: dict | None = None,
    ) -> AgentResponse:
        """Cuerpo real de process() (ver wrapper para el manejo del ContextVar)."""
        context = context or {}
        logger.info(f"DataAgent processing: {query[:100]}...")

        # Verificar si hay una acción específica en el contexto (del router)
        if context.get("action") == "search_external":
            # Búsqueda externa directa (viene del router)
            # IMPORTANTE: Usar el query original del usuario, NO keywords generados por LLM
            search_query = context.get("search_query") or query

            logger.info(f"DataAgent: Direct external search with query: '{search_query}'")

            search_results = await self.search_open_data(
                search_query=search_query,
                region=context.get("session_region"),  # F2.2: override por sesión
                conversation_history=context.get("conversation_history"),
            )

            return AgentResponse(
                success=True,
                message=f"External search completed for: '{search_query}'",
                data={
                    "search_results": search_results,
                    "search_query": search_query
                }
            )

        # Obtener contexto semántico si está disponible
        semantic_context = ""
        if self.semantic_layer:
            semantic_context = self.semantic_layer.get_context_for_llm()

        # Analizar la intención del usuario con el LLM
        system_prompt = f"""Eres un agente especializado en descubrimiento de datos geoespaciales.
Tu tarea es analizar la solicitud del usuario y determinar:
1. Qué tipo de datos necesita
2. Dónde buscarlos (catálogo interno o catálogos abiertos externos)
3. Qué filtros aplicar

{semantic_context}

Responde en formato JSON con:
{{
    "intent": "search_internal|search_external|profile|validate",
    "data_type": "tipo de dato buscado",
    "search_query": "query de búsqueda libre — texto natural que se usará como `q=` en APIs externas y como búsqueda fuzzy en el catálogo interno. Mantén el lugar/tema del usuario; el agente downstream decide cómo combinarlo.",
    "suggested_entities": ["entidades del catálogo si aplica"],
    "external_url": "URL si el usuario proporcionó una",
    "filters": {{
        "where": "cláusula WHERE si aplica",
        "bbox": [minx, miny, maxx, maxy] // si aplica,
        "limit": numero // si aplica
    }},
    "reasoning": "explicación breve"
}}"""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=query)
        ]

        try:
            response = await self.llm_client.chat(messages)
            # Si el LLM no devuelve JSON parseable, NO asumimos
            # intent="search_internal" silenciosamente — eso ejecutaba una
            # búsqueda interna que el usuario nunca pidió. Mejor fallar.
            analysis = parse_json_from_llm(response.content, None)
            if not isinstance(analysis, dict) or "intent" not in analysis:
                logger.error(
                    f"[DataAgent] LLM no devolvió JSON válido. "
                    f"Raw response: {response.content[:300]}"
                )
                return AgentResponse(
                    success=False,
                    message="No pude interpretar la respuesta del LLM. Reformula tu consulta.",
                    data={"error": "invalid_llm_response", "raw": response.content[:200]},
                )

            # Ejecutar según la intención (sin default — el LLM ya devolvió uno)
            intent = analysis["intent"]
            filters = analysis.get("filters", {})

            if intent == "search_internal":
                # R4.1: la firma acepta keywords (lista), no search_query — el
                # kwarg inexistente producía TypeError en runtime cada vez que
                # el LLM elegía search_internal (tragado por el except amplio).
                _sq = analysis.get("search_query", "") or ""
                results = await self.search_internal_catalog(
                    keywords=_sq.split() if _sq else None,
                    entities=analysis.get("suggested_entities", [])
                )
            elif intent == "search_external":
                results = await self.search_open_data(
                    search_query=analysis.get("search_query", ""),
                    region=context.get("session_region"),  # F2.2: override por sesión
                    conversation_history=context.get("conversation_history"),
                )
            elif intent == "profile":
                results = {"message": "Profile functionality - specify dataset to profile"}
            elif intent == "validate":
                results = {"message": "Validation functionality - specify dataset to validate"}
            else:
                results = {"message": f"Unknown intent: {intent}"}

            return AgentResponse(
                success=True,
                message=f"Data search completed for: {analysis.get('data_type', 'unknown')}",
                data={
                    "analysis": analysis,
                    "results": results
                }
            )

        except Exception as e:
            logger.error(f"Error processing data request: {e}", exc_info=True)
            return AgentResponse(
                success=False,
                message=f"Error processing request: {str(e)}",
                data=None
            )

    # =========================================================================
    # HERRAMIENTAS INTERNAS
    # =========================================================================


    # ==========================================================================
    # A2A — capacidades públicas para que otros agentes consulten al DataAgent.
    #
    # Estas son las superficies que el ``SQLGenerator``, el ``SymbologyAgent``,
    # etc. invocan via ``hub.call(target="data_agent", method="lookup_entity")``.
    # NO usan el LLM — son lookups sobre el semantic layer hidratado, así que
    # son rápidos (<1ms típico) y determinísticos.
    # ==========================================================================


    # =========================================================================
    # HERRAMIENTAS EXTERNAS
    # =========================================================================
