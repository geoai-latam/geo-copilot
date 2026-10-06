"""
InsightsAgent node — extraído de ``graph.py`` en Fase 6 #6.

Genera narrativa, contexto y responde follow-ups. Si la consulta es
un ``follow_up`` (pregunta sobre los resultados anteriores), delega
en ``InsightsAgent.handle_follow_up``. En otro caso pide al LLM una
narrativa corta.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from geo_copilot.core.colors import log_agent
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


async def narrate_plan_outcome(
    graph: GeoAgentGraph,
    query: str,
    succeeded: list[dict],
    failed_steps: list[dict],
    skipped_steps: list[dict],
    has_output: bool,
) -> str:
    """Narrativa LLM del desenlace de un plan con fallos parciales.

    Reemplaza el mensaje técnico "El plan no se completó: falló N paso(s)..."
    por una respuesta versátil y útil: lidera con lo que SÍ se logró, explica en
    lenguaje natural qué faltó (sin jerga de step/traceback) y sugiere un
    siguiente paso concreto. Aprovecha el LLM (no un template rígido); cae a un
    mensaje determinista si el LLM no responde.
    """
    ok = len(succeeded)
    total = ok + len(failed_steps) + len(skipped_steps)
    failed_desc = "; ".join(
        f"«{s.get('description') or s.get('query_fragment') or s.get('step_id', 'operación')}»: "
        f"{(s.get('error') or 'no produjo resultado').strip()[:120]}"
        for s in failed_steps
    ) or "una operación no dio resultado"

    prompt = (
        f'El usuario pidió: "{query}"\n'
        f"Se ejecutó un plan de {total} operación(es): {ok} completadas; "
        f"{len(failed_steps)} sin resultado ({failed_desc}); "
        f"{len(skipped_steps)} no se intentaron por depender de las anteriores.\n"
        + (
            "HAY un resultado parcial visible en el mapa/tabla.\n"
            if has_output
            else "NO se produjo un resultado mostrable.\n"
        )
        + "Redacta una respuesta breve (2-3 frases), en español, HONESTA pero NO "
        "alarmante, como un copiloto que ayuda:\n"
        "- Si hay resultado parcial, LIDERA reconociéndolo (qué sí se obtuvo).\n"
        "- Explica en lenguaje natural qué faltó, SIN jerga técnica "
        "(no digas 'paso', 'step', 'traceback', 'query').\n"
        "- Sugiere UN siguiente paso concreto y útil para lograr lo que quería.\n"
        "No uses viñetas."
    )
    try:
        response = await graph.llm.chat([LLMMessage(role="user", content=prompt)])
        text = (response.content or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — narrar es best-effort
        logger.warning("[Responder] narración de desenlace falló: %s", exc)

    # Fallback determinista.
    if has_output:
        return (
            f"Te muestro el resultado de lo que sí se pudo resolver ({ok} de {total}). "
            "No logré completar el resto; dime si quieres que lo reintente de otra forma."
        )
    return (
        "No pude completar esta operación con los datos actuales. "
        "¿Quieres que lo intente con otro enfoque o que ajustemos la consulta?"
    )


async def narrate_analysis(graph: GeoAgentGraph, query: str, visualization: dict) -> str:
    """Narra el resultado de un ANÁLISIS (intent analyze): tabla/estadísticas/
    gráfico del sandbox. Antes caía a "Consulta procesada." porque el analyze no
    deja raw_data (el resultado va en `visualization`). El LLM narra los HECHOS
    reales (números/top/tendencia) — no fabrica. Best-effort; devuelve "" si no."""
    import json

    viz = visualization or {}
    payload: dict = {"type": viz.get("type"), "title": viz.get("title", "")}
    data = viz.get("data")
    if isinstance(data, list) and data:
        payload["filas"] = data[:12]
        payload["total_filas"] = len(data)
    if viz.get("stats"):
        payload["estadisticas"] = viz["stats"]
    if viz.get("x") or viz.get("y"):
        payload["ejes"] = {"x": viz.get("x"), "y": viz.get("y")}

    prompt = (
        f'El usuario pidió: "{query}"\n'
        f"El análisis produjo este resultado (JSON):\n"
        f"{json.dumps(payload, default=str, ensure_ascii=False)[:1600]}\n\n"
        "Narra el resultado en 2-3 frases en español con los HECHOS reales "
        "(números concretos, el mayor/menor, la tendencia) — NO inventes datos. "
        "Menciona lo más relevante y ofrece un siguiente paso útil. Sin viñetas "
        "ni jerga técnica (no digas 'gdf', 'dataframe', 'JSON')."
    )
    try:
        response = await graph.llm.chat([LLMMessage(role="user", content=prompt)])
        text = (response.content or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — narrar es best-effort
        logger.warning("[Responder] narración de análisis falló: %s", exc)
    return ""


async def narrate_result(graph: GeoAgentGraph, state: GraphState) -> str:
    """Narrativa LLM honesta de un resultado de DATOS (conteo / agregación /
    tabla / mapa).

    Se extrajo del nodo para poder reutilizarla desde el ``responder``: un
    conteo va por ``gis_agent -> step_finalizer -> responder`` SIN pasar por
    el nodo insights, así que sin esto el responder caía a "Consulta procesada"
    y nunca verbalizaba el número (E2, audit 2026-06-14). El InsightsAgent/LLM
    sigue siendo quien narra (LLM-pilar); el código solo arma HECHOS (conteo,
    agregación detectada, veredicto del 0) — no fabrica respuestas.
    """
    raw_data = state.get("raw_data") or []

    # LIVE-01: detectar AGREGACIÓN/escalar (COUNT/SUM/AVG) desde raw_data SOLO —
    # 1 fila con TODAS sus columnas numéricas y sin geometría propia — ANTES de
    # mirar ninguna capa. Si no, el feature_count de una capa cargada antes en el
    # estado (p. ej. 200 lotes en external_geojson) contamina la narración:
    # "¿cuántos lotes hay?" respondería "200" en vez del escalar real (933473).
    agg_nums = None
    if len(raw_data) == 1 and isinstance(raw_data[0], dict):
        _row = raw_data[0]
        _nums = {
            k: v
            for k, v in _row.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if _nums and len(_nums) == len(_row):
            agg_nums = _nums

    geojson = state.get("geojson") or state.get("external_geojson") or {}
    # En una agregación NO cuenta la capa activa (LIVE-01): el hecho a narrar es
    # el escalar de la fila, no cuántas features tenga una capa previa.
    feature_count = 0 if agg_nums is not None else (
        len(geojson.get("features", [])) if isinstance(geojson, dict) else 0
    )
    effective_count = feature_count or len(raw_data)
    # Sin nombre de capa (p. ej. un conteo) NO se inventa uno: el fallback
    # "Datos" terminaba narrado como 'en la capa "Datos"'.
    layer_name = state.get("layer_name")
    plural = "registro" if effective_count == 1 else "registros"

    agg_note = ""
    if agg_nums is not None:
        pairs = ", ".join(f"{k} = {v}" for k, v in agg_nums.items())
        agg_note = (
            f"\nIMPORTANTE: la consulta es una AGREGACIÓN; el resultado es "
            f"{pairs}. ESCRIBE ese número en la respuesta, EN CIFRAS y tal cual "
            f"(con separador de miles, p. ej. 933.473), NO en palabras ni "
            f"redondeado (NO digas solo 'Consulta procesada' ni el conteo de otra capa)."
        )

    # F2.3: veredicto sobre un 0 (real vs bug) para narrar honesto.
    honesty_note = ""
    verdict = state.get("empty_result_verdict")
    if effective_count == 0 and isinstance(verdict, dict):
        reason = (verdict.get("reason") or "").strip()
        is_bug = (
            verdict.get("verdict") == "likely_bug"
            and float(verdict.get("confidence") or 0) >= 0.7
        )
        if is_bug:
            honesty_note = (
                "\nIMPORTANTE: el 0 podría deberse a un PROBLEMA en la consulta "
                f"({reason}); no se pudo verificar más. NO afirmes con certeza que "
                "no existen datos: preséntalo como posible limitación y sugiere "
                "reformular o revisar filtros."
            )
        else:
            honesty_note = (
                "\nIMPORTANTE: el análisis indica que este 0 parece ser la "
                f"respuesta REAL ({reason}), no un error. Dilo con naturalidad "
                "(p. ej. 'no hay registros que cumplan esos criterios')."
            )

    # Una tabla SIN capa (p. ej. un conteo por manzana) llega con sus VALORES: con
    # solo «2 registros», el LLM narró «4 lotes en total» sobre una tabla de 44 y 25
    # (V5 F4). Misma regla que las observaciones del bucle ReAct.
    from geo_copilot.core.formatters import INSTRUCCION_REFERENCIAS
    from geo_copilot.orchestrator.react_tools import _filas_tabulares

    filas_note = "" if agg_nums is not None else _filas_tabulares(
        {"raw_data": raw_data, "geojson": geojson if feature_count else None})

    prompt = f"""Genera una respuesta breve y amigable para el usuario sobre estos resultados:

Consulta: "{state.get("query", "")}"
Resultados: {effective_count} {plural}{filas_note}
{f"Nombre de capa: {layer_name}" if layer_name else "(No se cargó ninguna capa: no menciones capas.)"}
{honesty_note}{agg_note}

La respuesta debe:
1. Confirmar qué se encontró (si es un conteo/agregación, da el número EN CIFRAS, nunca en palabras)
2. Mencionar el nombre descriptivo de la capa si hay una
3. Sugerir qué puede hacer el usuario a continuación

Responde en 2-3 oraciones máximo.

{INSTRUCCION_REFERENCIAS}"""
    response = await graph.llm.chat([LLMMessage(role="user", content=prompt)])
    return response.content.strip()


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Generar insights/narrativa sobre los resultados actuales."""
    log_agent(
        "InsightsAgent", "Generando insights...",
        f"Intent: {state.get('intent')}, Data rows: {len(state.get('raw_data') or [])}",
    )
    logger.info(f"[InsightsAgent] Generating insights... Intent: {state.get('intent')}")

    intent = state.get("intent", "")

    try:
        # Follow-up sobre resultados previos: delega al agente especializado.
        if intent == "follow_up":
            result = await graph.insights_agent.handle_follow_up(
                query=state["query"],
                previous_sql=state.get("previous_sql"),
                previous_results=state.get("previous_results"),
                previous_geojson=state.get("previous_geojson"),
                # FH.1 (V5): sin esto el seguimiento no veía la conversación ni el mapa
                # («no veo ninguna instrucción previa sobre colores» justo tras pedirlos).
                conversation_history=state.get("conversation_history"),
                map_context=state.get("map_context"),
                map_layers=state.get("map_layers"),
            )
            if result.get("necesita_ejecutar"):
                # V5 FH.4: el seguimiento solo escribe texto; si el LLM juzga que la
                # respuesta exige calcular, el turno pasa al bucle ReAct (que mide).
                from geo_copilot.core.config import get_settings
                from geo_copilot.orchestrator.graph import _resolve_react_policy

                if _resolve_react_policy(get_settings()) in ("hybrid", "always"):
                    from geo_copilot.orchestrator.nodes import agent_loop

                    logger.info("[InsightsAgent] follow_up → agent_loop: %s", result.get("motivo_ejecutar"))
                    return await agent_loop.run(graph, {**state, "intent": "analyze",
                                                        "interpretacion_previa": result.get("motivo_ejecutar")})
                result = {**result, "final_response": (
                    "Para responder eso hay que calcularlo (" + str(result.get("motivo_ejecutar") or "")
                    + "); pídemelo como un análisis y lo mido.")}
            # El Responder lee ``final_response``; si ``handle_follow_up``
            # devolvió ``response`` (compat), normalizamos.
            if "response" in result and "final_response" not in result:
                result["final_response"] = result["response"]
            return result

        # Contar features REALES de la capa (geojson), no raw_data: para
        # apply_symbology/spatial_operation no hay raw_data (row_count=0), y
        # eso hacía que el LLM dijera "0 registros encontrados" / "no se
        # encontraron registros" aunque la operación SÍ tuvo éxito.
        geojson = state.get("geojson") or state.get("external_geojson") or {}
        feature_count = (
            len(geojson.get("features", [])) if isinstance(geojson, dict) else 0
        )
        raw_count = len(state.get("raw_data") or [])
        effective_count = feature_count or raw_count
        layer_name = state.get("layer_name") or "la capa"
        symbology = state.get("symbology")

        data_summary = {
            "query": state["query"],
            "row_count": effective_count,
            "has_geometry": bool(geojson),
            "layer_name": state.get("layer_name"),
        }

        if intent in ("apply_symbology", "symbology") and symbology:
            # La simbología YA se aplicó con éxito (lo hizo el symbology
            # agent); el narrador solo confirma, no debe inventar un fallo.
            sym_type = symbology.get("symbology_type") if isinstance(symbology, dict) else None
            # Regresión (V3 FH): pidió «por el campo lotdispers» (98 % «N»: una sola categoría en la
            # muestra), el juez dejó un color único y el narrador inventó «no se pudo clasificar».
            # Recibe lo pedido y el PORQUÉ del diseño (del diseñador / su juez).
            porque = str((symbology.get("reasoning") if isinstance(symbology, dict) else "") or "")
            porque = porque.replace("[A2A] InsightsAgent corrigió:", "se ajustó:").split("LLM original")[0].strip()[:300]
            campo = symbology.get("classification_field") if isinstance(symbology, dict) else None
            prompt = f"""El usuario pidió: «{state.get("query", "")}».
La simbología se aplicó CORRECTAMENTE sobre "{layer_name}" ({effective_count} elementos){f", tipo: {sym_type}" if sym_type else ""}{f", campo: {campo}" if campo else ""}.
{f"Por qué quedó así (del diseñador): {porque}" if porque else ""}
Genera una confirmación breve y amigable (1-2 oraciones) de que el estilo se aplicó. Si no es lo que
pidió, di el motivo de arriba tal cual; no inventes otro.
IMPORTANTE: la operación tuvo ÉXITO — NO digas que no se encontraron registros ni inventes nombres de capas distintos."""
            response = await graph.llm.chat([LLMMessage(role="user", content=prompt)])
            narrative = response.content.strip()
        else:
            # E2 (audit 2026-06-14): narración de un resultado de DATOS, extraída
            # a ``narrate_result`` para reutilizarla desde el responder (un conteo
            # NO pasa por este nodo). Verbaliza agregaciones y respeta el
            # veredicto del 0.
            narrative = await narrate_result(graph, state)

        return {
            "current_agent": "insights_agent",
            "final_response": narrative,
            "messages": [{
                "agent": "insights_agent",
                "content": narrative,
                "data": data_summary,
                "success": True,
            }],
        }

    except Exception as exc:  # captura amplia a propósito: narrar es best-effort
        # Captura amplia a propósito: la narración (LLM) es best-effort: la operación ya tuvo éxito.
        logger.error(f"InsightsAgent error: {exc}", exc_info=True)
        # ORQ (auditoría): NO contar solo raw_data — en symbology/analyze/
        # spatial_operation los datos viajan por geojson/visualization (raw_data
        # vacío), así que "Se encontraron 0 resultados" era engañoso tras un éxito.
        _gj = state.get("geojson") or state.get("external_geojson") or {}
        _n = len(_gj.get("features", [])) if isinstance(_gj, dict) else 0
        _n = _n or len(state.get("raw_data") or [])
        msg = (
            f"La operación se completó ({_n} elementos)." if _n
            else "La operación se completó."
        )
        return {
            "current_agent": "insights_agent",
            "final_response": msg,
            "messages": [],
        }
