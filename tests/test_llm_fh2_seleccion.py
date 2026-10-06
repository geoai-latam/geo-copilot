"""FH.2 con LLM REAL: la selección compartida.

Qué se juzga (el LLM decide; el código solo da hechos):
  - con elementos seleccionados, «¿cuánto suman?» es un análisis sobre ellos, no
    una consulta nueva ni una aclaración;
  - «selecciona los lotes de más de N m²» es una orden `select` con la condición
    del texto (campo real de la capa, operador y valor), no ids inventados;
  - sobre lo seleccionado, las herramientas reciben la referencia `seleccion`.

Ejecutar: pytest -m llm tests/test_llm_fh2_seleccion.py -s   (y leer las respuestas)
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

LOTES = "layer-2"


def _lotes(n: int = 30) -> dict:
    """30 lotes cuadrados con `area_m2` = 100, 200, …, 3000."""
    feats = []
    for i in range(n):
        x, y = -74.08 + (i % 6) * 0.001, 4.6 + (i // 6) * 0.001
        feats.append({"type": "Feature", "properties": {"lotcodigo": f"L{i:03d}", "area_m2": (i + 1) * 100},
                      "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 0.0009, y], [x + 0.0009, y + 0.0009],
                                                                        [x, y + 0.0009], [x, y]]]}})
    return {"type": "FeatureCollection", "features": feats}


def _mapa(seleccion: dict | None = None) -> dict:
    capa = {"id": LOTES, "name": "Lotes Manzana 004503004", "kind": "vector-geojson", "geometry_type": "Polygon",
            "feature_count": 30, "fields": ["lotcodigo", "area_m2"], "visible": True, "is_active": True, "opacity": 1}
    if seleccion:
        capa["seleccion"] = seleccion
    return {"layers": [capa]}


DOCE = {"ids": list(range(12)), "count": 12, "origin": "lasso"}


@pytest.mark.asyncio
async def test_con_seleccion_cuanto_suman_es_analisis_no_consulta():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    ctx = {"schema_info": "Tablas: catastro.lotes", "active_data_source": "internal",
           "active_feature_count": 30, "map_context": _mapa(DOCE)}
    resp = await RouterAgent(llm_client=await get_real_llm_or_skip()).process(
        "¿cuánto suman en hectáreas?", context=ctx)
    data = resp.data or {}
    print(f"\n[FH.2 router] -> {data.get('intent')} · {data.get('reasoning') or ''}")
    await _mide_o_pasa_el_turno(data, "¿cuánto suman en hectáreas?", ctx)


async def _mide_o_pasa_el_turno(data: dict, pregunta: str, ctx: dict) -> None:
    """La suma de lo seleccionado no se ha medido: o el router elige un intent que MIDE, o
    elige `follow_up` y ese camino (que solo escribe texto) pide ejecutar — V5 FH.4: no la
    inventa. Así funciona el grafo: el seguimiento le pasa el turno al bucle ReAct."""
    if data.get("intent") in ("analyze", "spatial_operation"):
        return
    assert data.get("intent") == "follow_up", data
    from geo_copilot.agents.insights_agent.agent import InsightsAgent

    out = await InsightsAgent(llm_client=await get_real_llm_or_skip()).handle_follow_up(
        pregunta, conversation_history=ctx.get("conversation_history"), map_context=ctx.get("map_context"))
    print(f"  follow_up -> necesita_ejecutar={out.get('necesita_ejecutar')} {out.get('motivo_ejecutar')!r}")
    assert out.get("necesita_ejecutar") is True, out


async def _loop(query: str, seleccion: dict | None) -> tuple[list[tuple[str, dict]], str, dict]:
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.platform.seleccion import SELECCION, capa_virtual

    llm = await get_real_llm_or_skip()
    graph = MagicMock()
    graph.llm = llm
    graph.agent_metrics = None
    mapa = _mapa(seleccion)
    capas = {LOTES: {"data": _lotes(), "name": "Lotes Manzana 004503004"}}
    virtual = capa_virtual(capas, mapa)
    if virtual:
        capas[SELECCION] = virtual
    llamadas: list[tuple[str, dict]] = []
    original = agent_loop.dispatch_tool

    async def espia(g, working, name, args):
        llamadas.append((name, dict(args or {})))
        return await original(g, working, name, args)

    agent_loop.dispatch_tool = espia
    try:
        out = await agent_loop.run(graph, {"query": query, "session_id": "sess-fh2",
                                           "map_context": mapa, "map_layers": capas})
    finally:
        agent_loop.dispatch_tool = original
    texto = out.get("final_response") or ""
    print(f"\n[FH.2] {query!r}\n  herramientas={llamadas}\n  respuesta={texto!r}")
    return llamadas, texto, out


@pytest.mark.asyncio
async def test_seleccionar_por_condicion_es_una_orden_select_con_esa_condicion():
    llamadas, _texto, out = await _loop("selecciona los lotes de más de 2000 m²", None)
    ordenes = [a for n, a in llamadas if n == "map_command" and a.get("op") == "select"]
    assert ordenes, llamadas
    args = ordenes[-1].get("args") or {}
    where = args.get("where") or {}
    assert where.get("field") == "area_m2", args
    assert where.get("op") in (">", ">="), args
    assert float(where.get("value")) == 2000, args
    # el hecho que vuelve: cuántos cumplen (area_m2 > 2000 → 10 lotes: 2100…3000)
    emitidas = [c for c in (out.get("map_commands") or []) if c.get("op") == "select"]
    assert emitidas and emitidas[-1]["args"].get("count") in (10, 11), emitidas


@pytest.mark.asyncio
async def test_sobre_lo_seleccionado_las_herramientas_reciben_seleccion():
    """DoD FH.2: lazo sobre 12 lotes → «¿cuánto suman en hectáreas?» responde sobre esos 12.

    Los 12 primeros suman 100+…+1200 = 7800 m² = 0,78 ha; los 30, 46 500 m² = 4,65 ha.
    """
    llamadas, texto, _ = await _loop("¿cuánto suman en hectáreas los que tengo seleccionados?", DOCE)
    assert llamadas, texto
    # la referencia EXACTA (un texto que diga «seleccionados» no cuenta)
    refs = [v for _n, a in llamadas for v in a.values() if isinstance(v, str)]
    assert "seleccion" in refs, llamadas
    from geo_copilot.agents.gis_agent.sandbox import SANDBOX_AVAILABLE

    if SANDBOX_AVAILABLE:  # sin sandbox (Windows nativo) el cálculo no corre: se valida en V5
        assert "0,78" in texto or "0.78" in texto, texto
    assert "4,65" not in texto and "4.65" not in texto, texto


@pytest.mark.asyncio
async def test_capa_del_workspace_con_seleccion_mide_la_seleccion(monkeypatch):
    """V5 FH.2 (caso real): los lotes vienen del workspace (`ds_…`) y hay 8 seleccionados
    con la caja. «¿cuánto suman en hectáreas?» mide `seleccion`, no el dataset entero."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.platform import seleccion as selmod
    from geo_copilot.platform.workspace import context as wsctx
    from geo_copilot.platform.workspace import ops

    completo = {"dataset": "Lotes Manzana 004503004", "elementos": 30, "geometria": "Polygon",
                "area_total_m2": 6321.01, "area_total_ha": 0.6321, "suma_areas_individuales_m2": 6321.01}
    sel8 = {"dataset": "Selección de Lotes Manzana 004503004", "elementos": 8, "geometria": "Polygon",
            "area_total_m2": 1759.25, "area_total_ha": 0.1759, "suma_areas_individuales_m2": 1759.25}
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(list_datasets=AsyncMock(return_value=[])))
    monkeypatch.setattr(selmod, "dataset_de_seleccion", AsyncMock(return_value="ds_selsel00000000"))
    monkeypatch.setattr(ops, "medir", AsyncMock(side_effect=lambda _s, _w, ds: ops.Resultado(
        None, sel8 if ds == "ds_selsel00000000" else completo)))

    import geo_copilot.orchestrator.nodes.agent_loop as agent_loop

    llm = await get_real_llm_or_skip()
    graph = MagicMock()
    graph.llm = llm
    graph.agent_metrics = None
    capa = {"id": LOTES, "name": "Lotes Manzana 004503004", "kind": "vector-geojson", "geometry_type": "Polygon",
            "feature_count": 30, "dataset_id": "ds_d96b1e0f541d4363",
            "fields": ["objectid", "lotcodigo", "lotdispers", "lotildispe", "lotupredia", "manzcodigo", "lotdistrit"],
            "visible": True, "is_active": True, "opacity": 1,
            "seleccion": {"ids": [21, 14, 12, 0, 27, 17, 10, 8], "count": 8, "origin": "box"}}
    llamadas: list[tuple[str, dict]] = []
    original = agent_loop.dispatch_tool

    async def espia(g, working, name, args):
        llamadas.append((name, dict(args or {})))
        return await original(g, working, name, args)

    agent_loop.dispatch_tool = espia
    try:
        out = await agent_loop.run(graph, {"query": "¿cuánto suman en hectáreas?", "session_id": "sess-fh2",
                                           "map_context": {"layers": [capa]}, "map_layers": {}})
    finally:
        agent_loop.dispatch_tool = original
    texto = out.get("final_response") or ""
    print(f"\n[FH.2 ws] herramientas={llamadas}\n  respuesta={texto!r}")
    refs = [v for _n, a in llamadas for v in a.values() if isinstance(v, str)]
    assert "seleccion" in refs, llamadas
    assert re.search(r"0[.,]17[56]", texto), texto


@pytest.mark.asyncio
async def test_seleccion_hecha_tras_otra_respuesta_con_area():
    """V5 FH.4: el turno anterior entregó el área de unos buffers; luego el usuario
    seleccionó 3 lotes y preguntó «¿cuánto suman en hectáreas?»: el router eligió
    follow_up y la respuesta inventó «X hectáreas» sobre otra capa."""
    from geo_copilot.agents.router_agent.agent import RouterAgent

    mapa = {"layers": [
        {"id": "layer-1", "name": "Lotes Manzana 004503004", "kind": "vector-geojson", "geometry_type": "Polygon",
         "feature_count": 30, "dataset_id": "ds_aaaaaaaaaaaaaaaa", "fields": ["lotcodigo"], "visible": True,
         "is_active": False, "seleccion": {"ids": [0, 1, 2], "count": 3, "origin": "click"}},
        {"id": "layer-2", "name": "Lotes Manzana 004503009", "kind": "vector-geojson", "geometry_type": "Polygon",
         "feature_count": 4, "dataset_id": "ds_bbbbbbbbbbbbbbbb", "fields": ["lotcodigo"], "visible": True,
         "is_active": False},
        {"id": "layer-3", "name": "Buffer 20 m de Lotes Manzana 004503004", "kind": "vector-geojson",
         "geometry_type": "Polygon", "feature_count": 30, "dataset_id": "ds_cccccccccccccccc", "fields": [],
         "visible": True, "is_active": True},
    ], "acciones": [{"op": "select", "layer_id": "layer-1", "layer_name": "Lotes Manzana 004503004",
                     "args": {"origin": "click", "mode": "replace", "ids": 3}, "at": "2026-09-26T02:39:10Z",
                     "author": "user", "undone": False}],
            "alcance_seleccion": True}  # el chip estaba a la vista al enviar (como en el V5)
    historial = [
        {"role": "user", "content": "haz un buffer de 20 m a @Lotes Manzana 004503004"},
        {"role": "assistant", "content": "[Entregado al usuario en este turno: capa «Buffer 20 m de Lotes Manzana "
         "004503004» (30 elementos) · intención: spatial_operation] He creado un buffer de 20 metros alrededor de "
         "cada lote de la manzana 004503004. El área total combinada de estos buffers es aproximadamente 1.44 "
         "hectáreas."},
    ]
    ctx = {"schema_info": "Tablas: catastro.lotes", "active_data_source": "internal", "active_feature_count": 30,
           "map_context": mapa, "conversation_history": historial}
    resp = await RouterAgent(llm_client=await get_real_llm_or_skip()).process("¿cuánto suman en hectáreas?", context=ctx)
    data = resp.data or {}
    print(f"\n[FH.2/4 router] ->{data.get('intent')} · {data.get('reasoning') or ''}")
    await _mide_o_pasa_el_turno(data, "¿cuánto suman en hectáreas?", ctx)
