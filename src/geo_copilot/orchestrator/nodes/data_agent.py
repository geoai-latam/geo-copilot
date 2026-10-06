"""
DataAgent node — extraído de ``graph.py`` en Fase 6 #6 parte 2.

Valida disponibilidad de datos y maneja fuentes externas: detecta URLs
externas en la consulta o estado, ejecuta búsquedas en catálogos
(ArcGIS, datos.gov.co), realiza fetch de servicios seleccionados por
HITL, y propaga los datos como ``geojson`` + ``external_geojson`` para
el resto del grafo.

Este nodo **no** lleva loop de reintentos: el ``DataAgent`` ya gestiona
sus propios fallbacks internamente y el HITL de selección de servicio
es interactivo (no se puede "auto-corregir").
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from geo_copilot.core.colors import log_agent
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)

# F4: cargar una URL externa y buscar en portales viven en sus módulos; se reexportan porque las
# pruebas y `run` los usan desde aquí (las pruebas sustituyen _handle_external_search aquí).
from geo_copilot.orchestrator.nodes.data_agent_busqueda import (  # noqa: F401
    _handle_external_search,
    format_search_results,
    juzgar_busqueda,
)
from geo_copilot.orchestrator.nodes.data_agent_url import (  # noqa: F401
    _build_imagery_state,
    _capas_del_servicio,
    _es_raiz_feature_server,
    _handle_external_url,
    _is_imagery_url,
    _msg,
    _ofrecer_capas,
    _palabras,
    _resumen,
    _traer_capa,
)

# Patrón de URLs reconocidas (servicios geoespaciales + archivos).
_URL_RE = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
_KNOWN_URL_TOKENS = (
    "arcgis.com", "featureserver", "mapserver",
    ".geojson", ".json", ".shp", ".zip", ".gpkg", ".kml",
    "datos.gov.co", "socrata",
)


def _normaliza_url(url: str) -> str:
    """Forma canónica para comparar procedencia (R0.9)."""
    return (url or "").strip().rstrip("/").lower()


def _external_url_con_procedencia(state: dict, query: str) -> str | None:
    """Devolver la URL externa SÓLO si su procedencia es confiable (R0.9, AUD-16).

    Antes esto era ``state.get("external_url") or extract_external_url(query)``,
    y el primer operando gana. Ese ``external_url`` lo pone el LLM del router a
    partir de su structured output, **sin validar**, y desde ahí llegaba a un
    GET server-side sin allowlist de dominio y sin HITL.

    Eso cerraba un ciclo de inyección indirecta: un Feature Service público en
    ArcGIS Hub con instrucciones dentro del ``title`` entra al system prompt vía
    ``found_services``; el LLM emite ``load_external`` contra el host del
    atacante; la respuesta se parsea y sus nombres de campo vuelven al prompt
    del turno siguiente (segunda etapa). El comentario de ``arcgis.py:413-416``
    afirmaba que "el usuario ya aprobó la URL vía HITL": es falso en este camino.

    Procedencias aceptadas:
      1. La URL aparece TEXTUALMENTE en el mensaje del usuario.
      2. Coincide con un servicio que se le mostró (``found_services``).

    Cualquier otra —es decir, una URL que el modelo se sacó de la manga— se
    descarta con log explícito. No se "arregla" ni se adivina: se ignora.
    """
    del_usuario = extract_external_url(query)
    if del_usuario:
        return del_usuario

    propuesta: str | None = state.get("external_url")
    if not propuesta:
        return None

    objetivo = _normaliza_url(propuesta)
    # La procedencia es que el USUARIO la escribió, no que parezca de ArcGIS: un WFS, un STAC, un
    # .csv o un .fgb pegado en el mensaje no casaba con la lista de tokens de `extract_external_url`
    # y se descartaba como «sin procedencia». Si es cargable lo dice el cargador (error honesto);
    # la SSRF la cubre url_validator.
    if objetivo and objetivo in {_normaliza_url(u) for u in _URL_RE.findall(query or "")}:
        return propuesta
    mostradas = {_normaliza_url(svc.get("url", "")) for svc in (state.get("found_services") or [])
                 if isinstance(svc, dict)} - {""}
    if objetivo and objetivo in mostradas:
        return propuesta
    # una CAPA de un servicio que se le mostró (…/FeatureServer/3): mismo servicio, otra capa
    if objetivo and any(objetivo.startswith(m + "/") and objetivo[len(m) + 1:].isdigit() for m in mostradas):
        return propuesta

    logger.warning(
        "[DataAgent] R0.9: descartada external_url sin procedencia: %r. "
        "No estaba en el mensaje del usuario ni entre los servicios mostrados.",
        propuesta,
    )
    return None


def extract_external_url(query: str) -> str | None:
    """Devolver la primera URL geoespacial reconocible dentro del texto."""
    for url in cast(list[str], _URL_RE.findall(query)):
        if any(token in url.lower() for token in _KNOWN_URL_TOKENS):
            return url
    return None


def _nombre_mostrado(state: dict, url: str) -> str | None:
    """El título con que se le mostró ese servicio (la búsqueda), o el que pasó quien lo eligió."""
    if state.get("_nombre_a_cargar"):  # lo pasa select_service en ESTA llamada (no el de una carga anterior)
        return str(state["_nombre_a_cargar"])
    objetivo = _normaliza_url(url)
    for svc in state.get("found_services") or []:
        if not isinstance(svc, dict):
            continue
        base = _normaliza_url(svc.get("url", ""))
        if base and (objetivo == base or (objetivo.startswith(base + "/") and objetivo[len(base) + 1:].isdigit())):
            return svc.get("name") or None
    return None


def _al_bucle_antes_de_portales() -> bool:
    """Política híbrida/ReAct y algún servicio conectado que el agente puede usar (hechos)."""
    from geo_copilot.core.config import get_settings
    from geo_copilot.orchestrator.graph import _resolve_react_policy
    from geo_copilot.orchestrator.nodes.gis_agent import _hay_servicios_conectados

    return _resolve_react_policy(get_settings()) in ("hybrid", "always") and _hay_servicios_conectados()


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Validar / cargar datos según el intent y el estado de la sesión."""
    log_agent(
        "DataAgent", "Validando datos...",
        f"Intent: {state.get('intent')}, Entities: {state.get('entities')}",
    )
    logger.info(f"[DataAgent] Validating data for entities: {state.get('entities')}")

    query = state.get("query", "")
    session_id = state.get("session_id", "")
    intent = state.get("intent", "")

    if intent == "search_external":
        if not state.get("_desde_bucle") and _al_bucle_antes_de_portales():
            # T5.4 (V5): «muéstrame en el mapa las sedes de Soacha» → el router lo manda a buscar en
            # portales (6/6, con cualquier descripción del servicio) aunque una base conectada lo
            # tiene. Como en T5.1, el turno pasa al bucle: ve los servicios conectados Y la
            # búsqueda en portales, y decide. El prompt del router no se toca (es frágil).
            from geo_copilot.orchestrator.nodes import agent_loop

            motivo = ("el router lo tomó como una búsqueda en portales de datos abiertos (ArcGIS Hub); "
                      "antes mira si ya está en una base de datos o servicio conectado; si no, busca en "
                      "portales con search_external")
            logger.info("[DataAgent] search_external con servicios conectados → agent_loop")
            return await agent_loop.run(graph, {**state, "intent": "analyze", "interpretacion_previa": motivo})
        return await _handle_external_search(
            graph, query, session_id, state.get("session_region"),
            conversation_history=state.get("conversation_history"),
        )

    external_url = _external_url_con_procedencia(cast(dict, state), query)
    if external_url:
        logger.info(f"[DataAgent] External URL detected: {external_url}")
        return await _handle_external_url(external_url, _nombre_mostrado(cast(dict, state), external_url))
    if intent == "load_external" and not state.get("_desde_bucle") and _al_bucle_antes_de_portales():
        # T5.6 (V5): «carga los predios del GeoParquet que caen en @Área 1» → el router dijo
        # load_external, pero no hay URL: lo que se quiere cargar está en un servicio conectado.
        from geo_copilot.orchestrator.nodes import agent_loop

        motivo = ("el router lo tomó como cargar datos externos, pero no hay una URL: mira si está en uno "
                  "de los servicios conectados (archivos, bases de datos) o búscalo en portales")
        logger.info("[DataAgent] load_external sin URL con servicios conectados → agent_loop")
        return await agent_loop.run(graph, {**state, "intent": "analyze", "interpretacion_previa": motivo})
    if intent == "load_external" and state.get("external_url"):
        # la URL propuesta no pasó el control de procedencia (R0.9): decirlo, no «ok, 0 elementos».
        # V3 F5: el bucle pasaba como URL `data:` el GeoJSON de otra herramienta y reintentaba 3 veces.
        msg = ("No se cargó: esa URL no la escribió el usuario ni salió de una búsqueda que se le mostró "
               "(control de procedencia). Si los datos vienen de otra herramienta, esa herramienta ya los "
               "deja como capa cuando traen geometría.")
        return {"current_agent": "data_agent", "error": msg, "final_response": msg,
                "messages": [_msg(msg, success=False)]}

    # Sin URL externa: solo validamos que haya conexión a BD para
    # consulta interna. El siguiente nodo (gis_agent) hace el trabajo.
    if not graph.db_pool:
        return {
            "current_agent": "data_agent",
            "error": "No database connection",
            "messages": [_msg("No hay conexión a la base de datos", success=False)],
        }

    return {
        "current_agent": "data_agent",
        "messages": [_msg(
            "Datos validados y disponibles",
            success=True,
            data={"validated": True},
        )],
    }
