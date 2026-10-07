"""El estado y los pasos de UN turno del bucle ReAct (F4 del plan de calidad).

Antes era `agent_loop.run`: una función de 317 líneas (complejidad 80) con el turno entero en
variables locales. Aquí el estado del turno (mensajes, traza, breaker, herramientas usadas, hechos
para el juez, fallos repetidos) vive en `BucleReAct`, y cada decisión del paso es un método. El juez
composicional, que estaba duplicado (cierre en prosa y `answer`), es uno solo. Código movido tal
cual: sin cambio de comportamiento.

`agent_loop` sigue siendo el punto de entrada (`run`) y el de inyección de las pruebas
(`get_settings`, `dispatch_tool`): se resuelven desde ese módulo en cada uso.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from geo_copilot.core.agent_audit import DecisionTrace
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator import traza
from geo_copilot.orchestrator.circuit_breaker import CircuitBreaker
from geo_copilot.platform.capabilities import ANSWER_TOOL

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

#: el mismo logger que el nodo: los registros siguen saliendo con su nombre
logger = get_logger("geo_copilot.orchestrator.nodes.agent_loop")


def _al():
    """`agent_loop` importa este módulo; sus funciones (y lo que las pruebas sustituyen ahí) se
    resuelven al usarlas."""
    from geo_copilot.orchestrator.nodes import agent_loop

    return agent_loop


async def _prompt_del_sistema(graph: Any, state: Mapping[str, Any], metrics: Any) -> str:
    """El system prompt del turno: reglas + herramientas, capas del mapa, workspace, servicios MCP y
    el historial de costo de la sesión."""
    al = _al()
    al.ensure_core()
    # FRT-04: exponer al LLM del bucle las CAPAS EN EL MAPA (id + nombre + cuál es la activa) y la
    # convención de capa objetivo. Sin esto el bucle no conoce los ids y no puede dirigir una tool a
    # la capa NOMBRADA por el usuario — sólo operaría sobre la activa (bug destapado en validación en
    # vivo). Mismo map_context que ve el router_agent (format_map_context). S1.3: la lista de
    # herramientas sale del registro de capacidades, solo con las disponibles.
    system_content: str = al.react_system_prompt(graph)
    from geo_copilot.core.formatters import format_map_context
    _map_block = format_map_context(state.get("map_context"))
    if _map_block:
        system_content = f"{system_content}\n\n{_map_block}\n\n{al._TARGET_LAYER_RULE}"
    # S2.5: los datasets del workspace de la sesión (ids para las ws_*).
    from geo_copilot.orchestrator.capabilities_espaciales import bloque_workspace

    _ws_block = await bloque_workspace(state.get("session_id"), state.get("map_context"))
    if _ws_block:
        system_content = f"{system_content}\n\n{_ws_block}"
    # F3: qué servicios MCP hay conectados y en qué estado (el LLM elige).
    from geo_copilot.platform.mcp.hub import hub_actual

    _hub = hub_actual()
    _mcp_block = _hub.resumen_prompt() if _hub is not None else ""
    if _mcp_block:
        system_content = f"{system_content}\n\n{_mcp_block}"
    # F6: realimentar el historial de costo/fiabilidad de la sesión al prompt (cost-aware). Solo
    # aparece con suficiente historia (cost_hint lo decide). Se AÑADE a lo ya construido:
    # reconstruir desde REACT_SYSTEM_PROMPT borraba el bloque de capas y la regla de capa objetivo.
    if metrics is not None:
        hint = metrics.cost_hint()
        if hint:
            system_content = f"{system_content}\n\n{hint}"
    return system_content


def _pedido(state: Mapping[str, Any], query: str) -> str:
    """El mensaje del usuario con lo que otros pasos de este turno ya leyeron (V5 FH.4, FH.9, FH.14)."""
    pedido = query
    if state.get("intent") == "clarify" and state.get("final_response"):
        # FH.9: el router juzgó ambigua la consulta y propuso una pregunta; aquí se decide cómo
        # resolverlo viendo el mapa (sus hechos están arriba).
        pedido = (f"{query}\n\n(Un paso anterior de este turno juzgó que esta consulta es ambigua y propuso "
                  f"preguntar: «{state['final_response']}». Decide tú: si con lo que hay en el mapa ya no es "
                  "ambigua, resuélvela; si lo que falta es DÓNDE o SOBRE QUÉ del mapa, pídeselo con "
                  "request_map_input; si falta otra cosa, pregúntalo con answer.)")
    elif state.get("interpretacion_previa"):
        # V5 FH.4: si otro agente de este turno (el seguimiento) ya leyó la conversación y el mapa y
        # juzgó que hay que calcular algo, esa lectura llega como hecho, con su fuente.
        pedido = (f"{query}\n\n(Un paso anterior de este turno, que leyó esta misma conversación y el "
                  f"mapa, determinó que para responder hay que: {state['interpretacion_previa']})")
    mc_turno = state.get("map_context") or {}
    # V3 FH.14: el router ya leyó ESTE mensaje con el mapa («…el área total en hectáreas de los 12
    # lotes seleccionados») y esa lectura se tiraba; el bucle midió los 27 de la conversación.
    lectura = _al()._lectura_del_router(state)
    if lectura and state.get("intent") != "clarify" and not state.get("interpretacion_previa"):
        pedido += f"\n\n(El paso que clasificó este mensaje, leyendo el mismo mapa, lo entendió así: «{lectura}».)"
    respuesta = mc_turno.get("respuesta_mapa")
    if isinstance(respuesta, dict):
        # FH.9: este mensaje ES la respuesta a lo que pediste en el mapa (V5: con el punto ya
        # marcado, el bucle volvía a preguntar «¿cerca de qué lugar?» siguiendo al router).
        from geo_copilot.core.formatters import _respuesta_mapa

        pedido += f"\n\n({_respuesta_mapa(respuesta, mc_turno.get('layers') or [], mc_turno.get('clicked_point'))})"
    return pedido


@dataclass
class BucleReAct:
    graph: GeoAgentGraph
    state: GraphState
    settings: Any
    llm: Any
    query: str
    breaker: CircuitBreaker
    metrics: Any
    messages: list[LLMMessage]
    max_reflections: int
    trace: DecisionTrace = field(default_factory=DecisionTrace)
    working: dict = field(default_factory=dict)
    final_text: str | None = None
    step: int = 0
    tools_used: list[str] = field(default_factory=list)       # F5: herramientas usadas (para el juez)
    observaciones: list[str] = field(default_factory=list)    # lo que reportó cada una (hechos para el juez)
    fallidas: dict[str, int] = field(default_factory=dict)    # llamada exacta que falló → paso
    reflections: int = 0                                      # F5: re-trabajos por validación composicional

    @classmethod
    async def preparar(cls, graph: GeoAgentGraph, state: GraphState) -> BucleReAct:
        settings = _al().get_settings()
        query = state.get("query", "") or ""
        metrics = getattr(graph, "agent_metrics", None)
        messages = [
            LLMMessage(role="system", content=await _prompt_del_sistema(graph, state, metrics)),
            *_al()._conversacion_reciente(state.get("conversation_history"), query),
            LLMMessage(role="user", content=_pedido(state, query)),
        ]
        max_reflections = getattr(settings, "react_max_reflections", 0)
        if not isinstance(max_reflections, int):  # defensivo: setting ausente/no-int → off
            max_reflections = 0
        return cls(graph=graph, state=state, settings=settings, llm=getattr(graph, "llm", None), query=query,
                   breaker=CircuitBreaker.from_settings(settings), metrics=metrics, messages=messages,
                   max_reflections=max_reflections, working=dict(state))

    # ------------------------------------------------------------------ el juez (F5, R4.10)
    async def _falta_algo(self, candidate: str, *, en_prosa: bool) -> bool:
        """Validación composicional: ¿la respuesta cubre la consulta? Si no (con alta confianza) y queda
        margen, empuja al agente a seguir en vez de cerrar evasivo. Sesgo conservador: ante la duda,
        acepta. R4.10: la prosa pasa por el MISMO juez que la ruta `answer`."""
        if not (self.max_reflections and self.reflections < self.max_reflections and not self.breaker.tripped):
            return False
        from geo_copilot.core.composition_judge import judge_answer
        verdict = await judge_answer(query=self.query, answer=candidate, tools_used=self.tools_used,
                                     llm_client=self.llm, observaciones=self.observaciones)
        if not verdict.needs_more_work:
            return False
        self.reflections += 1
        gap = verdict.missing or verdict.reason
        self.trace.record_reflection(self.step, f"falta (prosa): {gap}" if en_prosa else f"falta: {gap}")
        self.messages.append(LLMMessage(role="assistant", content=f"Iba a responder: {_al()._short(candidate, 120)}"))
        self.messages.append(LLMMessage(role="user", content=(
            f"Esa respuesta aún no cubre: {gap}. Usa las herramientas disponibles para completarla; si de "
            "verdad no se puede, dilo honestamente con `answer`." if en_prosa else
            f"Esa respuesta aún no cubre: {gap}. Usa más herramientas para completarla; si de verdad no se "
            "puede, dilo honestamente con `answer`.")))
        self.step += 1
        return True

    async def _cerrar(self, candidate: str, *, en_prosa: bool) -> bool:
        """Cierre del turno (prosa o `answer`), salvo que el juez pida más trabajo. True = seguir."""
        if await self._falta_algo(candidate, en_prosa=en_prosa):
            return True
        self.final_text = candidate
        self.trace.record_final(self.step, self.final_text)
        return False

    # ------------------------------------------------------------------ el paso
    async def paso(self) -> bool:
        """Un paso del bucle: pensar → elegir herramienta → ejecutarla → observar. True = seguir."""
        session_id = self.state.get("session_id") or ""
        id_pensar = traza.nuevo_id("pensar")
        await traza.emitir(session_id, id=id_pensar, tipo="pensar", estado="en_curso",
                           titulo="Decidiendo el siguiente paso")
        t0 = time.monotonic()
        try:
            tool_schemas = _al()._esquemas_del_paso(self.graph, self.working)
            response = await self.llm.chat(self.messages, tools=tool_schemas)
        except Exception as exc:  # noqa: BLE001 — un fallo del LLM cierra honesto
            from geo_copilot.core.llm_client import mensaje_fallo_llm

            await traza.emitir(session_id, id=id_pensar, tipo="pensar", estado="fallo",
                               titulo="Decidiendo el siguiente paso", detalle="el modelo no respondió",
                               ms=int((time.monotonic() - t0) * 1000))
            logger.error(f"[agent_loop] LLM falló: {exc}")
            self.trace.record_error(self.step, f"LLM error: {exc}")
            self.final_text = (mensaje_fallo_llm(exc)  # saturación o cuota agotada: cada una con su mensaje
                               or "No pude completar la solicitud por un error del modelo.")
            return False
        elegida = (((response.tool_calls or [{}])[0] or {}).get("function") or {}).get("name") if response.tool_calls else None
        await traza.emitir(session_id, id=id_pensar, tipo="pensar", estado="ok", titulo="Decidiendo el siguiente paso",
                           detalle=("eligió responder" if not elegida or elegida == ANSWER_TOOL
                                    else f"eligió {traza.describir_herramienta(elegida)[0]}"),
                           ms=int((time.monotonic() - t0) * 1000))
        usage = response.usage or {}
        _tok = usage.get("total_tokens")
        self.breaker.record_tokens(_tok if isinstance(_tok, int) else 0)
        # Sin tool_calls → el contenido es la respuesta final.
        if not response.tool_calls:
            return await self._cerrar((response.content or "").strip(), en_prosa=True)
        # Una herramienta por paso: se observa su resultado antes de decidir la siguiente (el cliente
        # pide al proveedor que no reparta el paso en llamadas paralelas). Si aun así llegan varias,
        # se ejecuta la primera y se le DICE al modelo cuáles no (antes se perdían en silencio).
        first = response.tool_calls[0]
        call = first.get("function") or {} if isinstance(first, dict) else {}
        name = (call.get("name") or "") if isinstance(call, dict) else ""
        if not name:  # tool_call malformado → no malgastes presupuesto: cierra honesto.
            self.final_text = "El modelo devolvió una acción malformada; no pude continuar."
            self.trace.record_error(self.step, "tool_call malformado")
            return False
        call_id = first.get("id") or f"call_{self.step}"
        try:
            args = json.loads(call.get("arguments") or "{}")
            args_ok = isinstance(args, dict)
        except (ValueError, TypeError):
            args_ok = False
        if not args_ok:
            return self._args_invalidos(response, name, call_id)
        # ``answer`` es la herramienta TERMINAL: cierra el bucle. No cuenta como tool de trabajo (no
        # consume el presupuesto del breaker) ni se registra como tool_call: es la decisión final.
        if name == ANSWER_TOOL:
            return await self._cerrar(str(args.get("text") or "").strip(), en_prosa=False)
        omitidas = [str(((tc.get("function") or {}) if isinstance(tc, dict) else {}).get("name") or "?")
                    for tc in response.tool_calls[1:]]
        return await self._ejecutar(response, call, call_id, name, args, omitidas)

    def _args_invalidos(self, response: Any, name: str, call_id: str) -> bool:
        """Antes: args = {} y la herramienta corría SIN argumentos (con el tope de salida agotado, la
        llamada llega truncada). Es un hecho que el modelo puede corregir."""
        motivo = ("se agotó el tope de salida y la llamada llegó truncada"
                  if getattr(response, "stop_reason", None) == "length" else "no son un objeto JSON válido")
        logger.warning(f"[agent_loop] paso {self.step}: argumentos de {name} inválidos ({motivo})")
        self.trace.record_error(self.step, f"argumentos inválidos para {name}: {motivo}")
        self.breaker.record_tool_call()
        self.messages.append(LLMMessage(role="assistant", content=response.content or "",
                                        tool_calls=[{"id": call_id, "function": {"name": name, "arguments": "{}"}}]))
        self.messages.append(LLMMessage(role="tool", tool_call_id=call_id, name=name, content=(
            f"No se ejecutó {name}: sus argumentos {motivo}. Vuelve a llamarla con argumentos JSON "
            "completos (más cortos si hace falta).")))
        self.step += 1
        return True

    async def _ejecutar(self, response: Any, call: dict, call_id: str, name: str, args: dict,
                        omitidas: list[str]) -> bool:
        """Ejecuta la herramienta. Un fallo del nodo NO tumba el bucle: es una observación de error que
        el LLM ve, y el breaker sigue contando para llegar al cierre honesto (revisión F4.4)."""
        self.trace.record_tool_call(self.step, name, args)
        # Qué decidió el modelo en cada paso: sin esto, un turno que se desvía (V5 F4: llamó a NDVI por
        # feature cuando se pidió colorear por área) solo se reconstruía adivinando desde los logs.
        logger.info(f"[agent_loop] paso {self.step}: {name} {_al()._short(json.dumps(args, ensure_ascii=False), 300)}")
        self.breaker.record_tool_call()
        self.tools_used.append(name)
        session_id = self.state.get("session_id") or ""
        await _al()._anunciar(session_id, name, "started")
        titulo, que_hace = traza.describir_herramienta(name)
        id_tool, t0 = traza.nuevo_id("tool"), time.monotonic()
        await traza.emitir(session_id, id=id_tool, tipo="herramienta", estado="en_curso", titulo=titulo,
                           detalle=que_hace, herramienta=name, argumentos=traza.resumen_argumentos(args))
        try:
            outcome = await _al().dispatch_tool(self.graph, self.working, name, args)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[agent_loop] dispatch '{name}' falló: {exc}")
            from geo_copilot.orchestrator.react_tools import ToolOutcome
            outcome = ToolOutcome(observation=f"la herramienta {name} falló: {str(exc)[:200]}", success=False)
        await traza.emitir(session_id, id=id_tool, tipo="herramienta", estado="ok" if outcome.success else "fallo",
                           titulo=titulo, herramienta=name, argumentos=traza.resumen_argumentos(args),
                           detalle=traza.resumen_resultado(outcome),
                           ms=int((time.monotonic() - t0) * 1000))
        if outcome.is_final:  # p. ej. request_map_input: cierra el turno CON su orden al mapa
            self.working.update(outcome.delta)
            self.final_text = (outcome.final_text or "").strip()
            self.trace.record_final(self.step, self.final_text)
            return False
        await _al()._anunciar(session_id, name, "completed" if outcome.success else "failed")
        outcome = self._acumular(outcome, name, args)
        self._responder_al_modelo(response, call, call_id, name, outcome.observation, omitidas)
        return True

    def _acumular(self, outcome: Any, name: str, args: dict) -> Any:
        """Lo que el resultado deja en el turno (rasters, analíticos, fallos repetidos) y en la traza."""
        working = self.working
        # FH.10: cada raster del turno se entrega (el NDVI de marzo y el de junio para compararlos);
        # antes el segundo pisaba al primero.
        if outcome.delta.get("external_imagery") and working.get("external_imagery"):
            working["imagery_previas"] = [*(working.get("imagery_previas") or []), working["external_imagery"]]
        working.update(outcome.delta)
        # FH.10: con 2+ rasters CON FECHA en el turno, el hecho de que existen y cómo se ven lado a
        # lado (V5: una pista en CADA raster llevó a comparar un mapa de cambio con unos edificios)
        con_fecha = [i for i in [*(working.get("imagery_previas") or []), working.get("external_imagery") or {}]
                     if i.get("time")] if outcome.delta.get("external_imagery") else []
        if len(con_fecha) >= 2:
            outcome = dataclasses.replace(outcome, observation=outcome.observation + (
                " | En este turno hay rasters de varias fechas: " + ", ".join(f"«{i.get('name')}» ({i['time']})" for i in con_fecha)
                + ". Para verlos lado a lado: map_command compare {left, right} con sus nombres."))
        # Cada resultado analítico se ACUMULA: el siguiente análisis pisa data/visualization,
        # pero lo que el agente ya generó se entrega (V5 F4: gráfico + tabla → solo tabla).
        viz, dat = outcome.delta.get("visualization"), outcome.delta.get("data")
        if isinstance(viz, dict) and isinstance(dat, dict) and dat.get("results"):
            # lo que acompaña al gráfico va ANTES: el último de la lista es el que viaja en `data`
            extras = [a for a in (outcome.delta.get("analiticos_extra") or [])
                      if isinstance(a, dict) and ((a.get("data") or {}).get("results"))]
            working["analiticos"] = [*(working.get("analiticos") or []), *extras,
                                     {"data": dat, "visualization": viz}]
        # V5 F5: el bucle repitió 7 veces la MISMA llamada que el servidor rechazaba con el mismo
        # motivo. Que ya falló así es un hecho del turno: va en la observación (el LLM decide).
        firma = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if not outcome.success:
            if firma in self.fallidas:
                outcome = dataclasses.replace(outcome, observation=outcome.observation + (
                    f" | Es la MISMA llamada que ya falló en el paso {self.fallidas[firma]} con este resultado: "
                    "repetirla no lo cambia. Cambia la llamada (otros argumentos u otra herramienta) o "
                    "responde con lo que sabes."))
            else:
                self.fallidas[firma] = self.step
        # El resultado (éxito o el fallo capturado) queda en la traza.
        self.trace.record_result(self.step, name, success=outcome.success, result=outcome.observation)
        self.observaciones.append(f"{name}: {_al()._short(outcome.observation, 400)}")
        logger.info(f"[agent_loop] paso {self.step} ← {'ok' if outcome.success else 'FALLO'}: "
                    f"{_al()._short(outcome.observation, 300)}")
        if self.metrics is not None:  # F6: fiabilidad por herramienta
            self.metrics.record_tool(name, outcome.success)
        return outcome

    def _responder_al_modelo(self, response: Any, call: dict, call_id: str, name: str, observacion: str,
                             omitidas: list[str]) -> None:
        """Round-trip NATIVO de tool (en vez del scratchpad de texto): el turno assistant lleva el
        tool_call y el turno role=tool la observación, con el MISMO id."""
        if omitidas:
            observacion += (f" | Pediste varias herramientas a la vez; solo se ejecutó {name}. NO se ejecutaron: "
                            f"{', '.join(omitidas)}. Si aún hacen falta, pídelas en los pasos siguientes.")
        self.messages.append(LLMMessage(
            role="assistant", content=response.content or "",
            tool_calls=[{"id": call_id, "function": call}],
            provider_blocks=_al()._bloques_de(response, call_id)))
        self.messages.append(LLMMessage(role="tool", tool_call_id=call_id, name=name, content=observacion))
        self.step += 1

    # ------------------------------------------------------------------ el cierre
    def cierre(self) -> dict:
        """El update del turno; si el bucle salió por el circuit-breaker sin `answer`, lo dice."""
        if self.final_text is None:
            reason = self.breaker.reason or "límite del bucle alcanzado"
            logger.warning(f"[agent_loop] circuit-breaker: {reason}")
            self.trace.record_error(self.step, reason)
            self.final_text = (
                f"Alcancé el límite del razonamiento ({reason}) tras {self.step} paso(s) "
                f"sin completar la solicitud. Intenta acotarla o divídela."
            )
        # F6: registrar el turno (cost-aware). Éxito = no terminó en error.
        turn_ok = not (self.trace.decisions and self.trace.decisions[-1].kind == "error")
        if self.metrics is not None:
            self.metrics.record_turn(success=turn_ok, tokens=self.breaker.tokens_used,
                                     n_tools=len(self.tools_used), reflections=self.reflections)
        update: dict = {"current_agent": "agent_loop", "final_response": self.final_text,
                        "decision_trace": self.trace.to_list(), "messages": []}
        for k in _al()._OUTPUT_KEYS:
            if k in self.working and self.working[k] is not None:
                update[k] = self.working[k]
        return update
