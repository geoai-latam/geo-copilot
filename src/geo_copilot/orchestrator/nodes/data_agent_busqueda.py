"""Buscar en los PORTALES abiertos desde el nodo de datos: la búsqueda, el juicio del LLM sobre los
resultados y cómo se presentan.

Salió de `nodes/data_agent.py` (F4 del plan de calidad: data_agent.py tenía 636 líneas), tal cual.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from geo_copilot.core.error_sanitizer import sanitize_error
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.nodes.data_agent_url import (
    _build_imagery_state,
    _is_imagery_url,
    _msg,
    _resumen,
    _traer_capa,
)

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph

logger = get_logger("geo_copilot.orchestrator.nodes.data_agent")


def format_search_results(
    search_query: str,
    results: list[dict],
    *,
    place_mismatch: bool = False,
    place_queried: str | None = None,
) -> str:
    """Mensaje corto que acompaña a las cards renderizadas por el frontend.

    Si `place_mismatch=True`, advertimos honestamente que ningún resultado
    menciona el lugar pedido — antes mostrábamos los items engañosos sin
    contexto y el usuario asumía que eran de Zipaquirá cuando eran Medellín.
    """
    n = len(results)
    if n == 0:
        return f"No encontré datasets para '{search_query}'. Intenta con otros términos."
    plural = "" if n == 1 else "s"
    base = (
        f"Encontré **{n}** dataset{plural} para *'{search_query}'*. "
        "Haz click en una tarjeta para cargarlo al mapa."
    )
    if place_mismatch and place_queried:
        return (
            f"⚠️ No encontré datasets específicos de **{place_queried}**. "
            f"{base} Los resultados son de otras zonas — considera buscar "
            "por departamento, región, o un tema más amplio."
        )
    return base


async def juzgar_busqueda(graph: GeoAgentGraph, query: str, results: list[dict], base: str) -> str:
    """El LLM dice si lo hallado es lo pedido (y cuál) o que ninguno lo es.

    Pendiente del acta FH: «busca la malla vial de Bogotá» listaba 7 servicios (zonas
    homogéneas, cartografía de Cota…) con «Encontré 7 datasets», sin decir que ninguno era lo
    pedido. Los hechos (título, organización, descripción, tipo) los pone el código; el juicio,
    el LLM. Si el LLM falla queda el mensaje factual (`base`): las tarjetas se ven igual.
    """
    llm = getattr(graph, "llm", None)
    if llm is None or not results:
        return base
    from geo_copilot.core.llm_client import LLMMessage

    filas = "\n".join(
        f"{i}. «{r.get('name', '')}» ({r.get('type', '')}) — {(r.get('description') or '')[:150]}"
        for i, r in enumerate(results[:10], 1))
    sistema = (
        "Eres el asistente de un visor de mapas. El usuario buscó datos en un catálogo público y "
        "estos son los servicios hallados (verá cada uno como una tarjeta con «Cargar al mapa»). "
        "En 1–3 frases, en español: di cuáles responden a lo que pidió y por qué; si NINGUNO es lo "
        "pedido (otro tema, otra zona), dilo claramente y sugiere cómo buscar mejor. No inventes "
        "servicios ni datos que no estén en la lista.")
    try:
        r = await llm.chat([LLMMessage(role="system", content=sistema),
                            LLMMessage(role="user", content=f"Pidió: «{query}»\nHallados:\n{filas}")])
        texto = (getattr(r, "content", "") or "").strip()
        return texto or base
    except Exception:  # el juicio es un añadido: sin él quedan los hechos y las tarjetas
        logger.warning("[DataAgent] no se pudo juzgar la búsqueda", exc_info=True)
        return base


async def _handle_external_search(
    graph: GeoAgentGraph,
    query: str,
    session_id: str,
    session_region: str | None = None,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Búsqueda en fuentes externas (ArcGIS / Socrata catalog)."""
    response, fallo = await _buscar(graph, query, session_id, session_region, conversation_history)
    if fallo is not None:
        return fallo

    if not (response.success and response.data):
        return {
            "current_agent": "data_agent",
            "error": response.message or "Error en búsqueda externa",
            "messages": [_msg(f"Error: {response.message}", success=False)],
        }

    search_results = response.data.get("search_results", {})
    status = search_results.get("status", "")

    if status == "service_selected":
        elegido = await _servicio_elegido(search_results)
        if elegido is not None:
            return elegido

    sin_servicios = _sin_servicios(status, search_results, query)
    if sin_servicios is not None:
        return sin_servicios

    # ``services_found`` (con o sin HITL todavía sin respuesta).
    if status == "services_found" or search_results.get("services"):
        return await _servicios_encontrados(graph, query, search_results)

    # Fallback.
    return {
        "current_agent": "data_agent",
        "final_response": (
            f"Búsqueda completada: "
            f"{search_results.get('message', 'Sin resultados específicos')}"
        ),
        "messages": [_msg("Búsqueda completada", success=True, data=search_results)],
    }


async def _buscar(graph: GeoAgentGraph, query: str, session_id: str, session_region: str | None,
                  conversation_history: list[dict] | None) -> tuple[Any, dict | None]:
    """(respuesta del DataAgent, None) o (None, el estado de error si la búsqueda ni se pudo hacer)."""
    try:
        response = await graph.data_agent.process(
            query=query,
            context={
                "action": "search_external",
                "search_query": query,
                # F2.2: override de región por sesión (None = default configurado).
                "session_region": session_region,
                # #6 (audit 2026-06-14): historial para resolver referencias
                # elípticas en discovery ("busca datos tipo feature" tras un lugar).
                "conversation_history": conversation_history,
            },
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001 — discovery (LLM + HTTP externo); sanitize_error registra el traceback
        from geo_copilot.agents.data_agent.servicio_arcgis import ArcGISNoDisponible

        if isinstance(exc, ArcGISNoDisponible):
            # el motivo lo escribe nuestro propio código (tool deshabilitada hasta re-aprobarla, servidor
            # caído…). Rama arcgis-busqueda (V5): con «inténtalo de nuevo» a secas, el agente acabó
            # diciendo «no hay vías de Bogotá en los portales» cuando la búsqueda ni se había hecho.
            logger.warning(f"[DataAgent] búsqueda en ArcGIS no disponible: {exc}")
            msg = (f"La búsqueda en ArcGIS no se pudo hacer (no es que no haya resultados): {exc}")
            return None, {"current_agent": "data_agent", "error": msg, "messages": [_msg(msg, success=False)]}
        msg = sanitize_error(
            exc, context="data_agent.external_search",
            user_message="No se pudo completar la búsqueda externa. Inténtalo de nuevo.")
        return None, {
            "current_agent": "data_agent",
            "error": msg,
            "messages": [_msg(msg, success=False)],
        }
    return response, None


async def _servicio_elegido(search_results: dict) -> dict | None:
    """Servicio seleccionado vía HITL → fetch automático (None si no trae URL)."""
    selected_service = search_results.get("selected_service", {})
    service_url = search_results.get("url") or selected_service.get("url")
    if service_url:
        service_name = selected_service.get("name", "servicio")
        # MapServer / ImageServer → capa imagery (no GeoJSON).
        kind = _is_imagery_url(service_url)
        if kind:
            return await _build_imagery_state(service_url, service_name, kind)

        geojson, hechos, error_msg = await _traer_capa(service_url)
        if geojson is not None:
            feature_count = len(geojson.get("features", []))
            service_name = selected_service.get("name", "servicio")
            logger.info(f"[DataAgent] Data fetched: {feature_count} features")
            return {
                "current_agent": "data_agent",
                "raw_data": geojson.get("features", []),
                "geojson": geojson,
                "external_geojson": geojson,
                "external_source_url": service_url,
                "external_source_name": service_name,
                "has_external_data": True,
                # Limpiar found_services: la selección ya se consumió.
                "found_services": None,
                "messages": [_msg(
                    f"Datos obtenidos de {service_name}: {_resumen(feature_count, hechos)}",
                    success=True,
                    data={
                        "source": service_url,
                        "service_name": service_name,
                        "feature_count": feature_count,
                        "total_en_servicio": hechos.get("total_en_servicio"),
                        "external": True,
                    },
                )],
            }
        return {
            "current_agent": "data_agent",
            "final_response": f"Error al obtener datos del servicio: {error_msg}",
            "messages": [_msg(f"Error: {error_msg}", success=False)],
        }
    return None


def _sin_servicios(status: str, search_results: dict, query: str) -> dict | None:
    """Catálogo vacío, sin resultados o búsqueda cancelada: lo que se dice en cada caso."""
    if status == "catalog_empty":
        return {
            "current_agent": "data_agent",
            "final_response": "El catálogo de servicios ArcGIS está vacío. Ejecutando indexación...",
            "messages": [_msg(
                "Catálogo vacío",
                success=False,
                data={"action_required": "refresh_catalog"},
            )],
        }

    if status == "no_results":
        return {
            "current_agent": "data_agent",
            "final_response": (
                f"No se encontraron servicios ArcGIS para: '{query}'. "
                "Intenta con otros términos."
            ),
            "messages": [_msg("Sin resultados", success=True, data={"search_query": query})],
        }

    if status == "cancelled":
        # V5: el bucle leía «0 elemento(s)» y daba por hecho que no había nada: no se buscó porque la
        # persona no aprobó la búsqueda (o la aprobación caducó)
        motivo = ("la aprobación de la búsqueda caducó sin respuesta: no se buscó"
                  if search_results.get("caducada") else "el usuario no aprobó la búsqueda: no se buscó")
        return {
            "current_agent": "data_agent",
            "error": motivo,
            "final_response": ("La aprobación de la búsqueda caducó." if search_results.get("caducada")
                               else "Búsqueda cancelada por el usuario."),
            "messages": [_msg("Cancelado", success=True, data={"search_query": query})],
        }
    return None


async def _servicios_encontrados(graph: GeoAgentGraph, query: str, search_results: dict) -> dict:
    """Los servicios hallados (con sus hechos) y el juicio del LLM sobre ellos."""
    services = search_results.get("services", [])
    formatted_results = [
        {
            "name": svc.get("name", "Sin nombre"),
            "description": (svc.get("description") or "")[:150],
            "url": svc.get("url", ""),
            "type": svc.get("type", "ArcGIS"),
            "layer_count": svc.get("layer_count", 0),
            # rama arcgis-busqueda: sin estos hechos el LLM elegía «el 1» a ciegas
            **{k: svc.get(k) for k in ("owner", "org", "credits", "views", "completeness",
                                       "single_layer", "layer_id", "modified") if svc.get(k) not in (None, "")},
        }
        for svc in services[:10]
    ]
    return {
        "current_agent": "data_agent",
        "final_response": await juzgar_busqueda(graph, query, formatted_results, format_search_results(
            query,
            formatted_results,
            place_mismatch=bool(search_results.get("place_mismatch")),
            place_queried=search_results.get("place_queried"),
        )),
        "found_services": formatted_results,
        "new_search_executed": True,
        "messages": [_msg(
            f"Encontrados {len(services)} servicios",
            success=True,
            data={"search_query": query, "results": formatted_results, "total": len(services)},
        )],
    }
