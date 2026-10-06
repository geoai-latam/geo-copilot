"""LIVE-01: la narración de un COUNT/agregación debe verbalizar el ESCALAR de
la fila de resultado, no el feature-count de una capa que quedó cargada en el
estado.

Bug: al preguntar "¿cuántos lotes hay?" tras haber cargado 200 lotes, el
resultado COUNT(*) (933.473) es 1 fila SIN geometría, pero
``state["external_geojson"]`` aún tenía los 200 features de la capa previa →
la narración arrastraba "200" en vez del escalar real.

Los facts se arman en código (LLM-pilar: el LLM narra, el código sólo provee
HECHOS); estos tests inspeccionan el prompt que recibe el narrador, no piden un
LLM real.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _graph_capturing(captured: dict):
    class _Resp:
        content = "ok"

    graph = MagicMock()

    async def _chat(messages):
        captured["prompt"] = messages[0].content
        return _Resp()

    graph.llm.chat = _chat
    return graph


@pytest.mark.asyncio
async def test_agregacion_narra_escalar_no_capa_previa():
    from geo_copilot.orchestrator.nodes import insights

    captured: dict = {}
    graph = _graph_capturing(captured)

    # COUNT(*) → 1 fila escalar; y una capa de 200 lotes cargada antes que quedó
    # en external_geojson. Sin el fix, feature_count=200 contaminaba la narración.
    state = {
        "query": "¿cuántos lotes hay?",
        "intent": "query_data",
        "raw_data": [{"total": 933473}],
        "geojson": None,
        "external_geojson": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": None}
            ] * 200,
        },
        "layer_name": "Lotes",
    }

    await insights.run(graph, state)
    prompt = captured["prompt"]

    assert "933473" in prompt, "debe verbalizar el escalar real del COUNT"
    assert "AGREGACIÓN" in prompt, "debe marcar la fila como agregación"
    # El hecho de conteo narrado es 1 fila (el escalar va en la nota), NO los 200
    # de la capa previa.
    assert "Resultados: 1 registro" in prompt
    assert "200" not in prompt, "no debe arrastrar el feature-count de la capa previa"


@pytest.mark.asyncio
async def test_conteo_normal_no_agregado_conserva_feature_count():
    """Guardia de no-regresión: una consulta que trae 200 features (NO una
    agregación de 1 fila) sigue narrando 200 y sin nota de agregación."""
    from geo_copilot.orchestrator.nodes import insights

    captured: dict = {}
    graph = _graph_capturing(captured)

    feats = [
        {"type": "Feature", "properties": {"id": i}, "geometry": None}
        for i in range(200)
    ]
    state = {
        "query": "trae 200 lotes",
        "intent": "query_data",
        "raw_data": [{"id": i} for i in range(200)],
        "geojson": {"type": "FeatureCollection", "features": feats},
        "layer_name": "Lotes",
    }

    await insights.run(graph, state)
    prompt = captured["prompt"]

    assert "Resultados: 200 registros" in prompt
    assert "AGREGACIÓN" not in prompt
