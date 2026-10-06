"""FH.5 con LLM REAL: «filtra / deja solo los de …» termina en un filtro de capa (set_filter)
con la condición del texto, y lo que se pregunta después usa la capa filtrada.

Ejecutar: pytest -m llm tests/test_llm_fh5_filtros.py -s
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

LOTES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1 + i * 1e-3, 4.6]},
     "properties": {"lotcodigo": f"L{i:02d}", "estrato": [1, 2, 3][i % 3], "uso": ["res", "com", "ind"][i % 3]}}
    for i in range(30)]}
CAPA = {"id": "layer-1", "name": "Lotes Manzana 004503004", "kind": "vector-geojson", "geometry_type": "Point",
        "feature_count": 30, "fields": ["lotcodigo", "estrato", "uso"], "visible": True, "is_active": True}


async def _turno(query: str, capa: dict = CAPA, datos: dict = LOTES) -> tuple[list[tuple[str, dict]], dict]:
    """Router real + bucle ReAct real (el grafo manda ahí las peticiones con operaciones)."""
    from geo_copilot.agents.router_agent.agent import RouterAgent
    from geo_copilot.orchestrator.nodes import agent_loop

    llm = await get_real_llm_or_skip()
    mapa = {"layers": [capa]}
    r = await RouterAgent(llm_client=llm).process(query, context={
        "schema_info": "Tablas: catastro.lotes", "active_data_source": "internal", "active_feature_count": 30,
        "map_context": mapa})
    graph = MagicMock()
    graph.llm = llm
    graph.agent_metrics = None
    # Sin BD en esta prueba: si el modelo elige `query_database`, el nodo responde «No hay conexión a
    # BD» (como un despliegue sin base) y el bucle sigue con la capa del mapa. Con el MagicMock, db_pool
    # era verdadero y el nodo seguía con un agente falso: el turno fallaba por el arnés, no por el agente.
    graph.db_pool = None
    llamadas: list[tuple[str, dict]] = []
    original = agent_loop.dispatch_tool

    async def espia(g, working, name, args):
        llamadas.append((name, dict(args or {})))
        return await original(g, working, name, args)

    agent_loop.dispatch_tool = espia
    try:
        out = await agent_loop.run(graph, {"query": query, "session_id": "sess-fh5", "map_context": mapa,
                                           "map_layers": {"layer-1": {"data": datos, "name": CAPA["name"]}}})
    finally:
        agent_loop.dispatch_tool = original
    print(f"\n[FH.5] {query!r} router={(r.data or {}).get('intent')} -> {llamadas}\n  {out.get('final_response')!r}")
    return llamadas, out


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "campo", "valor"), [
    ("filtra los lotes de estrato 3", "estrato", 3),
    ("deja solo los de uso residencial", "uso", "res"),
])
async def test_filtrar_es_un_filtro_de_capa_con_la_condicion_del_texto(query, campo, valor):
    llamadas, out = await _turno(query)
    filtros = [a for n, a in llamadas if n == "map_command" and a.get("op") == "set_filter"]
    assert filtros, llamadas
    # si el primer valor no existía («residencial»), el hecho de qué valores hay le deja corregirlo
    where = (filtros[-1].get("args") or {}).get("where") or []
    def es(c: dict) -> bool:  # `= 'res'` o `in ['res']`: la misma condición
        v = c.get("value")
        return c.get("field") == campo and [str(x) for x in (v if isinstance(v, list) else [v])] == [str(valor)]

    assert any(es(c) for c in where), where
    emitida = [c for c in (out.get("map_commands") or []) if c.get("op") == "set_filter"][-1]
    assert emitida["args"]["count"] == 10


@pytest.mark.asyncio
async def test_con_la_capa_filtrada_a_mano_el_resumen_es_del_subconjunto():
    """DoD FH.5: el siguiente turno usa el filtro (retocado a mano): en memoria, los datos ya son el subconjunto."""
    from geo_copilot.platform.seleccion import filtrar_capa

    filtro = [{"field": "estrato", "op": "=", "value": 2}]
    capa = {**CAPA, "filtro": filtro, "filtro_count": 10}
    _llamadas, out = await _turno("¿cuántos lotes hay y de qué usos?", capa=capa, datos=filtrar_capa(LOTES, filtro))
    from geo_copilot.agents.gis_agent.sandbox import SANDBOX_AVAILABLE

    texto = (out.get("final_response") or "").lower()
    assert "10" in texto, texto  # sabe que la capa filtrada son 10 (puede mencionar los 30 de la capa entera)
    if SANDBOX_AVAILABLE:  # sin sandbox (Windows nativo) el conteo por uso no corre: se valida en V5
        assert "10" in texto and "com" in texto, texto
