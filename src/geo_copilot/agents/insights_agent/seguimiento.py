"""El SEGUIMIENTO: preguntas sobre lo que ya hay (la conversación, el mapa y la última consulta).
El LLM responde con lo escrito o pide que se ejecute (no mide ni calcula).

Salió de `InsightsAgent` (F4 del plan de calidad: agent.py tenía 1.610 líneas), tal cual.
"""

import json
from typing import TYPE_CHECKING, Any, cast

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm
from geo_copilot.prompts import cargar_prompt

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.insights_agent.agent")


def _contexto_seguimiento(query: str, previous_sql: str | None, previous_results: list[dict] | None,
                         previous_geojson: dict | None, conversation_history: list[dict] | None,
                         map_context: dict | None, map_layers: dict | None) -> str:
    """Los hechos del seguimiento: la conversación, el mapa, la última consulta y su capa."""
    # Construir contexto para el LLM
    context = f"Pregunta del usuario: {query}\n\n"

    # FH.1: la conversación y el mapa que ve el usuario (capas, su aspecto y lo
    # que pasó en él desde la última respuesta) son hechos del seguimiento.
    if conversation_history:
        context += "CONVERSACIÓN RECIENTE:\n" + "\n".join(
            f"  {'Usuario' if m.get('role') == 'user' else 'Asistente'}: {str(m.get('content', ''))[:300]}"
            for m in conversation_history[-6:]
        ) + "\n\n"
    if map_context:
        from geo_copilot.core.formatters import format_map_context

        bloque = format_map_context(map_context)
        if bloque:
            context += bloque + "\n\n"
    # V5 FH.7: con solo metadatos («FILTRADA: 3 de 27»), el seguimiento repitió el lote
    # mayor de los 27 (quedaba fuera del filtro). Una capa pequeña va con TODAS sus filas
    # (ya filtradas: la capa filtrada ES su subconjunto); una grande, sin filas.
    if map_layers:
        from geo_copilot.orchestrator.react_tools import _filas_tabulares

        for lid, capa in map_layers.items():
            if isinstance(capa, dict) and isinstance(capa.get("data"), dict):
                filas = _filas_tabulares({"geojson": capa["data"]}).replace("[[layer:activa", f"[[layer:{lid}")
                if filas:
                    context += f"Capa [{lid}] «{capa.get('name', '')}»{filas}\n"

    if previous_sql:
        context += f"SQL ejecutado anteriormente:\n```sql\n{previous_sql}\n```\n\n"

    if previous_results:
        # V5 FH.4: con 3 filas de muestra y «calcúlalas», respondió «**X hectáreas**».
        # El hecho: es una MUESTRA, no los datos para calcular un total.
        context += (f"Resultado de la ÚLTIMA CONSULTA A LA BASE (puede no ser la capa de la que "
                    f"habla el usuario): {len(previous_results)} fila(s).\n")
        muestra = previous_results[:3]
        context += (f"MUESTRA de {len(muestra)} de esas {len(previous_results)} fila(s) (no alcanza para "
                    f"totales ni promedios de todas):\n{json.dumps(muestra, indent=2, default=str)[:1000]}\n")

    # Si hay una capa mostrada, dale al LLM su forma real (nº de features,
    # geometría, propiedades) para que el follow-up sea concreto en vez de
    # adivinar — p. ej. "¿cuántas hay en el mapa?" / "¿qué campos tienen?".
    feats = (previous_geojson or {}).get("features") if previous_geojson else None
    if feats:
        geom_type = (feats[0].get("geometry") or {}).get("type") if feats[0] else None
        prop_keys = list((feats[0].get("properties") or {}).keys()) if feats[0] else []
        context += (
            f"\nCapa de esa última consulta: {len(feats)} features"
            f"{f' de tipo {geom_type}' if geom_type else ''} (lo que el mapa tiene AHORA está en "
            f"CAPAS EN EL MAPA).\n"
        )
        if prop_keys:
            context += f"Campos disponibles por feature: {', '.join(prop_keys[:20])}\n"
    return context


def _error_seguimiento(e: Exception) -> dict[str, Any]:
    """Fallo honesto del seguimiento (sin plantillas que parezcan análisis)."""
    return {
        "current_agent": "insights_agent",
        "final_response": (
            f"No pude procesar tu seguimiento: {type(e).__name__}. "
            "Reformula la pregunta o ejecuta una consulta nueva."
        ),
        "messages": [{
            "agent": "insights_agent",
            "content": f"Follow-up error: {e}",
            "data": {"type": "error", "error_type": type(e).__name__},
            "success": False,
        }]
    }


class SeguimientoMixin:
    """Preguntas de seguimiento sobre lo que ya hay en la conversación y el mapa."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient | None

    async def handle_follow_up(
        self,
        query: str,
        previous_sql: str | None = None,
        previous_results: list[dict] | None = None,
        previous_geojson: dict | None = None,
        conversation_history: list[dict] | None = None,
        map_context: dict | None = None,
        map_layers: dict | None = None,
    ) -> dict:
        """Manejar preguntas de seguimiento sobre resultados previos.

        `previous_geojson` (la capa mostrada antes) es solo contexto para razonar sobre sus features
        reales; NO se re-emite como capa. Devuelve la respuesta y sus mensajes.
        """
        from geo_copilot.core.formatters import INSTRUCCION_REFERENCIAS

        context = _contexto_seguimiento(query, previous_sql, previous_results, previous_geojson,
                                        conversation_history, map_context, map_layers)

        # V5 EH.6: instrucciones y datos iban juntos en UN mensaje de usuario, y el filtro de
        # contenido de Azure (Prompt Shields) lo marcó como «jailbreak». Las instrucciones van
        # como sistema y el mapa/conversación como el mensaje del usuario: mismo contenido.
        instrucciones = cargar_prompt("insights_seguimiento").format(referencias=INSTRUCCION_REFERENCIAS)

        try:
            # sin LLM (None) el AttributeError cae en el except: fallo honesto al usuario
            response = await cast("LLMClient", self.llm_client).chat([
                LLMMessage(role="system", content=instrucciones),
                LLMMessage(role="user", content=context),
            ])
            decision = parse_json_from_llm(response.content)
            if decision.get("necesita_ejecutar") is True:
                # V5 FH.4: el seguimiento respondía «**X hectáreas**» porque no puede medir.
                # Lo decide el LLM (no una regla): el turno pasa a quien sí mide.
                motivo = str(decision.get("motivo") or "")[:300]
                logger.info(f"[InsightsAgent] follow_up necesita ejecutar: {motivo}")
                return {"current_agent": "insights_agent", "necesita_ejecutar": True, "motivo_ejecutar": motivo}
            texto = str(decision.get("respuesta") or response.content).strip()

            return {
                "current_agent": "insights_agent",
                "final_response": texto,
                "messages": [{
                    "agent": "insights_agent",
                    "content": texto,
                    "data": {"type": "follow_up", "previous_sql": previous_sql is not None},
                    "success": True
                }]
            }

        except Exception as e:  # captura amplia a propósito: llamada al LLM (red/proveedor/timeout); se falla honestamente al usuario
            # Antes había un fallback que armaba a mano una respuesta tipo
            # "SQL ejecutado: ... Resultados: N registros. Columnas: ..."
            # — el usuario creía que era análisis del agente, cuando era
            # plantilla. Fallamos honestamente y dejamos que reformule.
            logger.error(f"[InsightsAgent] Follow-up handling error: {e}", exc_info=True)
            return _error_seguimiento(e)
