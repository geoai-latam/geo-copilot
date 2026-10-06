"""V5 en Chrome (ronda de correcciones FH): «calcula el área de cada lote» terminaba en un
script de Python con 4 aprobaciones y una capa duplicada. Medir cada elemento es cálculo
exacto: con la herramienta `ws_add_measure` a la vista, el LLM la elige solo (nada lo fuerza).

Ejecutar: pytest -m llm tests/test_llm_fh6_medida.py -s
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

CAPA = {"id": "layer-1", "name": "Lotes Manzana 008510017", "kind": "vector-tiles", "geometry_type": "Polygon",
        "feature_count": 27, "fields": ["lotcodigo", "lotupredia", "barmanpre"], "visible": True, "is_active": True,
        "dataset_id": "ds_0123456789abcdef"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "medida"), [
    ("calcula el área de cada lote en metros cuadrados", "area"),
    ("agrégale a cada lote su perímetro", "perimetro"),
])
async def test_medir_cada_elemento_es_la_herramienta_exacta_y_no_un_script(query, medida, monkeypatch):
    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.orchestrator.nodes import agent_loop

    monkeypatch.setattr(ce, "store_actual", lambda: object())  # como en la app: hay workspace PostGIS

    llm = await get_real_llm_or_skip()
    graph = MagicMock()
    graph.llm = llm
    graph.agent_metrics = None
    llamadas: list[tuple[str, dict]] = []

    async def espia(g, working, name, args):  # no se ejecuta nada: interesa qué ELIGE
        llamadas.append((name, dict(args or {})))
        from geo_copilot.platform.capabilities import ToolOutcome
        if name == "ws_add_measure":
            campo = {"area": "area_m2", "perimetro": "perimetro_m"}.get(args.get("measure"), "longitud_m")
            return ToolOutcome(f'{{"hechos": {{"campo": "{campo}", "elementos": 27, "misma_capa": true}}}}', success=True)
        return ToolOutcome("ok", success=True)

    original = agent_loop.dispatch_tool
    agent_loop.dispatch_tool = espia
    try:
        out = await agent_loop.run(graph, {"query": query, "session_id": "sess-medida", "map_context": {"layers": [CAPA]}})
    finally:
        agent_loop.dispatch_tool = original
    print(f"\n[medida] {query!r} -> {llamadas}\n  {out.get('final_response')!r}")
    nombres = [n for n, _ in llamadas]
    assert "ws_add_measure" in nombres, llamadas
    args = dict(llamadas[nombres.index("ws_add_measure")][1])
    assert args.get("measure") == medida, args
    assert str(args.get("dataset")) in {"layer-1", "activa", CAPA["dataset_id"]}, args
    assert not {"spatial_operation", "run_python", "execute_python"} & set(nombres[: nombres.index("ws_add_measure")]), llamadas
