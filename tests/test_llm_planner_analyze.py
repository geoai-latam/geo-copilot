"""Tests con LLM REAL (marker `llm`) — R1.1: el planner emite `analyze`.

Valida con el modelo real (no mocks) que el planner descompone consultas
compuestas analíticas usando el action_type `analyze` para la parte de
análisis/ML, y que sigue usando `query_database` para traer datos.

Ejecutar: pytest -m llm tests/test_llm_planner_analyze.py
"""

from __future__ import annotations

import pytest

from geo_copilot.orchestrator.planner import PlannerAgent

pytestmark = pytest.mark.llm


@pytest.mark.asyncio
async def test_plan_compuesto_analitico_emite_analyze():
    agent = PlannerAgent()  # LLM real desde settings
    plan = await agent.generate_plan(
        "trae 200 lotes de catastro y agrúpalos en clusters con DBSCAN "
        "reportando cuántos lotes tiene cada cluster",
        {"schema_info": "Tablas geoespaciales disponibles:\n- catastro.lotes (MULTIPOLYGON)"},
    )
    actions = [s.action_type for s in plan.steps]
    # Debe traer datos y luego analizar — sin adivinar todo como query_database.
    assert "query_database" in actions, actions
    assert "analyze" in actions, actions
    # El paso analyze depende (explícita o linealmente) del paso de datos.
    idx_q = actions.index("query_database")
    idx_a = actions.index("analyze")
    assert idx_a > idx_q, actions


@pytest.mark.asyncio
async def test_plan_estadistico_emite_analyze():
    agent = PlannerAgent()
    plan = await agent.generate_plan(
        "obtén las construcciones de catastro y calcula la correlación entre "
        "número de pisos y altura, con un gráfico de dispersión",
        {"schema_info": "Tablas geoespaciales disponibles:\n- catastro.construcciones (MULTIPOLYGON)"},
    )
    actions = [s.action_type for s in plan.steps]
    assert "analyze" in actions, actions


@pytest.mark.asyncio
async def test_plan_simple_no_abusa_de_analyze():
    # Anti-regresión: una consulta SIN análisis no debe inventar pasos analyze.
    agent = PlannerAgent()
    plan = await agent.generate_plan(
        "trae 100 lotes de catastro y coloréalos de azul",
        {"schema_info": "Tablas geoespaciales disponibles:\n- catastro.lotes (MULTIPOLYGON)"},
    )
    actions = [s.action_type for s in plan.steps]
    assert "analyze" not in actions, actions
    assert "query_database" in actions, actions
