"""FH.4 con LLM REAL: una mención `@` fija la capa sin inferir por el nombre.

Tres capas (lotes ACTIVA, «Vías» y «Vías 2021», nombres confundibles). Con la
mención, el buffer va sobre la capa mencionada. La geometría está simulada
(ops.buffer); se juzga la ELECCIÓN.

Ejecutar: pytest -m llm tests/test_llm_fh4_menciones.py -s
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.platform.contracts import FieldInfo, LayerRef, Provenance, WorkspaceTable
from geo_copilot.platform.workspace import context as wsctx
from geo_copilot.platform.workspace import ops
from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

WS = "ws_0123456789abcdef"


def _ref(ds: str, nombre: str, tipo: str, n: int) -> LayerRef:
    return LayerRef(id=ds, name=nombre, kind="vector", provider="core", crs="EPSG:4326", geometry_type=tipo,
                    feature_count=n, fields=[FieldInfo(name="tipo", type="string")],
                    storage=WorkspaceTable(schema_name=WS, table=ds.replace("ds_", "d_")),
                    provenance=Provenance(capability="core.query_data", produced_at=datetime.now(UTC)))


LOTES = _ref("ds_aaaaaaaaaaaaaaaa", "Lotes Manzana 004503004", "Polygon", 30)
VIAS = _ref("ds_bbbbbbbbbbbbbbbb", "Vías", "LineString", 12)
VIAS21 = _ref("ds_cccccccccccccccc", "Vías 2021", "LineString", 9)

CAPAS = [
    {"id": "layer-2", "name": "Vías", "geometry_type": "LineString", "feature_count": 12, "dataset_id": VIAS.id,
     "fields": ["tipo"], "visible": True, "is_active": False},
    {"id": "layer-3", "name": "Vías 2021", "geometry_type": "LineString", "feature_count": 9, "dataset_id": VIAS21.id,
     "fields": ["tipo"], "visible": True, "is_active": False},
    {"id": "layer-1", "name": "Lotes Manzana 004503004", "geometry_type": "Polygon", "feature_count": 30,
     "dataset_id": LOTES.id, "fields": ["lotcodigo"], "visible": True, "is_active": True},
]


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(list_datasets=AsyncMock(return_value=[LOTES, VIAS, VIAS21])))
    async def buffer(_s, _w, ds, metros, *, disolver=False, nombre=None):
        return ops.Resultado(_ref("ds_dddddddddddddddd", nombre or "Buffer", "MultiPolygon", 1),
                             {"operacion": "buffer", "dataset": ds, "metros": metros, "disuelto": disolver})

    monkeypatch.setattr(ops, "buffer", buffer)


async def _loop(query: str, menciones: list[dict]) -> list[tuple[str, dict]]:
    from geo_copilot.orchestrator.nodes import agent_loop

    graph = MagicMock()
    graph.llm = await get_real_llm_or_skip()
    graph.agent_metrics = None
    llamadas: list[tuple[str, dict]] = []
    original = agent_loop.dispatch_tool

    async def espia(g, working, name, args):
        llamadas.append((name, dict(args or {})))
        return await original(g, working, name, args)

    agent_loop.dispatch_tool = espia
    try:
        await agent_loop.run(graph, {"query": query, "session_id": "sess-fh4",
                                     "map_context": {"layers": CAPAS, "menciones": menciones}})
    finally:
        agent_loop.dispatch_tool = original
    print(f"\n[FH.4] {query!r} {menciones} -> {llamadas}")
    return llamadas


def _capa_del_buffer(llamadas: list[tuple[str, dict]]) -> str | None:
    for nombre, args in llamadas:
        if nombre == "ws_buffer":
            return str(args.get("dataset")).strip("[]")
        if nombre == "spatial_operation":
            return str(args.get("target_layer_id")).strip("[]")
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(("texto", "capa", "ds"), [
    ("@Vías buffer 100 m", "layer-2", VIAS.id),
    ("@Vías 2021 buffer 100 m", "layer-3", VIAS21.id),
])
async def test_la_mencion_fija_la_capa(workspace, texto, capa, ds):
    mencion = {"tipo": "capa", "layer_id": capa, "texto": texto.split(" buffer")[0]}
    elegida = _capa_del_buffer(await _loop(texto, [mencion]))
    assert elegida in (capa, ds), elegida
