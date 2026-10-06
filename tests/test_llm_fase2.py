"""Fase 2 con LLM REAL (marker `llm`, V4): las decisiones del agente sobre el workspace.

Qué se juzga (el LLM decide; el código solo da hechos):
  - elige la capacidad determinista `ws_*` correcta, con los parámetros del texto;
  - narra las cifras que devolvió la herramienta, no otras;
  - el SQL que cruza la BD con un resultado previo usa la tabla del workspace y
    no mezcla SRID (H14).

La geometría está mockeada (la corrección de ops.* se prueba en PostGIS real en
test_workspace_ops.py); aquí solo importa el juicio.

Ejecutar: pytest -m llm tests/test_llm_fase2.py -s   (y leer las respuestas)
"""

from __future__ import annotations

import re
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


def _ref(ds: str, nombre: str, tipo: str, n: int, campos: list[str]) -> LayerRef:
    return LayerRef(
        id=ds, name=nombre, kind="vector", provider="core", crs="EPSG:4326",
        geometry_type=tipo, feature_count=n,
        fields=[FieldInfo(name=c, type="string") for c in campos],
        storage=WorkspaceTable(schema_name=WS, table=ds.replace("ds_", "d_")),
        provenance=Provenance(capability="core.query_data", produced_at=datetime.now(UTC)),
    )


VIAS = _ref("ds_aaaaaaaaaaaaaaaa", "Vías principales", "LineString", 12, ["nombre", "tipo"])
LOTES = _ref("ds_bbbbbbbbbbbbbbbb", "Lotes Manzana 004503009", "Polygon", 4, ["lotcodigo"])
BUFFER = _ref("ds_cccccccccccccccc", "Buffer 500 m de Lotes Manzana 004503009", "MultiPolygon", 1, [])


@pytest.fixture
def workspace(monkeypatch):
    store = SimpleNamespace(
        list_datasets=AsyncMock(return_value=[VIAS, LOTES]),
        to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": []}),
    )
    monkeypatch.setattr(wsctx, "_store", store)
    return store


async def _loop(query: str) -> tuple[list[tuple[str, dict]], str]:
    from geo_copilot.orchestrator.nodes import agent_loop

    llm = await get_real_llm_or_skip()
    graph = MagicMock()
    graph.llm = llm
    graph.agent_metrics = None
    llamadas: list[tuple[str, dict]] = []
    # agent_loop importa dispatch_tool por nombre: se espía ahí.
    original = agent_loop.dispatch_tool

    async def espia(g, working, name, args):
        llamadas.append((name, dict(args or {})))
        return await original(g, working, name, args)

    agent_loop.dispatch_tool = espia
    try:
        out = await agent_loop.run(graph, {"query": query, "session_id": "sess-v4"})
    finally:
        agent_loop.dispatch_tool = original
    texto = out.get("final_response") or ""
    print(f"\n[V4] {query!r}\n  herramientas={llamadas}\n  respuesta={texto!r}")
    return llamadas, texto


@pytest.mark.asyncio
async def test_buffer_metrico_usa_ws_buffer_y_narra_su_area(workspace, monkeypatch):
    monkeypatch.setattr(ops, "buffer", AsyncMock(return_value=ops.Resultado(
        _ref("ds_dddddddddddddddd", "Buffer 500 m de Vías principales", "MultiPolygon", 1, []),
        {"operacion": "buffer", "metros": 500.0, "disuelto": True, "area_total_m2": 2785398.16,
         "area_total_ha": 278.5398, "elementos": 1},
    )))
    llamadas, texto = await _loop(
        "haz un buffer de 500 m a las vías principales, disuelto, y dime el área total en hectáreas"
    )
    buffers = [a for n, a in llamadas if n == "ws_buffer"]
    assert buffers, llamadas
    assert buffers[0]["dataset"] in (VIAS.id,)
    assert float(buffers[0]["meters"]) == 500 and buffers[0]["dissolve"] is True
    # narra la cifra que DEVOLVIÓ la herramienta (278,54 ha), no otra
    assert re.search(r"278[.,]5", texto), texto


@pytest.mark.asyncio
async def test_area_total_usa_ws_measure(workspace, monkeypatch):
    monkeypatch.setattr(ops, "medir", AsyncMock(return_value=ops.Resultado(None, {
        "dataset": LOTES.name, "elementos": 4, "geometria": "Polygon",
        "area_total_m2": 6843.21, "area_total_ha": 0.6843, "suma_areas_individuales_m2": 6843.21,
    })))
    llamadas, texto = await _loop("¿cuál es el área total de los lotes de la manzana 004503009 en m²?")
    medidas = [a for n, a in llamadas if n == "ws_measure"]
    assert medidas and medidas[0]["dataset"] == LOTES.id, llamadas
    assert re.search(r"6[.,]?843", texto), texto


@pytest.mark.asyncio
async def test_sql_cruzado_usa_la_tabla_del_workspace_sin_mezclar_srid():
    """El caso H14: 'construcciones dentro de ese buffer' con el buffer en el workspace."""
    from geo_copilot.agents.gis_agent import sql_ast_validator as ast_v
    from geo_copilot.agents.gis_agent.agent import GISAgent
    from geo_copilot.platform.workspace.context import bloque_para_llm

    llm = await get_real_llm_or_skip()
    esquema = (
        'TABLA catastro.construcciones: | GEOMETRIA: "shape" (MULTIPOLYGON)\n'
        '  Columnas: "objectid" (int4), "concodigo" (varchar), "connpisos" (int4), "lotecodigo" (varchar)\n\n'
        'TABLA catastro.lotes: | GEOMETRIA: "shape" (MULTIPOLYGON)\n'
        '  Columnas: "objectid" (int4), "lotcodigo" (varchar), "manzcodigo" (varchar)\n'
    ) + bloque_para_llm([LOTES, BUFFER])
    sql = await GISAgent(llm_client=llm).generate_sql_from_query(
        "¿cuántas construcciones del catastro caen dentro de ese buffer de 500 m?", esquema,
        conversation_history=[
            {"role": "user", "content": "haz un buffer de 500 m a esos lotes, disuelto"},
            {"role": "assistant", "content": "Creé 'Buffer 500 m de Lotes Manzana 004503009' (97,42 ha)."},
        ],
    )
    print(f"\n[V4] SQL cruzado:\n{sql}")
    assert sql, "no generó SQL"
    assert f"{WS}.d_cccccccccccccccc".lower() in sql.lower(), "no usó la tabla del buffer del workspace"
    assert re.search(r"count\s*\(", sql, re.IGNORECASE), "la pregunta es cuántas: debe contar"
    ast_v.fijar_srid_de_columnas(4326)
    try:
        r = ast_v.analizar(sql)
    finally:
        ast_v.fijar_srid_de_columnas(None)
    assert not any("SRID" in m or "unidades" in m for m in r["motivos"]), r["motivos"]


@pytest.mark.asyncio
async def test_la_simbologia_decide_la_convencion_lisa_sin_que_el_codigo_la_imponga():
    """T3.0: la paleta de un resultado LISA la elige el LLM (conocimiento en su
    prompt), no una regla. El usuario solo pide "muéstrame los clusters"."""
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    llm = await get_real_llm_or_skip()
    clases = ["HH"] * 30 + ["LL"] * 12 + ["HL"] * 8 + ["LH"] * 6 + ["ns"] * 150
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[i, 0], [i + 1, 0], [i + 1, 1], [i, 0]]]},
         "properties": {"connpisos": i % 6, "lisa_clase": c, "lisa_p": 0.01}}
        for i, c in enumerate(clases)
    ]}
    resp = await SymbologyAgent(llm_client=llm).process(
        "¿hay agrupamiento del número de pisos? muéstrame los clusters LISA", {"geojson": fc},
    )
    assert resp.success, resp.message
    s = resp.data
    colores = {b["label"]: b["color"].lower() for b in s.get("class_breaks") or []}
    tipo = getattr(s.get("symbology_type"), "value", s.get("symbology_type"))
    print(f"\n[V4] simbologia LISA: {tipo} por {s.get('classification_field')}: {colores}")
    assert tipo == "unique_values" and s.get("classification_field") == "lisa_clase"

    def rgb(h):
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))

    r, g, b = rgb(colores["HH"])
    assert r > 150 and r > g + 60 and r > b + 60, "HH debería leerse como rojo"
    r, g, b = rgb(colores["LL"])
    assert b > 120 and b > r + 40, "LL debería leerse como azul"
    r, g, b = rgb(colores["ns"])
    assert max(r, g, b) - min(r, g, b) < 40, "ns debería ser neutro (gris)"


@pytest.mark.asyncio
@pytest.mark.parametrize(("pedido", "filtra"), [
    ("trae los lotes de la manzana 002412028", False),
    ("trae los lotes que se ven en esta zona", True),
])
async def test_la_zona_visible_filtra_solo_si_se_pide(pedido, filtra):
    """V5 F3: con el mapa en otra parte, «los lotes de la manzana X» devolvía 0 filas
    porque el SQL filtraba por la zona visible sin que el usuario la pidiera."""
    from geo_copilot.agents.gis_agent.agent import GISAgent
    from geo_copilot.orchestrator.nodes.gis_agent import bloque_zona_visible

    llm = await get_real_llm_or_skip()
    esquema = (
        'TABLA catastro.lotes: | GEOMETRIA: "shape" (MULTIPOLYGON)\n'
        '  Columnas: "objectid" (int4), "lotcodigo" (varchar), "manzcodigo" (varchar)\n'
    ) + bloque_zona_visible([-74.0938, 4.6376, -74.0062, 4.6824])
    sql = await GISAgent(llm_client=llm).generate_sql_from_query(pedido, esquema)
    print(f"\n[V4] {pedido!r}:\n{sql}")
    assert sql, "no generó SQL"
    assert ("st_makeenvelope" in sql.lower()) is filtra, sql
