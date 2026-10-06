"""agent_loop — bucle ReAct con tool-calling nativo (Fase 4 / F4.4).

Cierra el gap central del diagnóstico agéntico: ``LLMClient.chat()`` acepta
``tools=`` y los backends parsean ``tool_calls``, pero ``tools=`` NUNCA se pasaba
— el sistema era un workflow con routing por ``intent``, no un agente que ELIGE
sus acciones. Este nodo SÍ pasa el catálogo de herramientas (F4.1) y actúa sobre
los ``tool_calls`` que devuelve el LLM, en un bucle:

    pensar → elegir herramienta → ejecutarla → observar → volver a pensar …
    … hasta que el LLM llama a `answer` (termina) o salta el circuit-breaker.

Propiedades agénticas que aporta:
  - el agente decide la SECUENCIA de acciones dinámicamente (no un plan fijo);
  - cada acción reusa los nodos existentes (HITL, auto-corrección, juez de 0
    resultados) vía ``dispatch_tool`` (F4.4 react_tools);
  - acotado por el ``CircuitBreaker`` (F4.3) — nunca bucle infinito;
  - auditado por ``DecisionTrace`` (F4.T3) — traza sanitizada de cada decisión.

NOTA (v1): la observación se realimenta como texto (scratchpad ReAct), porque el
``LLMMessage`` actual no modela los roles tool/assistant-tool_calls nativos. El
LLM SÍ emite ``tool_calls`` nativos en cada paso (se pasa ``tools=``); el
round-trip nativo de mensajes es un follow-up. El nodo NO está cableado al grafo
por defecto todavía (la integración es un paso aparte, revisado).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from geo_copilot.core.config import get_settings
from geo_copilot.core.formatters import INSTRUCCION_REFERENCIAS
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.capabilities_core import (
    ensure_core,  # noqa: F401 — lo usa BucleReAct desde aquí
)
from geo_copilot.orchestrator.react_tools import (
    dispatch_tool,  # noqa: F401 — punto de inyección (BucleReAct, pruebas)
)
from geo_copilot.platform import events
from geo_copilot.platform.capabilities import ANSWER_TOOL, registry
from geo_copilot.prompts import cargar_prompt

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)

# T1.4 (#18): el camino ReAct no emitía progreso — el chip de agentes quedaba
# quieto mientras el bucle trabajaba. Cada herramienta se anuncia como un paso
# de agente; el (agente, descripción) lo declara la capacidad (`Capability.step`)
# con las palabras que el chip reconoce (`useAgentsPipeline.classifyStep`).


async def _anunciar(session_id: str, herramienta: str, status: str) -> None:
    """Progreso best-effort: un fallo del transporte nunca tumba el bucle."""
    if not session_id:
        return
    cap = registry().get(herramienta)
    agente, descripcion = cap.step if cap and cap.step[1] else (
        "agent_loop", f"Herramienta {herramienta}",
    )
    try:
        await events.sink().agent_step(session_id, agente, descripcion, status)
    except Exception:  # telemetría de progreso: nunca interrumpe el razonamiento
        logger.debug("[agent_loop] no se pudo emitir el progreso", exc_info=True)


REACT_SYSTEM_PROMPT = cargar_prompt("react")

def _lectura_del_router(state: dict) -> str | None:
    """La razón con la que el router clasificó este mensaje (su lectura del pedido con el mapa)."""
    for m in reversed(state.get("messages") or []):
        if isinstance(m, dict) and m.get("agent") == "router":
            razon = str(((m.get("data") or {}).get("reasoning")) or "").strip()
            return razon[:400] or None
    return None


def react_system_prompt(graph: object = None, hoy: date | None = None) -> str:
    """System prompt ReAct con la lista de herramientas del registro (S1.3) y la
    fecha de hoy.

    La fecha importa: sin ella, cuando el NDVI de los últimos 45 días no
    encontraba escenas sin nubes, el LLM "ampliaba" el rango hacia 2023-2024 (la
    época de su entrenamiento) y presentaba imagery de hace dos años como actual
    (visto al validar H8, recorrido 06).
    """
    hoy = hoy or date.today()
    return (
        REACT_SYSTEM_PROMPT.format(herramientas=_bloque_herramientas(graph))
        + f"\n8. Hoy es {hoy.isoformat()}. Interpreta \"reciente\", \"este año\" o"
        " \"los últimos meses\" desde esa fecha, y si amplías un rango de fechas,"
        " amplíalo hacia atrás DESDE HOY. Nunca uses fechas de tu entrenamiento."
        # FH.7: junto a las demás reglas de `answer` (al final de un prompt largo, el
        # LLM enlazaba la capa pero casi nunca el elemento: 1/4 con gpt-4.1-mini).
        + "\n9. En `answer`, " + INSTRUCCION_REFERENCIAS
    )


# FRT-04: convención de capa objetivo. Se ANEXA al system prompt SOLO cuando hay
# capas en el mapa (ver run()), junto al listado de "CAPAS EN EL MAPA" con sus
# [id]. Sin esto el LLM del bucle no conoce los ids y nunca puede dirigir una
# tool a la capa NOMBRADA por el usuario (sólo operaría sobre la activa). Espeja
# la regla del router_agent (prompts.py) para el camino ReAct.
_TARGET_LAYER_RULE = """⚠️ CAPA OBJETIVO (`target_layer_id`) — para apply_symbology, spatial_operation y analyze_layer:
- Si hay 2+ capas arriba y el usuario NOMBRA una específica ("colorea LOS LOTES", "el área de LOS PREDIOS"), pon su [id] EXACTO (el de los corchetes) en `target_layer_id`.
- Si no nombra ninguna, hay una sola capa, o es ambiguo: omite `target_layer_id` (se opera sobre la capa >> ACTIVA).
- Las herramientas de servicios conectados (`<servicio>__…`) no llevan `target_layer_id`: en su argumento geo pon el [id] de la capa (o `viewport`).
- No inventes ids: usa sólo los que aparecen entre corchetes arriba."""

# Claves de datos del estado de trabajo que se exponen en el resultado final
# (para que la respuesta lleve el geojson/simbología producidos por el bucle).
# R1.4: incluye el canal analítico (data/visualization/python_code) — antes un
# análisis exitoso dentro del bucle se perdía al salir (GraphState los declara).
_OUTPUT_KEYS = (
    "geojson", "raw_data", "sql", "symbology", "layer_name",
    "external_geojson", "has_external_data", "empty_result_verdict",
    "data", "visualization", "analiticos", "map_commands", "python_code", "external_imagery", "imagery_previas",
    # FRT-04: la capa objetivo que la tool eligió por nombre, para que el cliente
    # re-estile ESA capa (el bucle ReAct no pasa por el router node que lo setea).
    "target_layer_id",
    # ORQ-10 (auditoría): una búsqueda externa DENTRO del bucle mergea estas
    # claves al working state (react_tools._MERGE_KEYS) pero se perdían al salir,
    # así que las cards de descubrimiento y "carga el N" no llegaban a la UI ni a
    # _map_final_state. El build de arriba solo las incluye si están presentes.
    "found_services", "new_search_executed",
    "external_source_name", "active_data_source",
    # S2.5: dataset que produjo una capacidad ws_* (query.py no lo re-materializa).
    "result_layer_ref",
)


def _umbral_mcp() -> int:
    valor = getattr(get_settings(), "mcp_tools_umbral", 25)
    return valor if isinstance(valor, int) else 25


def _bloque_herramientas(graph: Any) -> str:
    """La lista del prompt: la misma selección que los schemas del primer paso (S3.7)."""
    from geo_copilot.platform.capabilities import _ANSWER_BLURB
    from geo_copilot.platform.mcp.busqueda import catalogo, seleccion

    umbral = _umbral_mcp()
    lineas = [f"- {c.tool_name}: {c.blurb}" for c in seleccion(graph, [], umbral)]
    bloque = "\n".join([*lineas, f"- {ANSWER_TOOL}: {_ANSWER_BLURB}"])
    cat = catalogo(graph, [], umbral)
    return f"{bloque}\n\n{cat}" if cat else bloque


def _esquemas_del_paso(graph: Any, working: dict) -> list[dict]:
    from geo_copilot.platform.capabilities import _answer_schema
    from geo_copilot.platform.mcp.busqueda import ACTIVADAS, seleccion

    umbral = _umbral_mcp()
    caps = seleccion(graph, working.get(ACTIVADAS) or [], umbral)
    return [c.tool_schema() for c in caps] + [_answer_schema()]


def _conversacion_reciente(historial: list[dict] | None, query: str, *, turnos: int = 6) -> list[LLMMessage]:
    """Los últimos turnos de la conversación, antes del actual (V5 F3).

    Sin ellos «inténtalo de nuevo» llegaba solo al bucle: tras pedir el NDVI por
    lote de enero-febrero, el agente calculó el NDVI general de una escena de agosto.
    """
    previos = [m for m in (historial or []) if m.get("role") in ("user", "assistant") and m.get("content")]
    if previos and previos[-1]["role"] == "user" and previos[-1]["content"] == query:
        previos = previos[:-1]  # el turno actual ya va como último mensaje
    return [LLMMessage(role=m["role"], content=str(m["content"])[:1500]) for m in previos[-turnos:]]


def _bloques_de(response: Any, call_id: str) -> list[dict] | None:
    """El turno crudo del proveedor (con su razonamiento) para reenviarlo tal cual, sin las llamadas
    que no se ejecutaron (cada ``tool_use`` reenviado exige su ``tool_result``)."""
    bloques = getattr(response, "provider_blocks", None)
    if not isinstance(bloques, list) or not bloques:
        return None
    return [b for b in bloques if b.get("type") != "tool_use" or b.get("id") == call_id]


def _short(obj: object, n: int = 80) -> str:
    s = " ".join(str(obj).split())
    return s if len(s) <= n else s[: n - 1] + "…"


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Ejecuta el bucle ReAct para la query del estado.

    Devuelve un update con ``final_response``, ``decision_trace`` y las claves de
    datos producidas. Idempotente respecto al estado de entrada (trabaja sobre
    una copia). El turno y sus pasos están en ``bucle_react.BucleReAct`` (F4).
    """
    from geo_copilot.orchestrator.nodes.bucle_react import BucleReAct

    bucle = await BucleReAct.preparar(graph, state)
    while not bucle.breaker.tripped and await bucle.paso():
        pass
    return bucle.cierre()
