"""F3.1: tracking de dependencias (DAG) en el plan multi-paso.

VALIDACIÓN CRÍTICA: un fallo en un paso NO mata pasos independientes; los pasos
que dependen del fallido se SALTAN honestamente (no se ejecutan sobre datos
obsoletos). El bucle procesa TODOS los pasos en vez de abortar al primer fallo.
"""

from unittest.mock import MagicMock

import pytest

from geo_copilot.orchestrator.nodes import step_finalizer, step_router
from geo_copilot.orchestrator.nodes.responder import run as responder_run
from geo_copilot.orchestrator.plan_deps import (
    blocked_dependencies,
    step_dependencies,
    unavailable_step_ids,
)


def _s(step_id, action="query_database", depends_on=None):
    d = {"step_id": step_id, "action_type": action, "description": step_id,
         "query_fragment": f"frag {step_id}"}
    if depends_on is not None:
        d["depends_on"] = depends_on
    return d


# ---------------------------------------------------------------------------
# plan_deps: derivación de dependencias
# ---------------------------------------------------------------------------
def test_dependencies_explicit():
    plan = [_s("s1"), _s("s2", depends_on=["s1"])]
    assert step_dependencies(plan[1], 1, plan) == ["s1"]


def test_dependencies_linear_default_when_absent():
    # Sin depends_on → depende del paso anterior (default lineal conservador).
    plan = [_s("s1"), _s("s2")]
    assert step_dependencies(plan[1], 1, plan) == ["s1"]
    assert step_dependencies(plan[0], 0, plan) == []  # el primero no depende


def test_dependencies_empty_means_independent():
    plan = [_s("s1"), _s("s2", depends_on=[])]
    assert step_dependencies(plan[1], 1, plan) == []


def test_unavailable_excludes_pending_selection():
    results = [
        {"step_id": "s1", "success": False},
        {"step_id": "s2", "success": True},
        {"step_id": "s3", "success": False, "pending_selection": True},
        {"step_id": "s4", "success": False, "skipped": True},
    ]
    assert unavailable_step_ids(results) == {"s1", "s4"}  # s3 (pausa) excluido


def test_blocked_independent_branch_survives():
    # s1 falló; s2 depende de s1 (bloqueado), s3 es independiente (NO bloqueado).
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["s1"]), _s("s3", depends_on=[])]
    results = [{"step_id": "s1", "success": False}]
    assert blocked_dependencies(plan[1], 1, plan, results) == ["s1"]
    assert blocked_dependencies(plan[2], 2, plan, results) == []  # independiente


def test_blocked_transitive_via_skipped():
    # s1 falló → s2 saltado (success False) → s3 depende de s2 → bloqueado.
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["s1"]), _s("s3", depends_on=["s2"])]
    results = [
        {"step_id": "s1", "success": False},
        {"step_id": "s2", "success": False, "skipped": True},
    ]
    assert blocked_dependencies(plan[2], 2, plan, results) == ["s2"]


def test_satisfied_dependency_not_blocked():
    # Dependencia que SÍ terminó con éxito → no bloquea (cadena lineal normal).
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["s1"])]
    results = [{"step_id": "s1", "success": True}]
    assert blocked_dependencies(plan[1], 1, plan, results) == []


# --- depends_on malformado: el gating "satisfecho" lo cubre sin deadlock (F3.1)
def test_nonexistent_dependency_is_blocked():
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["step_99"])]
    results = [{"step_id": "s1", "success": True}]
    assert blocked_dependencies(plan[1], 1, plan, results) == ["step_99"]


def test_forward_dependency_is_blocked():
    # s1 depende de s2 (posterior) → aún no satisfecho cuando s1 corre → bloqueado.
    plan = [_s("s1", depends_on=["s2"]), _s("s2", depends_on=[])]
    assert blocked_dependencies(plan[0], 0, plan, []) == ["s2"]


def test_self_dependency_is_blocked():
    plan = [_s("s1", depends_on=["s1"])]
    assert blocked_dependencies(plan[0], 0, plan, []) == ["s1"]


def test_cycle_both_blocked_no_deadlock():
    # s2<->s3 ciclo: ninguno se satisface → ambos bloqueados (se saltan, sin colgar).
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["s3"]), _s("s3", depends_on=["s2"])]
    results = [{"step_id": "s1", "success": True}]
    assert blocked_dependencies(plan[1], 1, plan, results) == ["s3"]
    assert blocked_dependencies(plan[2], 2, plan, results) == ["s2"]


def test_dependencies_none_equals_missing_key():
    # depends_on ausente y depends_on=None se resuelven idénticos (default lineal).
    plan_missing = [_s("s1"), _s("s2")]  # s2 sin la clave
    plan_none = [_s("s1"), {**_s("s2"), "depends_on": None}]
    assert step_dependencies(plan_missing[1], 1, plan_missing) == \
        step_dependencies(plan_none[1], 1, plan_none)


# ---------------------------------------------------------------------------
# step_router: routea pasos bloqueados a step_finalizer (no ejecuta)
# ---------------------------------------------------------------------------
def test_route_blocked_step_goes_to_finalizer():
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["s1"])]
    state = {
        "execution_plan": plan, "current_step_index": 1,
        "step_results": [{"step_id": "s1", "success": False}],
    }
    assert step_router.route_from_step_router(state) == "step_finalizer"


def test_route_unblocked_step_goes_to_agent():
    plan = [_s("s1", action="query_database", depends_on=[])]
    state = {"execution_plan": plan, "current_step_index": 0, "step_results": []}
    # step_router.run setea intent; aquí lo simulamos vía el mapeo del nodo.
    assert step_router.route_from_step_router({**state, "intent": "query_data"}) == "data_agent"


@pytest.mark.asyncio
async def test_step_router_run_skips_blocked_without_intent():
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["s1"])]
    state = {
        "execution_plan": plan, "current_step_index": 1, "session_id": "",
        "step_results": [{"step_id": "s1", "success": False}],
    }
    updates = await step_router.run(MagicMock(), state)
    # No prepara intent de ejecución para un step bloqueado.
    assert "intent" not in updates


# ---------------------------------------------------------------------------
# step_finalizer: skip de bloqueados + fallo NO aborta (avanza)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalizer_records_skip_for_blocked_step():
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=["s1"])]
    state = {
        "execution_plan": plan, "current_step_index": 1, "session_id": "",
        "step_results": [{"step_id": "s1", "success": False}],
    }
    updates = await step_finalizer.run(MagicMock(), state)
    entry = updates["step_results"][-1]
    assert entry["step_id"] == "s2" and entry["skipped"] is True
    assert updates["current_step_index"] == 2  # avanza
    assert updates["error"] is None


@pytest.mark.asyncio
async def test_finalizer_failure_advances_not_abort():
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=[])]  # s2 independiente
    state = {
        "execution_plan": plan, "current_step_index": 0, "session_id": "",
        "step_results": [], "error": "boom",  # s1 falló
    }
    updates = await step_finalizer.run(MagicMock(), state)
    entry = updates["step_results"][-1]
    assert entry["step_id"] == "s1" and entry["success"] is False
    assert updates["current_step_index"] == 1   # AVANZA (no aborta)
    assert updates["error"] is None             # limpia error → no fatal


@pytest.mark.asyncio
async def test_finalizer_success_advances():
    plan = [_s("s1", depends_on=[]), _s("s2", depends_on=[])]
    state = {
        "execution_plan": plan, "current_step_index": 0, "session_id": "",
        "step_results": [], "geojson": {"type": "FeatureCollection", "features": [{"x": 1}]},
    }
    updates = await step_finalizer.run(MagicMock(), state)
    assert updates["step_results"][-1]["success"] is True
    assert updates["current_step_index"] == 1


def test_route_finalizer_continues_after_failure():
    # Tras un fallo (error limpiado), si quedan steps → vuelve al loop.
    state = {
        "execution_plan": [_s("s1"), _s("s2")], "current_step_index": 1,
        "step_results": [{"step_id": "s1", "success": False}], "error": None,
    }
    assert step_finalizer.route_from_step_finalizer(state) == "step_router"


def test_route_finalizer_responder_when_done():
    state = {
        "execution_plan": [_s("s1"), _s("s2")], "current_step_index": 2,
        "step_results": [{"step_id": "s1", "success": True}, {"step_id": "s2", "success": True}],
    }
    assert step_finalizer.route_from_step_finalizer(state) == "responder"


# ---------------------------------------------------------------------------
# responder: desglose éxito/fallo/saltado
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_responder_reports_failed_and_skipped():
    from unittest.mock import AsyncMock

    graph = MagicMock()
    graph.insights_agent.infer_visualization_type = AsyncMock(return_value={"type": "map"})
    # El desenlace ahora lo narra el LLM (versátil, no "falló N paso(s)").
    narration = "Cargué los hospitales en azul; no encontré bomberos, ¿los busco en otra fuente?"
    graph.llm.chat = AsyncMock(return_value=MagicMock(content=narration))
    state = {
        "query": "carga bomberos rojo y hospitales azul",
        "geojson": {"type": "FeatureCollection", "features": [{"x": 1}]},
        "raw_data": [],
        "step_results": [
            {"step_id": "s1", "success": False, "description": "bomberos", "error": "no encontrado"},
            {"step_id": "s2", "success": False, "skipped": True},
            {"step_id": "s3", "success": True, "description": "hospitales"},
            {"step_id": "s4", "success": True, "description": "azul"},
        ],
    }
    result = await responder_run(graph, state)
    # Contrato: el plan se marca como parcialmente fallido...
    assert result["plan_partial_failure"] is True
    # ...y el mensaje es la narración del LLM (no el técnico "falló N paso(s)").
    assert result["final_response"] == narration
    assert "paso" not in result["final_response"].lower()


# ---------------------------------------------------------------------------
# planner: depends_on se preserva
# ---------------------------------------------------------------------------
def test_step_to_dict_includes_depends_on():
    from geo_copilot.orchestrator.planner import PlannerAgent, PlanStep

    step = PlanStep(step_id="s2", description="d", query_fragment="q",
                    action_type="symbology", depends_on=["s1"])
    # _step_to_dict no usa self; lo llamamos con un objeto mínimo.
    d = PlannerAgent._step_to_dict(MagicMock(), step)
    assert d["depends_on"] == ["s1"]


def test_planstep_depends_on_defaults_none():
    from geo_copilot.orchestrator.planner import PlanStep

    assert PlanStep(step_id="s1", description="d").depends_on is None


# ---------------------------------------------------------------------------
# step_router: limpia output de pasos que generan datos (anti-stale-state)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step_router_clears_output_for_sourcing_step():
    # query_database arranca con pizarra limpia: no hereda geojson del paso previo.
    plan = [_s("s1", action="query_database", depends_on=[])]
    state = {
        "execution_plan": plan, "current_step_index": 0, "session_id": "",
        "step_results": [], "geojson": {"stale": True}, "raw_data": [{"old": 1}],
    }
    updates = await step_router.run(MagicMock(), state)
    assert updates["geojson"] is None
    assert updates["raw_data"] is None
    assert updates["symbology"] is None


@pytest.mark.asyncio
async def test_step_router_keeps_geojson_for_transform_step():
    # spatial_operation CONSUME el geojson de entrada → NO se limpia.
    plan = [_s("s1", action="spatial_operation", depends_on=[])]
    state = {
        "execution_plan": plan, "current_step_index": 0, "session_id": "",
        "step_results": [], "geojson": {"features": [1]},
    }
    updates = await step_router.run(MagicMock(), state)
    assert "geojson" not in updates  # no lo toca → el python_agent lo usa


@pytest.mark.asyncio
async def test_generate_plan_dedups_step_ids():
    import json

    from geo_copilot.orchestrator.planner import PlannerAgent

    plan_json = {
        "reasoning": "dup ids",
        "steps": [
            {"step_id": "step_1", "action_type": "query_database", "description": "a",
             "query_fragment": "a"},
            {"step_id": "step_1", "action_type": "symbology", "description": "b",
             "query_fragment": "b", "depends_on": ["step_1"]},
        ],
    }

    # A3: el plan llega como tool_call `create_plan` (structured outputs).
    from geo_copilot.core.scripted_llm import tool_call_response

    fake_llm = MagicMock()

    async def _chat(messages, **kwargs):
        return tool_call_response("create_plan", plan_json)

    fake_llm.chat = _chat
    agent = PlannerAgent(llm_client=fake_llm)
    plan = await agent.generate_plan("q", {})
    ids = [s.step_id for s in plan.steps]
    assert len(ids) == len(set(ids))  # sin duplicados tras la dedup
