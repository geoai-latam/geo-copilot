"""
PythonAgent node — extraído de ``graph.py`` en Fase 6 #6 parte 2.

Ejecuta operaciones espaciales en memoria (buffer, centroid, intersect,
etc.) sobre los datos cargados (internos o externos). El ``PythonAgent``
genera código que corre en el sandbox aislado (subprocess + rlimits;
ver Fase 1 SEC-1).

El bucle de auto-corrección (fallo de ejecución → ``CodeCorrector`` →
retry) usa ``RetryExecutor`` igual que el GISAgent — la misma
abstracción única introducida en Fase 6 #4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from geo_copilot.agents.python_agent.code_corrector import (
    CodeCorrector,  # noqa: F401 — lo usa IntentoPython vía el nodo (pruebas)
)
from geo_copilot.core.colors import log_agent
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.nodes.python_intento import IntentoPython
from geo_copilot.orchestrator.retry import RetryExecutor

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


_OPERACIONES = {"buffer": "buffer", "centroid": "centroides", "area": "área", "union": "unión",
                "intersect": "intersección", "dissolve": "disuelta", "clip": "recorte", "distance": "distancias",
                "heatmap": "calor", "cluster": "grupos"}


def _nombre_resultado(fuente: str | None, operacion: str | None) -> str:
    """«Lotes Manzana 008510017 · área»: la capa de entrada y lo que se le hizo."""
    base = (fuente or "Resultado").strip()[:80]
    op = _OPERACIONES.get(str(operacion or "").lower(), str(operacion or "").strip().lower())
    return f"{base} · {op}"[:120] if op and op != "other" else f"{base} (resultado)"


def _msg(content: str, *, success: bool, data: dict | None = None) -> dict:
    return {"agent": "python_agent", "content": content, "data": data, "success": success}


def _select_working_data(state: GraphState) -> tuple[dict | None, str, str]:
    """Decidir qué GeoJSON usa el agente.

    Devuelve ``(geojson, source_name, active_source)`` donde
    ``active_source`` es ``"internal" | "external" | "previous" | "none"``.

    S1.4: delega en `orchestrator.layer_resolution.resolver_capa`. Antes este
    nodo priorizaba la fuente DECLARADA activa mientras simbología priorizaba los
    datos del turno: con una capa activa en el mapa y una consulta nueva en el
    mismo turno, cada uno operaba sobre una capa distinta.
    """
    from geo_copilot.orchestrator.layer_resolution import resolver_capa

    capa = resolver_capa(dict(state))
    if capa is None:
        return None, "sin fuente activa", state.get("active_data_source", "none")
    por_defecto = {
        "target": "capa seleccionada",
        "internal": "base de datos interna",
        "external": "servicio externo",
        "previous": "capa cargada previamente",
    }
    kind = "external" if capa.origen == "target" else capa.origen
    return capa.geojson, capa.name or por_defecto[capa.origen], kind


def _select_secondary_data(state: Any, primary_source: str | None,
                           primary_geojson: Any) -> tuple[dict | None, str | None]:
    """La OTRA capa cargada, distinta de la activa, para cruces cross-source.

    Cuando hay una capa de la BD (internal/previous) Y otra de un servicio REST
    (external) cargadas a la vez, la activa va como ``gdf`` y esta como ``gdf2``.
    Devuelve ``(geojson, name)`` o ``(None, None)``. No adivina: solo devuelve
    una capa realmente cargada y distinta de la primaria.
    """
    def _has_features(gj: Any) -> bool:
        return bool(gj and (gj.get("features") or []))

    candidates: list[tuple[dict, str]] = []
    # Si la activa NO es externa, la externa (REST) es candidata a segunda capa.
    if primary_source != "external":
        ext = state.get("external_geojson")
        if _has_features(ext):
            candidates.append((ext, state.get("external_source_name") or "servicio externo"))
    # Si la activa NO es interna, la interna (BD) es candidata.
    if primary_source != "internal":
        internal = state.get("geojson")
        if _has_features(internal):
            candidates.append((internal, state.get("active_source_name") or "base de datos interna"))
    # La heredada del turno anterior, si es distinta de la primaria.
    prev = state.get("previous_geojson")
    if _has_features(prev):
        candidates.append((prev, state.get("active_source_name") or "capa previa"))

    for gj, name in candidates:
        if gj is not primary_geojson:
            return gj, name
    return None, None


async def _denegado_por_rol(state: GraphState) -> dict | None:
    """F6 (S6.3): el sandbox CALCULA (riesgo `compute`): un visor no lo usa.

    Por el bucle del agente ya lo impide `capabilities.ejecutar`; esto cubre el camino
    directo del router a este nodo (p. ej. un «analiza…» que no pasa por el bucle).
    """
    from geo_copilot.platform import auditoria
    from geo_copilot.platform.capabilities import ROL_POR_RIESGO
    from geo_copilot.platform.identidad.principal import principal_actual

    principal = principal_actual()
    minimo = ROL_POR_RIESGO["compute"]
    if principal is None or principal.puede(minimo):
        return None
    await auditoria.registrar("capacidad.ejecutar", "core.python_sandbox", "denegado",
                              session_id=state.get("session_id"),
                              detalle={"origen": "grafo", "riesgo": "compute", "consulta": state.get("query"),
                                       "rol": principal.rol, "rol_requerido": minimo})
    msg = (f"No puedo hacer ese cálculo: las operaciones de análisis y cálculo espacial requieren el rol "
           f"«{minimo}» y tu rol es «{principal.rol}». Un administrador de tu organización puede cambiártelo.")
    return {"current_agent": "python_agent", "error": msg, "final_response": msg,
            "messages": [_msg(msg, success=False)]}


def _sin_sandbox() -> dict:
    """F1.1: enforcement de autoconocimiento. El cómputo geométrico en memoria corre en el sandbox
    POSIX; si no está disponible (Windows), NO generamos código ni pedimos HITL para luego fallar opaco
    — cortamos AQUÍ con un mensaje honesto y la alternativa PostGIS. (El note al router es solo
    advisory; esto es el guard determinista.)"""
    logger.warning(
        "[PythonAgent] Sandbox no disponible (no-POSIX) — operación "
        "espacial en memoria rechazada con alternativa PostGIS."
    )
    honest = (
        "El cómputo geométrico en memoria (buffer, área, centroide, "
        "intersección) requiere un entorno Linux/Docker y no está "
        "disponible en esta plataforma. Si tus datos están en la base de "
        "datos interna (PostGIS), puedo hacer estas operaciones con SQL "
        "(ST_Buffer, ST_Area, ST_Centroid…) — pídemelo como una consulta "
        "a la base de datos."
    )
    return {
        "current_agent": "python_agent",
        # También como `error`: en el bucle ReAct la observación sale de aquí;
        # sin él decía «0 elemento(s)» y el agente concluía que la capa estaba vacía.
        "error": honest,
        "final_response": honest,
        "messages": [_msg(honest, success=False)],
    }


async def _tras_reintentos(state: GraphState, result: Any, intento: IntentoPython, max_retries: int) -> dict:
    """Se agotaron los reintentos: el error con su contexto (y el aviso al cliente)."""
    session_id = state.get("session_id", "")
    final_error = result.outcome.error or "Unknown error"
    if session_id and result.attempts > 1:
        from geo_copilot.platform import events
        await events.sink().retry_result(
            session_id=session_id, agent="python_agent", success=False, attempts=result.attempts,
            message=f"No se pudo corregir el error: {final_error[:100]}",
        )
    return {
        "current_agent": "python_agent",
        "error": final_error,
        "retry_count": max(0, result.attempts - 1),
        "last_error": final_error,
        "error_context": {
            "all_errors": intento.errors_collected,
            "attempts": result.attempts,
            "max_retries": max_retries,
        },
        "messages": [_msg(
            f"Error después de {result.attempts} intento(s): {final_error[:100]}",
            success=False,
            data={"errors": intento.errors_collected},
        )],
    }


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Ejecutar la operación espacial con retry + auto-corrección."""
    denegado = await _denegado_por_rol(state)
    if denegado is not None:
        return denegado
    working_geojson, source_name, active_source = _select_working_data(state)
    feature_count = len(working_geojson.get("features", [])) if working_geojson else 0

    log_agent(
        "PythonAgent", "Procesando operación espacial...",
        f"Features: {feature_count}, Query: {state['query'][:50]}...",
    )
    logger.info(
        f"[PythonAgent] Processing spatial operation on {source_name} "
        f"({feature_count} features)"
    )

    if not working_geojson:
        return {
            "current_agent": "python_agent",
            "error": (
                "No hay datos cargados para procesar. "
                "Primero consulta la base de datos o carga un servicio externo."
            ),
            "messages": [_msg("No hay datos cargados", success=False)],
        }
    from geo_copilot.agents.gis_agent.sandbox import SANDBOX_AVAILABLE
    if not SANDBOX_AVAILABLE:
        return _sin_sandbox()

    settings = get_settings()
    autonomous_mode = state.get("autonomous_mode", settings.autonomous_mode)
    max_retries = state.get("max_retries", settings.max_retries)
    max_attempts = (max_retries + 1) if autonomous_mode else 1
    columns: list[str] = []
    if working_geojson.get("features"):
        columns = list(working_geojson["features"][0].get("properties", {}).keys())
    # Cross-source: la OTRA capa cargada (distinta de la activa), si existe, se
    # expone al sandbox como `gdf2` para cruces (spatial join / overlay / clip)
    # entre una capa de la BD y otra de un servicio REST, por ejemplo.
    secondary_geojson, secondary_name = _select_secondary_data(state, active_source, working_geojson)
    if secondary_geojson:
        logger.info("[PythonAgent] Segunda capa disponible para cross-source: %s", secondary_name)

    intento = IntentoPython(graph=graph, state=state, working_geojson=working_geojson, source_name=source_name,
                            active_source=active_source, secondary_geojson=secondary_geojson,
                            secondary_name=secondary_name, columns=columns, feature_count=feature_count)
    result = await RetryExecutor(max_attempts=max_attempts).run(
        attempt=intento.intento,
        correct=intento.corregir if autonomous_mode else None,
        on_retry=intento.notificar_reintento,
    )
    if result.outcome.success or result.outcome.no_retry:
        return result.outcome.payload
    return await _tras_reintentos(state, result, intento, max_retries)
