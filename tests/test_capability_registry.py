"""S1.3 — registro de capacidades: una capacidad es UNA entrada.

Antes, añadir una herramienta tocaba ~13 sitios (enum del router, prompt, schema
a mano, cadena `if name ==` del despacho, filtro de disponibilidad, mapa de
pasos del chip…). El test central registra una capacidad en un solo lugar y
comprueba que el bucle ReAct la ofrece, la describe, la ejecuta y la anuncia.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from geo_copilot.core.scripted_llm import ScriptedLLM, tool_call_response
from geo_copilot.orchestrator.nodes import agent_loop
from geo_copilot.platform import events
from geo_copilot.platform.capabilities import (
    ANSWER_TOOL,
    Capability,
    CapabilityRegistry,
    ToolOutcome,
    registry,
)


def _cap(nombre="contar_arboles", **over) -> Capability:
    async def ejecutor(graph, working, args):
        return ToolOutcome(
            observation=f"{args.get('zona')}: 42 árboles", success=True,
            delta={"raw_data": [{"arboles": 42}]},
        )

    datos = {
        "id": f"test.{nombre}", "tool_name": nombre,
        "description": "Cuenta los árboles de una zona (capacidad de prueba).",
        "parameters": {"type": "object", "properties": {"zona": {"type": "string"}},
                       "required": ["zona"], "additionalProperties": False},
        "executor": ejecutor,
        "blurb": "cuenta árboles en una zona (prueba).",
        "step": ("data_agent", "Contando árboles"),
    }
    datos.update(over)
    return Capability(**datos)


@pytest.fixture
def capacidad_registrada():
    cap = registry().register(_cap())
    yield cap
    registry().unregister(cap.tool_name)


class _Pasos(events.NullSink):
    def __init__(self):
        self.pasos = []

    async def agent_step(self, session_id, agent, description, status):
        self.pasos.append((agent, description, status))


@pytest.mark.asyncio
async def test_una_capacidad_nueva_se_ofrece_se_describe_se_ejecuta_y_se_anuncia(
    capacidad_registrada, monkeypatch,
):
    grabador = _Pasos()
    monkeypatch.setattr(events, "_sink", grabador)
    llm = ScriptedLLM([
        tool_call_response("contar_arboles", {"zona": "Soacha"}),
        tool_call_response("answer", {"text": "Hay 42 árboles."}, call_id="c2"),
    ])
    graph = MagicMock()
    graph.llm = llm
    graph.agent_metrics = None
    ajustes = SimpleNamespace(react_max_tool_calls=8, react_token_budget=0,
                              react_max_reflections=0)
    with patch.object(agent_loop, "get_settings", return_value=ajustes):
        out = await agent_loop.run(graph, {"query": "árboles en Soacha", "session_id": "s"})

    primera = llm.calls[0]
    ofrecidas = [t["function"]["name"] for t in primera["tools"]]
    assert "contar_arboles" in ofrecidas                       # se ofrece al LLM
    assert "cuenta árboles en una zona" in primera["messages"][0].content  # se describe
    assert out["raw_data"] == [{"arboles": 42}]                # se ejecutó
    assert out["final_response"] == "Hay 42 árboles."
    assert ("data_agent", "Contando árboles", "started") in grabador.pasos  # se anuncia


@pytest.mark.asyncio
async def test_no_disponible_no_se_ofrece_ni_se_describe(monkeypatch):
    cap = registry().register(_cap("capacidad_apagada", available=lambda g: False))
    try:
        graph = MagicMock()
        assert "capacidad_apagada" not in agent_loop.react_system_prompt(graph)
        assert "capacidad_apagada" not in [
            s["function"]["name"] for s in registry().tool_schemas(graph)
        ]
    finally:
        registry().unregister(cap.tool_name)


def test_las_nueve_herramientas_del_nucleo_estan_registradas():
    from geo_copilot.orchestrator.capabilities_core import ensure_core

    ensure_core()
    assert {
        "query_database", "spatial_operation", "analyze_layer",
        "apply_symbology", "search_external", "select_service", "load_external",
    } <= {c.tool_name for c in registry().all()}
    assert ANSWER_TOOL in registry().names()


class TestReglasDelRegistro:
    def test_answer_esta_reservado(self):
        with pytest.raises(ValueError, match="reservado"):
            CapabilityRegistry().register(_cap(ANSWER_TOOL))

    def test_no_se_pisa_una_capacidad_sin_querer(self):
        reg = CapabilityRegistry()
        reg.register(_cap())
        with pytest.raises(ValueError, match="ya hay"):
            reg.register(_cap())
        reg.register(_cap(), replace=True)  # explícito sí

    def test_parametros_deben_ser_un_objeto(self):
        with pytest.raises(ValueError, match="JSON Schema de objeto"):
            CapabilityRegistry().register(_cap(parameters={"type": "string"}))

    def test_answer_siempre_va_al_final_del_catalogo(self):
        reg = CapabilityRegistry()
        reg.register(_cap())
        assert [s["function"]["name"] for s in reg.tool_schemas()] == [
            "contar_arboles", ANSWER_TOOL,
        ]


@pytest.mark.asyncio
async def test_despacho_de_herramienta_desconocida_es_honesto():
    from geo_copilot.orchestrator.react_tools import dispatch_tool

    out = await dispatch_tool(MagicMock(), {}, "no_existe", {})
    assert out.success is False and "desconocida" in out.observation
