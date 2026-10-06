"""Búsqueda en catálogos abiertos para el DataAgent.

T5.2: los conectores externos (ArcGIS, archivos, Socrata) ya no viven en el núcleo. La búsqueda
la hace `DiscoveryAgent` (el LLM arma el plan) sobre el servidor MCP de ArcGIS.
"""

from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

_LOTE_PARA_ORDENAR = 50


async def search_open_data_portals(
    search_query: str,
    limit: int = 10,
    llm_client: Any | None = None,
    region: str | None = None,
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    """Buscar servicios en ArcGIS Hub Open Data (global, vía DiscoveryAgent).

    Args:
        search_query: Query de búsqueda libre en lenguaje natural.
        limit: máximo de resultados.
        llm_client: cliente LLM (requerido; el DiscoveryAgent lo necesita).
        region: clave de región de sesión (override). None = región configurada;
            una key sin catálogo → búsqueda global/neutral (sin anclar a otro país).

    Returns:
        ``{query: {search_query, text_query, hub_params}, services: [...],
           total_found, catalog_empty}`` donde ``query.text_query`` es lo
        que efectivamente se mandó a la API Hub.
    """
    from geo_copilot.agents.data_agent.discovery import (
        DiscoveryAgent,
        DiscoveryHints,
    )

    agent = DiscoveryAgent(llm_client=llm_client)
    # F2.2: `region` viene de session_region (override por sesión). None →
    # región configurada del producto; key sin catálogo → global/neutral.
    response = await agent.discover(
        search_query,
        # Se ordena sobre un lote amplio (como el panel) y se muestran los `limit` primeros:
        # pedir solo `limit` al Hub dejaba fuera lo pertinente que el Hub listaba más abajo
        # (V5 T5.2: «equipamientos de Cundinamarca» no traía «Equipamiento Cundinamarca»).
        hints=DiscoveryHints(max_results=max(limit, _LOTE_PARA_ORDENAR), region=region),
        conversation_history=conversation_history,
    )

    services = []
    for idx, item in enumerate(response.items[:limit], 1):
        services.append({
            "id": idx,
            "name": item.title,
            "description": (item.description or "")[:200],
            "url": item.service_url,
            "type": item.service_type,
            "owner": item.owner,
            "org": item.org,
            "layer_count": 1 if item.layer_id is not None else 0,
            # hechos para que quien elige (el LLM) juzgue la fuente; antes se perdían por el camino
            "layer_id": item.layer_id,
            "credits": item.credits,
            "views": item.views,
            "completeness": item.completeness,
            "single_layer": item.single_layer,
            "modified": item.modified,
            "sources": item.sources,
        })

    return {
        "query": {
            "search_query": search_query,
            "text_query": (response.debug.get("search_params", {}) or {}).get("text_query", search_query),
            "hub_params": response.debug.get("search_params", {}),
            "intent": response.intent,
            "llm_refined_query": response.debug.get("llm_refined_query"),
        },
        "services": services,
        "total_found": len(services),
        "catalog_empty": False,
        "authority_warning": response.authority_warning,
        "place_mismatch": response.place_mismatch,
        # el juicio del buscador: el agente del chat lo ve con la lista
        "criterio": response.criterio,
        "otras_busquedas": response.otras_busquedas,
        "relevantes": response.relevantes,
        "place_queried": response.place_queried,
    }
