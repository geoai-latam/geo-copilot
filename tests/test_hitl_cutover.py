"""Cutover HITL: el nodo gis_agent REAL pide aprobación vía LangGraph interrupt.

VALIDACIÓN CRÍTICA: con hitl_mode='interrupt' el grafo PAUSA en el SQL real,
y al reanudar (approve/reject/modify) NO regenera el SQL (idempotencia) — se
aprueba/ejecuta exactamente lo que el usuario vio. El guardrail se preserva:
reject NO ejecuta, modify ejecuta el SQL modificado.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from typing_extensions import TypedDict

from geo_copilot.orchestrator.nodes import gis_agent as gis_node


class _S(TypedDict, total=False):
    query: str
    session_id: str
    sql: str
    raw_data: list
    geojson: dict
    error: str
    current_agent: str
    retry_count: int
    messages: list


def _settings():
    return SimpleNamespace(
        hitl_enabled=True, hitl_mode="interrupt",
        autonomous_mode=False, max_retries=0,
    )


def _mock_graph(execute=None):
    g = MagicMock()
    g.db_pool = object()
    g.hitl_manager = MagicMock()
    g._pending_sql = {}
    g.gis_agent.get_db_schema = AsyncMock(return_value="t(a int, geom)")
    g.gis_agent.generate_sql_from_query = AsyncMock(return_value="SELECT * FROM t")
    g.gis_agent._execute_sql = AsyncMock(
        return_value=execute or ([{"a": 1}], {"type": "FeatureCollection", "features": [{"a": 1}]}))
    return g


def _build_app(mock_graph):
    async def node(state):
        with patch.object(gis_node, "get_settings", return_value=_settings()), \
             patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")):
            return await gis_node.run(mock_graph, state)

    g = StateGraph(_S)
    g.add_node("gis", node)
    g.add_edge(START, "gis")
    g.add_edge("gis", END)
    return g.compile(checkpointer=MemorySaver())


_CFG = {"configurable": {"thread_id": "t1"}}


@pytest.mark.asyncio
async def test_interrupt_pauses_at_real_sql_then_approves_idempotent():
    mg = _mock_graph()
    app = _build_app(mg)
    paused = await app.ainvoke({"query": "trae t", "session_id": "s1"}, _CFG)

    assert "__interrupt__" in paused  # pausó pidiendo aprobación del SQL
    # P0-A: el gis node capa el LIMIT del SQL antes del HITL → puede traer LIMIT añadido.
    assert paused["__interrupt__"][0].value["details"]["sql"].startswith("SELECT * FROM t")
    assert mg.gis_agent.generate_sql_from_query.await_count == 1
    assert mg.gis_agent._execute_sql.await_count == 0  # aún no ejecutó

    resumed = await app.ainvoke(Command(resume={"status": "approved"}), _CFG)
    # IDEMPOTENCIA: NO regeneró el SQL al reanudar.
    assert mg.gis_agent.generate_sql_from_query.await_count == 1
    assert mg.gis_agent._execute_sql.await_count == 1
    assert resumed.get("raw_data") == [{"a": 1}]
    assert not resumed.get("error")


@pytest.mark.asyncio
async def test_interrupt_reject_does_not_execute():
    mg = _mock_graph()
    app = _build_app(mg)
    await app.ainvoke({"query": "trae t", "session_id": "s1"}, {"configurable": {"thread_id": "t-rej"}})
    resumed = await app.ainvoke(
        Command(resume={"status": "rejected", "feedback": "no"}),
        {"configurable": {"thread_id": "t-rej"}})
    assert mg.gis_agent._execute_sql.await_count == 0  # GUARDRAIL: no ejecutó
    assert "rechazada" in (resumed.get("error") or "").lower()


@pytest.mark.asyncio
async def test_blocking_reject_does_not_execute():
    """GUARDRAIL en el OTRO gateway: con hitl_mode='blocking' (HITLManager
    bloqueante) un REJECTED tampoco debe ejecutar el SQL. El reject de interrupt
    ya estaba cubierto (arriba); el de blocking no tenía cobertura a nivel de
    nodo — sólo el ciclo REST vía API (test_hitl_full_flow). Aquí invocamos el
    nodo REAL directo (sin grafo/interrupt) y verificamos que el guardrail
    corta ANTES de ``_execute_sql``."""
    from geo_copilot.security.hitl import HITLResponse, HITLStatus

    mg = _mock_graph()
    mg.hitl_manager.request_approval = AsyncMock(return_value=HITLResponse(
        request_id="r1", status=HITLStatus.REJECTED, feedback="query peligrosa"))

    blocking = SimpleNamespace(
        hitl_enabled=True, hitl_mode="blocking",
        autonomous_mode=False, max_retries=0,
    )
    with patch.object(gis_node, "get_settings", return_value=blocking), \
         patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")):
        out = await gis_node.run(mg, {"query": "borra t", "session_id": "s1"})

    mg.hitl_manager.request_approval.assert_awaited_once()  # pasó por el gateway blocking
    assert mg.gis_agent._execute_sql.await_count == 0        # GUARDRAIL: no ejecutó
    assert "rechazada" in (out.get("error") or "").lower()
    assert out.get("hitl_approved") is False


@pytest.mark.asyncio
async def test_interrupt_modify_executes_modified_sql():
    mg = _mock_graph()
    app = _build_app(mg)
    await app.ainvoke({"query": "trae t", "session_id": "s1"}, {"configurable": {"thread_id": "t-mod"}})
    await app.ainvoke(
        Command(resume={"status": "modified", "modified_content": "SELECT 1 LIMIT 1"}),
        {"configurable": {"thread_id": "t-mod"}})
    # Ejecutó el SQL MODIFICADO, no el generado.
    assert mg.gis_agent._execute_sql.await_count == 1
    assert mg.gis_agent._execute_sql.call_args[0][0] == "SELECT 1 LIMIT 1"


@pytest.mark.asyncio
async def test_pending_sql_cleared_after_completion():
    mg = _mock_graph()
    app = _build_app(mg)
    await app.ainvoke({"query": "trae t", "session_id": "s1"}, {"configurable": {"thread_id": "t-clr"}})
    assert mg._pending_sql.get("s1")  # pendiente mientras espera aprobación
    await app.ainvoke(Command(resume={"status": "approved"}), {"configurable": {"thread_id": "t-clr"}})
    assert "s1" not in mg._pending_sql  # limpiado al completar


# ---------------------------------------------------------------------------
# Plumbing del grafo: process() forkea a interrupt; resume_hitl; blocking intacto
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_process_forks_to_interrupt_mode(monkeypatch):
    from geo_copilot.core.config import get_settings as real_get_settings
    from geo_copilot.orchestrator.graph import GeoAgentGraph

    s = real_get_settings()
    monkeypatch.setattr(s, "hitl_mode", "interrupt")
    monkeypatch.setattr("geo_copilot.orchestrator.graph.get_settings", lambda: s)

    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    assert g.compiled_interrupt is not None  # se compiló el grafo con checkpointer
    g._run_interrupt_mode = AsyncMock(return_value={"status": "waiting_approval"})
    out = await g.process(query="x", session_id="s")
    assert out["status"] == "waiting_approval"
    g._run_interrupt_mode.assert_awaited_once()


def test_blocking_mode_has_no_interrupt_graph():
    from geo_copilot.orchestrator.graph import GeoAgentGraph
    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    assert g.compiled_interrupt is None  # default blocking → sin grafo interrupt


@pytest.mark.asyncio
async def test_resume_hitl_without_interrupt_is_honest():
    from geo_copilot.orchestrator.graph import GeoAgentGraph
    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    out = await g.resume_hitl("s1", {"status": "approved"})
    assert out["success"] is False and "no está habilitado" in out["message"]


def test_empty_session_id_rejected_no_cross_session_collision():
    # Aislamiento: interrupt mode no debe coalescer session vacía a 'default'.
    from geo_copilot.orchestrator.graph import GeoAgentGraph
    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    with pytest.raises(ValueError):
        g._interrupt_config("")
    assert g._interrupt_config("s1")["configurable"]["thread_id"] == "s1"


def test_waiting_approval_has_pending_id_for_api():
    from geo_copilot.orchestrator.graph import GeoAgentGraph
    resp = GeoAgentGraph._waiting_approval(None, "s1")
    assert resp["status"] == "waiting_approval"
    assert resp["pending_approval_id"]  # la API lo necesita para marcar WAITING
    assert resp["requires_approval"] is True


@pytest.mark.asyncio
async def test_sin_sql_la_explicacion_del_generador_es_la_respuesta():
    """Regresión F0/E0.2 (TH.16): con una tabla que no existe el generador responde SOLO con un
    comentario. Con el validador antes del HITL (TH.9) salía «SQL rechazado: 0 statements»;
    su explicación es la respuesta, sin pedir aprobación, sin ejecutar y sin reintentar."""
    mg = _mock_graph()
    mg.gis_agent.generate_sql_from_query = AsyncMock(
        return_value='-- No existe tabla "hospitales" en el esquema proporcionado,\n-- no es posible la consulta.')
    blocking = SimpleNamespace(hitl_enabled=True, hitl_mode="blocking", autonomous_mode=True, max_retries=2)
    with patch.object(gis_node, "get_settings", return_value=blocking), \
         patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")):
        out = await gis_node.run(mg, {"query": "¿cuántos hospitales hay?", "session_id": "s1"})

    assert out.get("final_response") == ('No existe tabla "hospitales" en el esquema proporcionado, '
                                         "no es posible la consulta.")
    assert not out.get("error")
    mg.hitl_manager.request_approval.assert_not_called()
    assert mg.gis_agent._execute_sql.await_count == 0
    assert mg.gis_agent.generate_sql_from_query.await_count == 1  # no reintenta


@pytest.mark.asyncio
async def test_sin_sql_y_con_servicios_conectados_el_turno_pasa_al_bucle():
    """F5 (T5.1): «sedes educativas de Soacha» no está en la BD interna pero sí en una BD conectada
    por MCP: en vez de «no existe esa tabla», el bucle recibe el hecho y decide."""
    mg = _mock_graph()
    mg.gis_agent.generate_sql_from_query = AsyncMock(return_value='-- No existe tabla "sedes educativas" en el esquema.')
    ajustes = SimpleNamespace(hitl_enabled=True, hitl_mode="blocking", autonomous_mode=True, max_retries=2)
    bucle = AsyncMock(return_value={"final_response": "Hay 41 sedes en Soacha."})
    with patch.object(gis_node, "get_settings", return_value=ajustes), \
         patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")), \
         patch.object(gis_node, "_hay_servicios_conectados", return_value=True), \
         patch("geo_copilot.orchestrator.graph._resolve_react_policy", return_value="hybrid"), \
         patch("geo_copilot.orchestrator.nodes.agent_loop.run", bucle):
        out = await gis_node.run(mg, {"query": "¿cuántas sedes educativas hay en Soacha?", "session_id": "s1"})
    assert out == {"final_response": "Hay 41 sedes en Soacha."}
    estado = bucle.await_args.args[1]
    assert "la BD interna no lo tiene" in estado["interpretacion_previa"]
    assert 'No existe tabla "sedes educativas"' in estado["interpretacion_previa"]
    mg.hitl_manager.request_approval.assert_not_called()


@pytest.mark.asyncio
async def test_desde_el_bucle_no_se_abre_otro_bucle_dentro():
    """El `query_database` del bucle llama a este mismo nodo: si la BD interna no lo tiene, el
    hecho vuelve al bucle como observación; abrir otro bucle anidado duplicaba pasos y límites."""
    mg = _mock_graph()
    mg.gis_agent.generate_sql_from_query = AsyncMock(return_value='-- No existe tabla "sedes educativas".')
    ajustes = SimpleNamespace(hitl_enabled=True, hitl_mode="blocking", autonomous_mode=True, max_retries=2)
    bucle = AsyncMock()
    with patch.object(gis_node, "get_settings", return_value=ajustes), \
         patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")), \
         patch.object(gis_node, "_hay_servicios_conectados", return_value=True), \
         patch("geo_copilot.orchestrator.graph._resolve_react_policy", return_value="hybrid"), \
         patch("geo_copilot.orchestrator.nodes.agent_loop.run", bucle):
        out = await gis_node.run(mg, {"query": "sedes", "session_id": "s1", "_desde_bucle": True})
    bucle.assert_not_awaited()
    assert "No existe tabla" in (out.get("final_response") or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("desde_bucle, al_bucle", [(False, True), (True, False)])
async def test_una_tabla_inventada_con_servicios_conectados_pasa_al_bucle(desde_bucle, al_bucle):
    """T5.6 (V5): el generador inventó `archivos.predios` (un servicio conectado tomado por tabla);
    el validador lo rechazó y la respuesta fue «no encontré esa tabla»."""
    mg = _mock_graph()
    mg.gis_agent.generate_sql_from_query = AsyncMock(return_value="SELECT * FROM archivos.predios")
    mg.gis_agent.sql_validator = SimpleNamespace(validate=lambda sql: {
        "is_valid": False, "errors": ["Estructura no admitida: tabla no disponible en el catálogo: archivos.predios"]})
    mg.gis_agent.sql_corrector = None
    ajustes = SimpleNamespace(hitl_enabled=True, hitl_mode="blocking", autonomous_mode=True, max_retries=1)
    bucle = AsyncMock(return_value={"final_response": "desde el bucle"})
    estado = {"query": "carga los predios del GeoParquet", "session_id": "s1",
              **({"_desde_bucle": True} if desde_bucle else {})}
    with patch.object(gis_node, "get_settings", return_value=ajustes), \
         patch.object(gis_node, "_preflight_entities", AsyncMock(return_value="")), \
         patch.object(gis_node, "_hay_servicios_conectados", return_value=True), \
         patch("geo_copilot.orchestrator.graph._resolve_react_policy", return_value="hybrid"), \
         patch("geo_copilot.orchestrator.nodes.agent_loop.run", bucle):
        out = await gis_node.run(mg, estado)
    if al_bucle:
        assert out == {"final_response": "desde el bucle"}
        assert "archivos" in bucle.await_args.args[1]["interpretacion_previa"]
    else:
        bucle.assert_not_awaited()
        assert "tabla no disponible" in (out.get("error") or "")
    mg.gis_agent._execute_sql.assert_not_awaited()


@pytest.mark.parametrize("ruta", ["_route_from_gis_agent", "_route_from_data_agent"])
def test_si_el_bucle_ya_respondio_el_grafo_no_vuelve_a_narrar(ruta):
    """V3 F5: tras el traspaso al bucle, simbología → insights re-narraban el último resultado
    parcial (5 filas de muestra) y la respuesta honesta del bucle se convertía en «Encontré 0 sedes»."""
    from geo_copilot.orchestrator.graph import GeoAgentGraph

    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    del_bucle = {"current_agent": "agent_loop", "final_response": "Alcancé el límite…",
                 "geojson": {"type": "FeatureCollection", "features": [{"id": 1}]}}
    assert getattr(g, ruta)(del_bucle) == "responder"
    propio = {"current_agent": "gis_agent", "geojson": {"type": "FeatureCollection", "features": [{"id": 1}]}}
    assert getattr(g, ruta)(propio) != "responder" or ruta == "_route_from_data_agent"
