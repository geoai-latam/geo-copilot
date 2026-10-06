"""
SymbologyAgent node — extraído de ``graph.py`` en Fase 6 #6.

Genera la simbología sobre los datos cargados (color, opacidad, tamaño,
clasificación). No depende de la BD ni del LLM (sólo invoca al agente
de simbología, que sí los puede usar internamente).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from geo_copilot.core.colors import log_agent
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Generar simbología basada en los datos GeoJSON del estado.

    Smart Router: si el usuario pide ``apply_symbology`` (cambiar color
    sobre capa ya cargada) el nodo lee ``previous_geojson`` como capa
    activa — antes solo miraba ``state.geojson`` y rebotaba con "no hay
    datos para simbolizar" aunque el usuario tenía 3 capas en el mapa.
    """
    from geo_copilot.orchestrator.nodes._layer_context import resolve_active_layer

    geojson, source_name, source_kind = resolve_active_layer(state)
    # H23: una capa grande del workspace llega sin datos, con una MUESTRA para
    # diseñar el estilo (las clases se aplican luego a todas sus teselas).
    total_muestra: int | None = None
    if not geojson:
        from geo_copilot.orchestrator.layer_resolution import muestra_para_estilo

        m = muestra_para_estilo(dict(state))
        if m is not None:
            geojson, total_muestra, _ = m
            source_name, source_kind = f"muestra de {total_muestra} elementos", "muestra"
    feature_count = len(geojson.get("features", [])) if geojson else 0
    log_agent(
        "SymbologyAgent", "Generando simbología...",
        f"Features: {feature_count}, source={source_kind}",
    )
    logger.info(
        f"[SymbologyAgent] source={source_kind} ({source_name}), "
        f"features={feature_count}"
    )

    if not geojson:
        return {
            "current_agent": "symbology_agent",
            # H23: sin esto la observación decía "0 elemento(s)" y el LLM
            # narró la capa "coloreada por estado" sobre un mapa monocromo.
            "error": "No hay una capa con datos para simbolizar",
            "messages": [{
                "agent": "symbology_agent",
                "content": "No hay datos geográficos para simbolizar",
                "data": None,
                "success": False,
            }],
        }

    try:
        response = await graph.symbology_agent.process(
            query=_pedido_completo(dict(state)),
            context={"geojson": geojson, "estilo_actual": _estilo_actual(dict(state))},
        )

        if response.success:
            symbology = response.data
            layer_name = symbology.get("layer_title", "Capa de datos")
            if total_muestra is not None:
                # Los conteos por clase salieron de la muestra, no de la capa:
                # la leyenda no los muestra (serían falsos).
                for b in symbology.get("class_breaks") or []:
                    if isinstance(b, dict):
                        b.pop("count", None)
            # Si la simbología se aplicó sobre ``previous_geojson`` (skip
            # de data/gis), también propagamos el geojson al state para
            # que el responder lo re-emita al cliente — sino el frontend
            # queda con la capa vieja sin la nueva simbología aplicada.
            updates: dict = {
                "symbology": symbology,
                "layer_name": layer_name,
                "current_agent": "symbology_agent",
                "messages": [{
                    "agent": "symbology_agent",
                    "content": f"Simbología generada: {layer_name}",
                    "data": symbology,
                    "success": True,
                }],
            }
            if source_kind == "previous" and not state.get("geojson"):
                updates["geojson"] = geojson
            return updates

        return {
            "current_agent": "symbology_agent",
            "messages": [{
                "agent": "symbology_agent",
                "content": response.message,
                "data": None,
                "success": False,
            }],
        }

    except Exception as exc:
        # Captura amplia a propósito: frontera del nodo (LLM + clasificación); el error viaja en el
        # estado.
        logger.error(f"SymbologyAgent error: {exc}", exc_info=True)
        return {
            "current_agent": "symbology_agent",
            "error": str(exc),
            "messages": [],
        }


def _estilo_actual(state: dict) -> dict | None:
    """FH.6: el estilo que tiene AHORA en el mapa la capa que se va a re-estilar (la objetivo o
    la activa), con lo que el usuario fijó a mano. Hecho para el diseñador."""
    capas = (state.get("map_context") or {}).get("layers") or []
    objetivo = state.get("target_layer_id")
    capa = next((c for c in capas if objetivo and c.get("id") == objetivo), None) or next(
        (c for c in capas if c.get("is_active")), None)
    estilo = (capa or {}).get("style")
    return estilo if isinstance(estilo, dict) else None


def _pedido_completo(state: dict) -> str:
    """Lo que el diseñador necesita saber: el pedido del USUARIO y la instrucción del paso.

    T3.0 (V5 en Chrome): en el bucle ReAct el diseñador recibía solo la
    paráfrasis del paso ("coropleto por lisa_clase en 5 clases") y perdía la
    intención ("¿hay agrupamiento? muéstrame los clusters LISA"), así que no
    podía reconocer la convención que sí aplica cuando ve el pedido real. Se le
    dan ambos y decide él.
    """
    paso = str(state.get("query") or "")
    usuario = str(state.get("original_query") or "")
    if usuario and usuario.strip() != paso.strip():
        return f"Pedido del usuario: «{usuario}». Instrucción de este paso: «{paso}»."
    return paso
