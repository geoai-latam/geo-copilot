"""Regresión C3a-1: límite de recursión del grafo.

Antes ``ainvoke`` se llamaba sin ``recursion_limit``; un plan multi-paso
legítimo podía tocar el default (25) de LangGraph y lanzar
``GraphRecursionError``, que se tragaba en el except genérico como un
"Error: ..." opaco.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.errors import GraphRecursionError

from geo_copilot.orchestrator.graph import GeoAgentGraph


def _graph():
    return GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)


@pytest.mark.asyncio
async def test_recursion_limit_is_passed_to_ainvoke():
    graph = _graph()
    captured = {}

    async def _fake_ainvoke(state, config=None):
        captured["config"] = config
        return {"final_response": "ok"}

    graph.compiled.ainvoke = _fake_ainvoke

    await graph.process(query="hola", session_id="s1")

    assert captured["config"] is not None
    assert captured["config"].get("recursion_limit", 0) >= 25


@pytest.mark.asyncio
async def test_graph_recursion_error_is_handled_gracefully():
    graph = _graph()
    graph.compiled.ainvoke = AsyncMock(side_effect=GraphRecursionError("boom"))

    result = await graph.process(query="plan gigante", session_id="s2")

    assert result["success"] is False
    # Mensaje específico, no un "Error: boom" genérico.
    assert "boom" not in result["message"]
    assert "paso" in result["message"].lower() or "plan" in result["message"].lower()
