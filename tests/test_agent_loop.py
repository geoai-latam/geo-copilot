"""F4.4: bucle ReAct (agent_loop) — pasa tools= y actúa sobre tool_calls.

VALIDACIÓN CRÍTICA: el bucle elige herramientas dinámicamente, encadena
observaciones, TERMINA siempre (por `answer` o por circuit-breaker), audita cada
decisión, y nunca entra en bucle infinito. Probado de forma DETERMINISTA con
ScriptedLLM (F4.T1) + nodos hoja mockeados (el dispatch real corre).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.core.llm_client import LLMResponse
from geo_copilot.core.scripted_llm import (
    ScriptedLLM,
    final_response,
    tool_call_response,
)
from geo_copilot.orchestrator.nodes import agent_loop


def _graph(script):
    g = MagicMock()
    g.llm = ScriptedLLM(script)
    return g


def _settings(max_calls=8, max_reflections=0):
    return SimpleNamespace(
        react_max_tool_calls=max_calls, react_token_budget=0,
        react_max_reflections=max_reflections,
    )


def _kinds(trace):
    return [d["kind"] for d in trace]


# ---------------------------------------------------------------------------
# Camino feliz: query_database → answer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_query_then_answer():
    graph = _graph([
        tool_call_response("query_database", {"request": "trae los lotes"}),
        tool_call_response("answer", {"text": "Hay 12 lotes."}, call_id="c2"),
    ])
    gj = {"type": "FeatureCollection", "features": [{"id": i} for i in range(12)]}

    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"geojson": gj, "raw_data": [{}] * 12})), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "cuántos lotes hay", "session_id": "s"})

    assert result["final_response"] == "Hay 12 lotes."
    assert len(result["geojson"]["features"]) == 12   # dato producido por la tool
    assert _kinds(result["decision_trace"]) == ["tool_call", "tool_result", "final"]


# ---------------------------------------------------------------------------
# Encadenamiento: query → spatial_operation → answer (la 2da opera sobre la 1ra)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chains_tools_and_threads_state():
    graph = _graph([
        tool_call_response("query_database", {"request": "trae lotes"}),
        tool_call_response("spatial_operation", {"operation": "buffer", "request": "500 m"}, call_id="c2"),
        tool_call_response("answer", {"text": "Buffer aplicado."}, call_id="c3"),
    ])
    gj1 = {"type": "FeatureCollection", "features": [{"id": 1}]}
    gj2 = {"type": "FeatureCollection", "features": [{"id": 1, "buffered": True}]}

    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"geojson": gj1})), \
         patch("geo_copilot.orchestrator.nodes.python_agent.run",
               new=AsyncMock(return_value={"geojson": gj2})) as py_run, \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "trae lotes y buffer 500"})

    # python_agent vio el geojson cargado por query_database (estado encadenado).
    threaded_state = py_run.call_args[0][1]
    assert threaded_state["geojson"] == gj1
    assert result["geojson"] == gj2  # salida final = la del último paso
    assert _kinds(result["decision_trace"]) == [
        "tool_call", "tool_result", "tool_call", "tool_result", "final",
    ]


# ---------------------------------------------------------------------------
# Respuesta directa: el LLM no llama ninguna herramienta
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_direct_answer_without_tools():
    graph = _graph([final_response("¡Hola! ¿En qué te ayudo?")])
    with patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "hola"})
    assert result["final_response"] == "¡Hola! ¿En qué te ayudo?"
    assert _kinds(result["decision_trace"]) == ["final"]


# ---------------------------------------------------------------------------
# Circuit-breaker: un LLM que insiste en herramientas se corta (no bucle infinito)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_circuit_breaker_stops_runaway_loop():
    graph = _graph([tool_call_response("query_database", {"request": "x"}, call_id=f"c{i}")
                    for i in range(10)])  # nunca llama answer
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": []})), \
         patch.object(agent_loop, "get_settings", return_value=_settings(max_calls=3)):
        result = await agent_loop.run(graph, {"query": "loop"})

    assert "límite" in result["final_response"].lower()
    assert graph.llm.call_count == 3        # exactamente max_tool_calls iteraciones
    assert result["decision_trace"][-1]["kind"] == "error"  # cierre por breaker


# ---------------------------------------------------------------------------
# Robustez: herramienta desconocida y JSON inválido no crashean
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_tool_is_handled_gracefully():
    graph = _graph([
        tool_call_response("teleport", {"to": "marte"}),
        tool_call_response("answer", {"text": "No tengo esa capacidad."}, call_id="c2"),
    ])
    with patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "teletranspórtame"})
    assert result["final_response"] == "No tengo esa capacidad."
    # la decisión de tool desconocida quedó registrada como no exitosa
    teleport_result = [d for d in result["decision_trace"]
                       if d["kind"] == "tool_result" and d.get("tool") == "teleport"]
    assert teleport_result and teleport_result[0]["success"] is False


@pytest.mark.asyncio
async def test_invalid_json_arguments_do_not_crash():
    bad = LLMResponse(content="", model="x",
                      tool_calls=[{"id": "c1", "function": {"name": "query_database",
                                                            "arguments": "{not json"}}])
    graph = _graph([bad, tool_call_response("answer", {"text": "ok"}, call_id="c2")])
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": []})), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "x"})
    assert result["final_response"] == "ok"  # args inválidos → {} → no crashea


@pytest.mark.asyncio
async def test_llm_error_closes_honestly():
    graph = MagicMock()
    graph.llm = MagicMock()
    graph.llm.chat = AsyncMock(side_effect=RuntimeError("upstream down"))
    with patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "x"})
    assert "error del modelo" in result["final_response"].lower()
    assert result["decision_trace"][-1]["kind"] == "error"


# ---------------------------------------------------------------------------
# Revisión adversarial F4.4 — fixes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_exception_does_not_crash_loop():
    # Un nodo que LANZA (BD caída) no debe tumbar el bucle: se convierte en
    # observación de error y el LLM puede responder honesto.
    graph = _graph([
        tool_call_response("query_database", {"request": "trae lotes"}),
        tool_call_response("answer", {"text": "No pude consultar la BD."}, call_id="c2"),
    ])
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(side_effect=ConnectionError("DB down"))), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "cuántos lotes"})
    assert result["final_response"] == "No pude consultar la BD."
    qr = [d for d in result["decision_trace"]
          if d["kind"] == "tool_result" and d.get("tool") == "query_database"]
    assert qr and qr[0]["success"] is False  # el fallo del nodo quedó registrado


@pytest.mark.asyncio
async def test_malformed_tool_call_closes_honestly():
    bad = LLMResponse(content="", model="x", tool_calls=[{"id": "c1", "function": None}])
    graph = _graph([bad])
    with patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "x"})
    assert "malformada" in result["final_response"].lower()
    assert graph.llm.call_count == 1  # NO malgastó presupuesto reintentando


@pytest.mark.asyncio
async def test_query_database_clears_prior_stale_geojson():
    # query_database GENERA datos frescos: el geojson de un paso previo se limpia,
    # no se arrastra al output final (clase de bug de F3.1).
    graph = _graph([
        tool_call_response("query_database", {"request": "trae lotes"}),
        tool_call_response("answer", {"text": "ok"}, call_id="c2"),
    ])
    # El nodo devuelve raw_data pero NINGÚN geojson (consulta agregada).
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": [{"count": 5}]})), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(
            graph,
            {"query": "cuántos lotes", "geojson": {"features": [{"stale": True}]}},
        )
    # El geojson obsoleto del estado inicial NO aparece en el output.
    assert "geojson" not in result


@pytest.mark.asyncio
async def test_select_service_resolves_url_from_found_services():
    from geo_copilot.orchestrator.react_tools import dispatch_tool

    graph = MagicMock()
    loaded = {"external_geojson": {"type": "FeatureCollection", "features": [{"id": 1}]}}
    with patch("geo_copilot.orchestrator.nodes.data_agent.run",
               new=AsyncMock(return_value=loaded)) as data_run:
        ws = {"found_services": [{"name": "A", "url": "https://x/1"},
                                 {"name": "B", "url": "https://x/2"}]}
        out = await dispatch_tool(graph, ws, "select_service", {"number": 2})
    # Resolvió la URL del servicio #2 y cargó vía load_external.
    assert data_run.call_args[0][1]["external_url"] == "https://x/2"
    assert out.success and out.delta.get("external_geojson")


@pytest.mark.asyncio
async def test_select_service_out_of_range_is_honest_error():
    from geo_copilot.orchestrator.react_tools import dispatch_tool

    ws = {"found_services": [{"name": "A", "url": "u"}]}
    out = await dispatch_tool(MagicMock(), ws, "select_service", {"number": 9})
    assert out.success is False and "rango" in out.observation


@pytest.mark.asyncio
async def test_select_service_without_prior_search_is_honest():
    from geo_copilot.orchestrator.react_tools import dispatch_tool

    out = await dispatch_tool(MagicMock(), {}, "select_service", {"number": 1})
    assert out.success is False and "search_external" in out.observation


@pytest.mark.asyncio
async def test_non_integer_token_usage_does_not_crash():
    bad_usage = LLMResponse(content="hola", model="x", usage={"total_tokens": "NaN"})
    graph = MagicMock()
    graph.llm = MagicMock()
    graph.llm.chat = AsyncMock(return_value=bad_usage)
    with patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "hola"})
    assert result["final_response"] == "hola"  # usage no-int → ignorado, sin crash


# ---------------------------------------------------------------------------
# F5 — validación composicional (reflexión sobre la respuesta)
# ---------------------------------------------------------------------------
from geo_copilot.core.composition_judge import AnswerVerdict


@pytest.mark.asyncio
async def test_reflection_off_by_default_terminates_immediately():
    graph = _graph([tool_call_response("answer", {"text": "ya"})])
    # react_max_reflections=0 → el juez NUNCA se llama.
    judge = AsyncMock()
    with patch.object(agent_loop, "get_settings", return_value=_settings(max_reflections=0)), \
         patch("geo_copilot.core.composition_judge.judge_answer", judge):
        result = await agent_loop.run(graph, {"query": "x"})
    assert result["final_response"] == "ya"
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_reflection_pushes_agent_to_keep_working():
    # 1er answer no cubre → reflexión → el agente usa otra tool → 2do answer acepta.
    graph = _graph([
        tool_call_response("answer", {"text": "solo escuelas"}),
        tool_call_response("query_database", {"request": "hospitales"}, call_id="c2"),
        tool_call_response("answer", {"text": "escuelas y hospitales"}, call_id="c3"),
    ])
    judge = AsyncMock(return_value=AnswerVerdict(False, 0.9, "incompleta", "los hospitales"))
    with patch.object(agent_loop, "get_settings", return_value=_settings(max_reflections=1)), \
         patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": [{}]})), \
         patch("geo_copilot.core.composition_judge.judge_answer", judge):
        result = await agent_loop.run(graph, {"query": "escuelas y hospitales"})
    assert result["final_response"] == "escuelas y hospitales"
    kinds = [d["kind"] for d in result["decision_trace"]]
    assert "reflection" in kinds  # se registró la reflexión
    assert judge.await_count == 1  # solo se re-validó una vez (acotado)


@pytest.mark.asyncio
async def test_reflection_is_bounded_no_infinite_loop():
    # El juez SIEMPRE dice "no cubre"; con max_reflections=1 el bucle igual
    # termina (acepta el 2do answer sin re-validar).
    graph = _graph([
        tool_call_response("answer", {"text": "v1"}),
        tool_call_response("answer", {"text": "v2"}, call_id="c2"),
    ])
    judge = AsyncMock(return_value=AnswerVerdict(False, 0.95, "nunca cubre", "todo"))
    with patch.object(agent_loop, "get_settings", return_value=_settings(max_reflections=1)), \
         patch("geo_copilot.core.composition_judge.judge_answer", judge):
        result = await agent_loop.run(graph, {"query": "x"})
    assert result["final_response"] == "v2"   # aceptó tras agotar la reflexión
    assert judge.await_count == 1             # no se re-validó indefinidamente


@pytest.mark.asyncio
async def test_reflection_accepts_when_addressed():
    graph = _graph([tool_call_response("answer", {"text": "completo"})])
    judge = AsyncMock(return_value=AnswerVerdict(True, 0.9, "cubre"))
    with patch.object(agent_loop, "get_settings", return_value=_settings(max_reflections=2)), \
         patch("geo_copilot.core.composition_judge.judge_answer", judge):
        result = await agent_loop.run(graph, {"query": "x"})
    assert result["final_response"] == "completo"
    assert judge.await_count == 1  # validó una vez, aceptó


# ---------------------------------------------------------------------------
# T1.4 (#18): el bucle ReAct emite su progreso por el EventSink
# ---------------------------------------------------------------------------
class _Pasos:
    def __init__(self):
        self.pasos: list[tuple[str, str, str]] = []

    async def agent_step(self, session_id, agent, description, status):
        self.pasos.append((agent, description, status))


@pytest.mark.asyncio
async def test_cada_herramienta_se_anuncia_como_paso(monkeypatch):
    from geo_copilot.platform import events

    grabador = _Pasos()
    monkeypatch.setattr(events, "_sink", grabador)
    graph = _graph([
        tool_call_response("query_database", {"request": "trae lotes"}),
        tool_call_response("apply_symbology", {"request": "rojo"}, call_id="c2"),
        tool_call_response("answer", {"text": "Listo."}, call_id="c3"),
    ])
    gj = {"type": "FeatureCollection", "features": [{"id": 1}]}
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"geojson": gj})), \
         patch("geo_copilot.orchestrator.nodes.symbology.run",
               new=AsyncMock(return_value={"symbology": {"symbology_type": "single_symbol"}})), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        await agent_loop.run(graph, {"query": "trae lotes en rojo", "session_id": "s1"})

    assert [(a, s) for a, _, s in grabador.pasos] == [
        ("gis_agent", "started"), ("gis_agent", "completed"),
        ("symbology_agent", "started"), ("symbology_agent", "completed"),
    ]


@pytest.mark.asyncio
async def test_sin_sesion_no_se_emite_nada(monkeypatch):
    from geo_copilot.platform import events

    grabador = _Pasos()
    monkeypatch.setattr(events, "_sink", grabador)
    graph = _graph([
        tool_call_response("query_database", {"request": "x"}),
        tool_call_response("answer", {"text": "ok"}, call_id="c2"),
    ])
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": [{}]})), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        await agent_loop.run(graph, {"query": "x"})  # el bench corre sin session_id
    assert grabador.pasos == []


@pytest.mark.asyncio
async def test_un_sink_roto_no_tumba_el_bucle(monkeypatch):
    from geo_copilot.platform import events

    class _Roto(_Pasos):
        async def agent_step(self, *a, **k):
            raise RuntimeError("WS caído")

    monkeypatch.setattr(events, "_sink", _Roto())
    graph = _graph([
        tool_call_response("query_database", {"request": "x"}),
        tool_call_response("answer", {"text": "Hay 1."}, call_id="c2"),
    ])
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"raw_data": [{}]})), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "x", "session_id": "s"})
    assert result["final_response"] == "Hay 1."


# ---------------------------------------------------------------------------
# H8: la observación de un análisis lleva los números (el LLM del bucle es quien
# redacta la respuesta final; sin ellos decía "las estadísticas están listas").
# ---------------------------------------------------------------------------
def test_la_observacion_de_un_analisis_trae_los_datos():
    from geo_copilot.orchestrator.react_tools import _observe

    delta = {
        "visualization": {"type": "histogram", "stats": {"media": 412.7, "mediana": 388.0}},
        "data": {"results": [{"rango": f"{i * 100}-{(i + 1) * 100}", "n": 10 - i}
                             for i in range(12)]},
    }
    obs, ok = _observe("analyze_layer", delta)
    assert ok
    assert "412.7" in obs and "388.0" in obs          # estadísticas
    assert '"n": 10' in obs                           # primeras filas
    assert "filas_totales" in obs and '"n": 1,' not in obs  # acotado a 8 filas


def test_la_observacion_no_crece_sin_limite():
    from geo_copilot.orchestrator.react_tools import _observe

    delta = {"visualization": {"type": "table"},
             "data": {"results": [{"texto": "x" * 500} for _ in range(20)]}}
    obs, _ = _observe("analyze_layer", delta)
    assert len(obs) < 1200


def test_el_prompt_react_trae_la_fecha_de_hoy():
    """Sin la fecha, el LLM ampliaba rangos de imagery hacia su época de
    entrenamiento (2023-2024) y presentaba imágenes viejas como actuales."""
    from datetime import date

    p = agent_loop.react_system_prompt(MagicMock(), hoy=date(2026, 9, 24))
    assert "Hoy es 2026-09-24" in p
    assert "DESDE HOY" in p


@pytest.mark.asyncio
async def test_los_resultados_analiticos_del_turno_se_acumulan():
    """V5 F4: gráfico (paso 1) y luego tabla (paso 2): ambos salen del bucle."""
    graph = _graph([
        tool_call_response("query_database", {"request": "trae lotes"}),
        tool_call_response("analyze_layer", {"request": "gráfico del área"}, call_id="c2"),
        tool_call_response("analyze_layer", {"request": "tabla de clases"}, call_id="c3"),
        tool_call_response("answer", {"text": "Listo."}, call_id="c4"),
    ])
    gj = {"type": "FeatureCollection", "features": [{"id": 1}]}
    grafico = {"data": {"results": [{"lote": "1", "area": 5.0}]},
               "visualization": {"type": "chart", "chart_type": "bar", "x_axis": "lote", "y_axis": "area"}}
    tabla = {"data": {"results": [{"lote": "1", "clase": 2}]}, "visualization": {"type": "table"}}

    with patch("geo_copilot.orchestrator.nodes.gis_agent.run", new=AsyncMock(return_value={"geojson": gj})), \
         patch("geo_copilot.orchestrator.nodes.python_agent.run", new=AsyncMock(side_effect=[grafico, tabla])), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "lotes, gráfico y tabla", "session_id": "s"})

    assert [a["visualization"]["type"] for a in result["analiticos"]] == ["chart", "table"]
    assert result["visualization"]["type"] == "table"   # el último sigue en su sitio
    assert result["geojson"] == gj                       # y la capa no se pierde


@pytest.mark.asyncio
async def test_repetir_una_llamada_que_ya_fallo_se_le_dice_al_modelo():
    """V5 F5: repitió 7 veces la misma consulta que el servidor rechazaba. La segunda vez la
    observación dice que es la MISMA llamada que ya falló (un hecho; el modelo decide)."""
    graph = _graph([
        tool_call_response("teleport", {"to": "marte"}),
        tool_call_response("teleport", {"to": "marte"}, call_id="c2"),
        tool_call_response("teleport", {"to": "venus"}, call_id="c3"),
        tool_call_response("answer", {"text": "No puedo."}, call_id="c4"),
    ])
    with patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "teletranspórtame"})
    obs = [str(d.get("detail") or "") for d in result["decision_trace"] if d["kind"] == "tool_result"]
    assert "MISMA llamada que ya falló en el paso" not in obs[0]
    assert "MISMA llamada que ya falló en el paso 0" in obs[1]
    assert "MISMA llamada" not in obs[2]  # otros argumentos: otra llamada


@pytest.mark.asyncio
@pytest.mark.parametrize("resultado, nombre_esperado", [
    ({"data": {"results": [{"cantidad_lotes": 3}]}, "visualization": {"type": "table"}}, "Sedes educativas Soacha"),
    ({"geojson": {"type": "FeatureCollection", "features": [{"id": 1}]}}, "cuenta los lotes a menos de 200 m"),
])
async def test_un_conteo_no_renombra_la_capa_activa(resultado, nombre_esperado):
    """V3 F5: tras un COUNT (sin geometría) la capa activa —las 41 sedes del turno anterior— quedaba
    renombrada con el texto del conteo; el LLM la cruzó con las sedes y dijo 129 lotes donde había 3.
    Solo un resultado CON geometría pasa a ser la fuente activa con el nombre de lo pedido."""
    graph = _graph([
        tool_call_response("query_database", {"request": "cuenta los lotes a menos de 200 m"}),
        tool_call_response("spatial_operation", {"operation": "buffer", "request": "10 m"}, call_id="c2"),
        tool_call_response("answer", {"text": "Hay 3."}, call_id="c3"),
    ])
    sedes = {"type": "FeatureCollection", "features": [{"id": i} for i in range(41)]}
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run", new=AsyncMock(return_value=resultado)), \
         patch("geo_copilot.orchestrator.nodes.python_agent.run",
               new=AsyncMock(return_value={"geojson": sedes})) as py_run, \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        await agent_loop.run(graph, {"query": "¿cuántos lotes…?", "previous_geojson": sedes,
                                     "active_source_name": "Sedes educativas Soacha"})
    # lo que ve la SIGUIENTE herramienta: con qué nombre queda la capa activa
    assert py_run.call_args[0][1].get("active_source_name") == nombre_esperado


@pytest.mark.asyncio
async def test_la_tabla_que_acompana_a_un_grafico_llega_al_modelo_y_al_usuario():
    """V5 (auditoría F4): «una tabla con el número de lotes Y el área total por clase» — el código
    devolvió gráfico + tabla; `data` llevaba solo los puntos del gráfico (el conteo) y el agente
    dijo que el área total «no vino». La tabla completa va en la observación y en `analiticos`."""
    from geo_copilot.orchestrator.nodes.python_intento import _canal_analitico

    tabla = [{"lotdispers": "N", "contar_lotes": 30, "area_total_m2": 6321.0}]
    datos = {"chart": {"chart_type": "bar", "x": "lotdispers", "y": "contar_lotes",
                       "data": [{"lotdispers": "N", "contar_lotes": 30}]}, "table": tabla}
    payload: dict = {"messages": [{"content": "x"}]}
    _canal_analitico(payload, datos, "análisis listo")
    assert payload["visualization"]["type"] == "chart"
    assert payload["analiticos_extra"] == [{"data": {"results": tabla}, "visualization": {"type": "table"}}]
    assert [a["visualization"]["type"] for a in payload["analiticos"]] == ["table", "chart"]  # grafo clásico

    graph = _graph([
        tool_call_response("analyze_layer", {"request": "tabla por clase"}),
        tool_call_response("answer", {"text": "Listo."}, call_id="c2"),
    ])
    salida = {"current_agent": "python_agent", **{k: payload[k] for k in ("visualization", "data", "analiticos_extra")}}
    with patch("geo_copilot.orchestrator.nodes.python_agent.run", new=AsyncMock(return_value=salida)), \
         patch.object(agent_loop, "get_settings", return_value=_settings()):
        result = await agent_loop.run(graph, {"query": "tabla por clase", "session_id": "s",
                                              "geojson": {"type": "FeatureCollection", "features": [{"id": 1}]}})
    from geo_copilot.orchestrator.react_tools import _observe

    obs, ok = _observe("analyze", salida)  # lo que lee el modelo
    assert ok and "area_total_m2" in obs and "6321" in obs
    assert [a["visualization"]["type"] for a in result["analiticos"]] == ["table", "chart"]
    assert result["visualization"]["type"] == "chart"   # el último sigue en `data`

    from geo_copilot.platform.artefactos import construir_artefactos

    arts = construir_artefactos({**result, "intent": "analyze"}, layer_ref=None, tiles=None,
                                target_layer_id=None, row_count=1)
    tablas = [a for a in arts if a.kind == "table"]
    assert any("area_total_m2" in (t.preview[0] if t.preview else {}) for t in tablas)
