"""Los PORTALES abiertos: buscar en ellos, presentar los resultados y extraer una URL externa.

Salió de `DataAgent` (F4 del plan de calidad: agent.py tenía 971 líneas), tal cual.
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger
from geo_copilot.security.hitl import HITLActionType, HITLStatus

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient
    from geo_copilot.security.hitl import HITLManager

logger = get_logger("geo_copilot.agents.data_agent.agent")


def _agente():
    """`agent` importa este módulo; lo que las pruebas sustituyen ahí (`search_open_data_portals`,
    `settings`) se resuelve al usarlo."""
    from geo_copilot.agents.data_agent import agent

    return agent

# AGT-3 — task-local session id. Avoids the race condition that came from
# storing it on the shared agent instance (``self._current_session_id``);
# ``ContextVar`` is correctly scoped to the current asyncio task, so
# concurrent ``process()`` calls cannot stomp on each other's value.
_session_id_ctx: ContextVar[str | None] = ContextVar("data_agent_session_id", default=None)


class BusquedaAbiertaMixin:
    """Los PORTALES abiertos: buscar en ellos, presentar los resultados y extraer una URL externa."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient
        hitl_manager: HITLManager

    async def search_open_data(  # noqa: C901
        self, search_query: str, region: str | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Buscar servicios ArcGIS y presentar opciones al usuario.

        Args:
            search_query: Query de búsqueda del usuario en lenguaje natural.
            region: clave de región de sesión (F2.2). None = región configurada
                del producto; una key sin catálogo → búsqueda global/neutral.

        Returns:
            Diccionario con servicios encontrados o datos si el usuario seleccionó.

        Raises:
            ValueError: Si `search_query` está vacía. Una búsqueda sin query
                devolvería resultados arbitrarios.
        """
        if not search_query or not search_query.strip():
            raise ValueError(
                "search_open_data requiere una `search_query` no vacía."
            )

        # Búsqueda en ArcGIS Hub Open Data global (vía DiscoveryAgent).
        search_results = await _agente().search_open_data_portals(
            search_query=search_query,
            limit=10,
            llm_client=self.llm_client,
            region=region,
            conversation_history=conversation_history,
        )

        # Si el catálogo está vacío, informar
        if search_results.get("catalog_empty"):
            return {
                "status": "catalog_empty",
                "message": "El catálogo ArcGIS está vacío. Se debe ejecutar la indexación primero.",
                "action_required": "refresh_catalog"
            }

        # Si no hay resultados
        if not search_results.get("services"):
            return {
                "status": "no_results",
                "message": f"No se encontraron servicios para: '{search_query}'",
                "search_query": search_query
            }

        # Presentar opciones al usuario vía HITL
        services = search_results["services"]

        # Formatear opciones para mostrar al usuario
        options_text = "\n".join([
            f"  {svc['id']}. {svc['name']} ({svc['type']}) - {svc['layer_count']} capas"
            for svc in services
        ])

        if _agente().settings.hitl_enabled and self.hitl_manager:
            # Transparentar QUE se buscó: la query efectiva (limpia) y los
            # filtros que el agente derivó. Antes el HITL solo mostraba el
            # texto crudo del usuario y no se entendía por qué los resultados
            # eran los que eran.
            hub_query_info = search_results.get("query", {}) or {}
            effective_q = hub_query_info.get("text_query") or search_query
            hub_params = hub_query_info.get("hub_params") or {}
            param_summary_parts = [f"q='{effective_q}'"]
            if hub_params.get("tags_any"):
                param_summary_parts.append(f"tags={hub_params['tags_any']}")
            if hub_params.get("service_types"):
                param_summary_parts.append(f"tipo={hub_params['service_types']}")
            if hub_params.get("owner_any"):
                param_summary_parts.append(f"owner={len(hub_params['owner_any'])} cuentas")
            if hub_params.get("bbox"):
                param_summary_parts.append("bbox=sí")
            param_summary = " | ".join(param_summary_parts)

            description = (
                f"Búsqueda en ArcGIS (Hub y ArcGIS Online): {param_summary}. "
                f"Encontré {len(services)} servicio(s)."
            )

            # Rama arcgis-busqueda (V5): el panel salía vacío («0 caracteres») y se aprobaba sin ver qué
            # se había encontrado. Lo que se muestra es la lista con lo que permite juzgarla.
            def _linea(svc: dict) -> str:
                quien = svc.get("credits") or svc.get("org") or svc.get("owner") or "organización desconocida"
                extra = [str(svc.get("type") or "")]
                if isinstance(svc.get("views"), int):
                    extra.append(f"{svc['views']:,} vistas".replace(",", "."))
                if svc.get("modified"):
                    extra.append(str(svc["modified"])[:10])
                return f"{svc['id']}. {svc['name']}\n   {quien} · " + " · ".join(e for e in extra if e)

            vista = "\n".join(_linea(svc) for svc in services)

            hitl_response = await self.hitl_manager.request_approval(
                action_type=HITLActionType.EXTERNAL_API,
                title=f"ArcGIS · {len(services)} resultados",
                description=description,
                preview=vista,
                details={
                    "options": options_text,
                    "services": services,
                    "search_query_original": search_query,
                    "search_query_effective": effective_q,
                    "search_params": hub_params,
                    "instruction": "Ingrese el número del servicio a cargar (ej: 1) o 'cancelar'",
                },
                risks=[
                    "Se realizará una consulta al servicio seleccionado",
                ],
                session_id=_session_id_ctx.get()
            )

            if hitl_response.status not in (HITLStatus.APPROVED, HITLStatus.MODIFIED):
                caducada = hitl_response.status == HITLStatus.EXPIRED
                return {
                    "status": "cancelled",
                    "caducada": caducada,
                    "message": ("La aprobación de la búsqueda caducó sin respuesta" if caducada
                                else "Búsqueda cancelada por el usuario"),
                    "services_found": services
                }

            # Obtener selección del usuario desde el feedback
            selection = hitl_response.feedback
            if selection and selection.isdigit():
                selected_idx = int(selection) - 1
                if 0 <= selected_idx < len(services):
                    selected_service = services[selected_idx]

                    # Cargar el servicio seleccionado
                    logger.info(f"Usuario seleccionó: {selected_service['name']}")

                    return {
                        "status": "service_selected",
                        "selected_service": selected_service,
                        "message": f"Servicio seleccionado: {selected_service['name']}",
                        "next_action": "fetch_data",
                        "url": selected_service["url"]
                    }

        # Sin HITL, retornar lista de servicios
        logger.info(f"Search completed for query: '{search_query}', found {len(services)} services")
        return {
            "status": "services_found",
            "services": services,
            "total_found": len(services),
            "search_query": search_query,
            # Propagar la señal de honestidad del DiscoveryAgent: si el
            # usuario pidió un lugar específico (ej. "zipaquira") y los
            # resultados son de otro lado, el chat lo advierte en vez de
            # presentarlos como si fueran del lugar pedido.
            "place_mismatch": search_results.get("place_mismatch", False),
            "place_queried": search_results.get("place_queried"),
        }

    def format_search_results(self, search_query: str, results: list[dict]) -> str:
        """
        Formatear resultados de búsqueda externa para el usuario.

        Args:
            search_query: Query de búsqueda
            results: Lista de servicios encontrados

        Returns:
            Texto formateado para mostrar al usuario
        """
        if not results:
            return f"No se encontraron datasets para: '{search_query}'"

        lines = [f"**Encontré {len(results)} datasets relacionados con '{search_query}':**\n"]

        for i, r in enumerate(results, 1):
            name = r.get("name", "Sin nombre")
            desc = r.get("description", "")[:100]
            url = r.get("url", "")
            has_geo = "🌍" if r.get("has_geospatial") else ""

            lines.append(f"{i}. **{name}** {has_geo}")
            if desc:
                lines.append(f"   {desc}...")
            if url:
                lines.append(f"   📎 `{url}`")
            lines.append("")

        lines.append("\n💡 **Para cargar un servicio**, escribe el número: 'carga el 1' o simplemente '3'")

        return "\n".join(lines)

    def extract_external_url(self, query: str) -> str | None:
        """
        Extraer URL externa de la consulta si existe.

        Args:
            query: Consulta del usuario

        Returns:
            URL encontrada o None
        """
        import re

        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        matches: list[str] = re.findall(url_pattern, query)

        for url in matches:
            url_lower = url.lower()
            if any(keyword in url_lower for keyword in [
                'arcgis.com', 'featureserver', 'mapserver',
                '.geojson', '.json', '.shp', '.zip', '.gpkg', '.kml',
                'datos.gov.co', 'socrata'
            ]):
                return url

        return None
