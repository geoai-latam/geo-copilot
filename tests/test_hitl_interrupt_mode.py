"""F4.2 (spike): HITL vía LangGraph interrupt() + checkpointer.

VALIDACIÓN CRÍTICA: el mecanismo de interrupt preserva la semántica de guardrail
(approve ejecuta, reject aborta, modify usa el contenido modificado) en un
round-trip real contra un grafo compilado con MemorySaver. Y el guardrail de
PRODUCCIÓN sigue intacto: hitl_mode default 'blocking', el grafo de producción
sin checkpointer. Todo el HITL bloqueante existente queda sin tocar.
"""

import asyncio

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from typing_extensions import TypedDict

from geo_copilot.orchestrator.hitl_interrupt import extract_resume, raise_hitl_interrupt
from geo_copilot.security.hitl import HITLResponse, HITLStatus


class _S(TypedDict, total=False):
    sql: str
    result: str
    error: str


def _approval_node(state: _S) -> dict:
    """Mira la semántica del nodo gis_agent: approve ejecuta, reject aborta,
    modify usa el SQL modificado — pero vía interrupt()."""
    resp = extract_resume(raise_hitl_interrupt({"type": "sql_approval", "sql": state["sql"]}))
    if resp.status == HITLStatus.REJECTED:
        return {"error": f"Consulta rechazada: {resp.feedback}"}
    if resp.status == HITLStatus.EXPIRED:
        return {"error": "Aprobación expirada"}
    sql = resp.modified_content if resp.status == HITLStatus.MODIFIED else state["sql"]
    return {"result": f"executed: {sql}"}


def _build_graph():
    g = StateGraph(_S)
    g.add_node("approval", _approval_node)
    g.add_edge(START, "approval")
    g.add_edge("approval", END)
    return g.compile(checkpointer=MemorySaver())


def _is_paused(result: dict) -> bool:
    return "__interrupt__" in result


# ---------------------------------------------------------------------------
# Round-trip: approve / reject / modify
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_interrupt_pauses_then_approves():
    app = _build_graph()
    cfg = {"configurable": {"thread_id": "t-approve"}}
    paused = await app.ainvoke({"sql": "SELECT 1"}, cfg)
    assert _is_paused(paused)  # el grafo se PAUSÓ pidiendo aprobación
    assert paused["__interrupt__"][0].value["sql"] == "SELECT 1"  # payload llegó

    resumed = await app.ainvoke(Command(resume={"status": "approved"}), cfg)
    assert resumed["result"] == "executed: SELECT 1"
    assert "error" not in resumed


@pytest.mark.asyncio
async def test_interrupt_reject_aborts():
    app = _build_graph()
    cfg = {"configurable": {"thread_id": "t-reject"}}
    await app.ainvoke({"sql": "DROP TABLE x"}, cfg)
    resumed = await app.ainvoke(
        Command(resume={"status": "rejected", "feedback": "no autorizado"}), cfg)
    assert resumed.get("result") is None  # NO se ejecutó
    assert "rechazada" in resumed["error"]


@pytest.mark.asyncio
async def test_interrupt_modify_uses_modified_sql():
    app = _build_graph()
    cfg = {"configurable": {"thread_id": "t-modify"}}
    await app.ainvoke({"sql": "SELECT * FROM t"}, cfg)
    resumed = await app.ainvoke(
        Command(resume={"status": "modified", "modified_content": "SELECT 1 LIMIT 1"}), cfg)
    assert resumed["result"] == "executed: SELECT 1 LIMIT 1"  # usó el modificado


@pytest.mark.asyncio
async def test_interrupt_is_non_blocking():
    # Diferencia clave vs el HITL bloqueante: ainvoke RETORNA de inmediato al
    # pausar (no espera). El timeout deja de vivir dentro de request_approval;
    # pasa a ser responsabilidad del nivel API (documentado como diferido).
    app = _build_graph()
    cfg = {"configurable": {"thread_id": "t-nonblock"}}
    result = await asyncio.wait_for(app.ainvoke({"sql": "SELECT 1"}, cfg), timeout=2.0)
    assert _is_paused(result)  # no colgó esperando aprobación


# ---------------------------------------------------------------------------
# extract_resume: normalización y sesgo seguro
# ---------------------------------------------------------------------------
def test_extract_resume_accepts_models_dicts_and_strings():
    r = extract_resume(HITLResponse(request_id="x", status=HITLStatus.APPROVED))
    assert r.status == HITLStatus.APPROVED
    d = extract_resume({"status": "modified", "modified_content": "Q"})
    assert d.status == HITLStatus.MODIFIED and d.modified_content == "Q"
    s = extract_resume("approved")
    assert s.status == HITLStatus.APPROVED


def test_extract_resume_ambiguous_is_not_auto_approved():
    # Sesgo seguro: una reanudación ambigua NO aprueba una acción sensible.
    assert extract_resume({"status": "garbage"}).status == HITLStatus.EXPIRED
    assert extract_resume(object()).status == HITLStatus.EXPIRED


# ---------------------------------------------------------------------------
# Guardrail de producción intacto
# ---------------------------------------------------------------------------
def test_production_hitl_mode_defaults_to_blocking():
    from geo_copilot.core.config import get_settings
    assert get_settings().hitl_mode == "blocking"


def test_production_graph_has_no_checkpointer():
    # El grafo de producción se compila SIN checkpointer (camino bloqueante).
    from unittest.mock import MagicMock

    from geo_copilot.orchestrator.graph import GeoAgentGraph
    g = GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)
    assert getattr(g.compiled, "checkpointer", None) is None
