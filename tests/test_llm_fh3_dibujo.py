"""FH.3 con LLM REAL: «lo que dibujé» es el dibujo del usuario, no la capa activa.

La herramienta de imagery es sintética (misma descripción y esquema de referencia
geo que el hub le da al LLM); se prueba la ELECCIÓN del área y de las fechas.
El DoD completo (imagery real) se valida en el navegador (V5).

Ejecutar: pytest -m llm tests/test_llm_fh3_dibujo.py -s
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

DIBUJO_DS = "ds_145dbc4bac4d4842"
MAPA = {
    "layers": [
        {"id": "layer-1", "name": "Lotes Manzana 004503004", "kind": "vector-geojson", "geometry_type": "Polygon",
         "feature_count": 30, "dataset_id": "ds_d96b1e0f541d4363", "fields": ["lotcodigo", "manzcodigo"],
         "visible": True, "is_active": True, "opacity": 1},
        {"id": "layer-2", "name": "Área 1", "kind": "vector-geojson", "geometry_type": "Polygon",
         "feature_count": 1, "dataset_id": DIBUJO_DS, "fields": [], "visible": True, "is_active": False,
         "opacity": 1, "origin": {"capability": "user.sketch", "arguments": {"geometria": "Polygon"}}},
    ],
    "acciones": [{"op": "add_layer", "layer_id": "layer-2", "layer_name": "Área 1", "args": {"dibujo": "Polygon"},
                  "at": "2026-09-25T18:00:00Z", "author": "user", "undone": False}],
}


@pytest.fixture
def ndvi():
    """La tool como la construye el hub (descripción + esquema que ve el LLM), sin red."""
    from geo_copilot.orchestrator.capabilities_core import ensure_core
    from geo_copilot.platform.capabilities import ToolOutcome, registry
    from geo_copilot.platform.mcp.hub import EstadoTool, McpHub

    llamadas: list[dict] = []
    tool = SimpleNamespace(
        name="imagery_ndvi",
        description="NDVI medio y mapa NDVI de Sentinel-2 sobre un AOI entre dos fechas (YYYY-MM-DD).",
        inputSchema={"type": "object", "properties": {
            "aoi_geojson": {"type": "object", "additionalProperties": True, "title": "Aoi Geojson"},
            "date_from": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            "date_to": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        }, "required": ["aoi_geojson"]},
    )
    est = EstadoTool(servidor="imagery", tool="imagery_ndvi", nombre_llm="imagery__imagery_ndvi", habilitada=True,
                     motivo=None, riesgo="compute", huella="x", geo={"inputs": {"aoi_geojson": ["geometry", "layer_ref"]}})
    cfg = SimpleNamespace(id="imagery", conformance="G2")
    cap = McpHub._capacidad(SimpleNamespace(), cfg, tool, est)  # type: ignore[arg-type]

    async def _ejecutar(graph, working, args):
        llamadas.append(dict(args))
        return ToolOutcome('{"hechos": {"ndvi_mean": 0.65, "escena": "2026-06-11", "cloud_pct": 19.3}}', success=True)

    ensure_core()
    cap = dataclasses.replace(cap, executor=_ejecutar)
    registry().register(cap, replace=True)
    yield llamadas
    registry().unregister(cap.tool_name)


@pytest.mark.asyncio
@pytest.mark.parametrize("pedido", [
    "NDVI de lo que dibujé entre marzo y junio",
    "¿cómo está la vegetación en el área que acabo de dibujar? de marzo a junio",
])
async def test_lo_que_dibuje_es_el_dibujo(ndvi, pedido):
    from geo_copilot.orchestrator.nodes import agent_loop

    graph = SimpleNamespace(llm=await get_real_llm_or_skip(), agent_metrics=None, hitl_manager=None)
    ajustes = MagicMock(react_max_reflections=0, react_max_tool_calls=4, react_token_budget=0, mcp_tools_umbral=25)
    hub = SimpleNamespace(resumen_prompt=lambda: "SERVICIOS MCP CONECTADOS:\n  - imagery (disponible): NDVI Sentinel-2")
    with patch.object(agent_loop, "get_settings", return_value=ajustes), \
            patch("geo_copilot.platform.mcp.hub.hub_actual", return_value=hub):
        out = await agent_loop.run(graph, {"query": pedido, "session_id": "s", "map_context": MAPA})
    print(f"\n[FH.3] {pedido!r} -> {ndvi}\n  {out.get('final_response')!r}")
    assert ndvi, "no pidió el NDVI"
    aoi = str(ndvi[0]["aoi_geojson"]).strip("[]")
    # el dibujo (por id, dataset o su nombre exacto, que el hub resuelve), no los lotes activos
    # TH.15: `dibujo` (lo último que dibujó el usuario) es la referencia propia de «lo que dibujé»
    assert aoi in ("layer-2", DIBUJO_DS, "Área 1", "dibujo"), ndvi
    assert ndvi[0]["date_from"].endswith("-03-01") and ndvi[0]["date_to"].endswith("-06-30"), ndvi
