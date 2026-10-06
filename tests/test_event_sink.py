"""S1.2 — el núcleo emite su progreso a una interfaz, no a la API.

Antes 13 puntos de `orchestrator/` importaban `geo_copilot.api.websocket` dentro
de funciones (dependencia invertida, import circular esquivado a mano).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from geo_copilot.platform import events

SRC = Path(__file__).resolve().parents[1] / "src" / "geo_copilot"
NUCLEO = ("orchestrator", "agents", "core", "platform", "semantic", "security")


def test_el_nucleo_no_importa_la_api():
    """Test de arquitectura: ningún módulo del núcleo importa `geo_copilot.api`
    (ni a nivel de módulo ni dentro de una función)."""
    culpables = []
    for paquete in NUCLEO:
        for py in (SRC / paquete).rglob("*.py"):
            arbol = ast.parse(py.read_text(encoding="utf-8"))
            for nodo in ast.walk(arbol):
                modulo = None
                if isinstance(nodo, ast.ImportFrom):
                    modulo = nodo.module or ""
                elif isinstance(nodo, ast.Import):
                    modulo = ",".join(a.name for a in nodo.names)
                if modulo and "geo_copilot.api" in modulo:
                    culpables.append(f"{py.relative_to(SRC)}:{nodo.lineno} → {modulo}")
    assert not culpables, "el núcleo importa la API:\n" + "\n".join(culpables)


class _Grabador(events.NullSink):
    def __init__(self):
        self.eventos: list[tuple] = []

    async def plan_created(self, session_id, plan, reasoning=None):
        self.eventos.append(("plan_created", session_id, len(plan)))


def test_el_sink_cumple_el_protocolo():
    from geo_copilot.api.websocket import WebSocketEventSink

    assert isinstance(WebSocketEventSink(), events.EventSink)
    assert isinstance(events.NullSink(), events.EventSink)


@pytest.mark.asyncio
async def test_el_nodo_planner_emite_el_plan_por_el_sink(monkeypatch):
    """El nodo real, no el sink a mano: el evento llega a la interfaz."""
    from unittest.mock import AsyncMock, MagicMock

    from geo_copilot.orchestrator.nodes import planner as planner_node

    grabador = _Grabador()
    monkeypatch.setattr(events, "_sink", grabador)
    pasos = [
        {"step_id": "s1", "action_type": "query", "description": "traer lotes"},
        {"step_id": "s2", "action_type": "symbology", "description": "colorear",
         "depends_on": ["s1"]},
    ]
    graph = MagicMock()
    graph._get_cached_schema = AsyncMock(return_value="schema")
    graph.planner_agent.process = AsyncMock(
        return_value=MagicMock(success=True, data={"steps": pasos, "reasoning": "r"}),
    )
    await planner_node.run(graph, {"query": "q", "session_id": "s-sink"})
    assert grabador.eventos == [("plan_created", "s-sink", 2)]


@pytest.mark.asyncio
async def test_la_api_instala_el_sink_de_websocket():
    from fastapi.testclient import TestClient

    from geo_copilot.api.app import app
    from geo_copilot.api.websocket import WebSocketEventSink

    anterior = events.sink()
    try:
        with TestClient(app):
            assert isinstance(events.sink(), WebSocketEventSink)
    finally:
        events.set_sink(anterior)
