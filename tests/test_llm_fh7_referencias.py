"""FH.7 con LLM REAL: al nombrar un elemento concreto, la respuesta lo cita como enlace al
mapa con un valor que VIO en los datos, y el enlace resuelve al elemento correcto. Nada lo
fuerza: el LLM recibe la sintaxis (INSTRUCCION_REFERENCIAS) y decide.

Ejecutar: pytest -m llm tests/test_llm_fh7_referencias.py -s
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

# misma gramática que frontend/src/lib/referencias.ts
PATRON = re.compile(r"\[\[layer:([^\]|#?]+)(?:#([^\]|]+)|\?([^\]|=]+)=([^\]|]*))?(?:\|([^\]]*))?\]\]")

LOTES = [{"lotcodigo": f"0085100170{i:02d}", "area_m2": a} for i, a in
         enumerate([176.15, 699.44, 2483.22, 701.18, 348.36])]
MAYOR = LOTES[2]


def _feature(i: int, props: dict) -> dict:
    x = -74.05 + i * 1e-4
    return {"type": "Feature", "id": i, "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [[[x, 4.7], [x + 5e-5, 4.7], [x + 5e-5, 4.70005], [x, 4.7]]]}}


def _resuelve_al_mayor(texto: str, filas: list[dict]) -> bool:
    """Algún enlace señala EXACTAMENTE el lote mayor (por su valor o por su id/índice)."""
    for _capa, fid, campo, valor, _et in PATRON.findall(texto):
        if campo:
            hits = [f for f in filas if str(f.get(campo.strip())) == valor.strip()]
            if hits == [MAYOR]:
                return True
        if fid and fid.strip().isdigit() and filas[int(fid)] == MAYOR:
            return True
    return False


@pytest.mark.asyncio
async def test_el_narrador_enlaza_el_lote_mas_grande():
    from geo_copilot.orchestrator.nodes.insights import narrate_result

    graph = MagicMock()
    graph.llm = await get_real_llm_or_skip()
    fila = dict(MAYOR)
    state = {"query": "¿cuál es el lote más grande?", "raw_data": [fila], "layer_name": "Lote más grande",
             "geojson": {"type": "FeatureCollection", "features": [_feature(0, fila)]}}
    texto = await narrate_result(graph, state)
    print(f"\n[FH.7 narrador] {texto!r}")
    assert "2483" in texto.replace(".", "").replace(",", "") or "2.483" in texto
    assert _resuelve_al_mayor(texto, [fila]), texto


@pytest.mark.asyncio
async def test_el_seguimiento_enlaza_el_lote_de_la_capa_del_mapa():
    from geo_copilot.agents.insights_agent.agent import InsightsAgent

    llm = await get_real_llm_or_skip()
    capa = {"id": "layer-7", "name": "Lotes Manzana 008510017", "kind": "vector-geojson", "geometry_type": "Polygon",
            "feature_count": 5, "fields": ["lotcodigo", "area_m2"], "visible": True, "is_active": True}
    out = await InsightsAgent(llm_client=llm).handle_follow_up(
        query="¿y cuál de esos lotes es el más grande?", previous_sql=None, previous_results=LOTES,
        previous_geojson={"type": "FeatureCollection", "features": [_feature(i, p) for i, p in enumerate(LOTES)]},
        conversation_history=[{"role": "user", "content": "trae los lotes de la manzana 008510017 con su área"},
                              {"role": "assistant", "content": "Cargué 5 lotes con su área en m²."}],
        map_context={"layers": [capa]})
    texto = out.get("final_response") or ""
    print(f"\n[FH.7 seguimiento] {out.get('necesita_ejecutar')} {texto!r}")
    if out.get("necesita_ejecutar"):
        pytest.skip("el LLM decidió pasar el turno al bucle (legítimo: lo valida V5)")
    assert _resuelve_al_mayor(texto, LOTES), texto


@pytest.mark.asyncio
async def test_el_bucle_react_enlaza_el_lote_que_encontro_una_herramienta(monkeypatch):
    """V5 en Chrome: el bucle respondió bien («el lote 008510017059, 2483,22 m²») sin enlace."""
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.platform.capabilities import ToolOutcome

    graph = MagicMock()
    graph.llm = await get_real_llm_or_skip()
    graph.agent_metrics = None
    capa = {"id": "layer-1", "name": "Lotes Manzana 008510017", "kind": "vector-geojson", "geometry_type": "Polygon",
            "feature_count": 5, "fields": ["lotcodigo", "area_m2"], "visible": True, "is_active": True}
    from geo_copilot.orchestrator.react_tools import _filas_tabulares

    observacion = ("{tool}: 1 elemento(s). Ya es una capa con geometría en el mapa y queda como la capa activa: "
                   "para usarla en otra herramienta refiérete a ella como `activa`"
                   + _filas_tabulares({"geojson": {"type": "FeatureCollection", "features": [_feature(0, MAYOR)]}}))
    llamadas: list[str] = []

    async def espia(g, working, name, args):
        llamadas.append(name)
        return ToolOutcome(observacion.replace("{tool}", name), success=True)  # como la del ejecutor real

    monkeypatch.setattr(agent_loop, "dispatch_tool", espia)
    # Una TASA, no un sí/no: el bucle enlaza siempre la capa y casi siempre el elemento.
    # Se exige mayoría en 3 intentos.
    aciertos = 0
    for _ in range(3):
        out = await agent_loop.run(graph, {"query": "¿cuál es el lote más grande?", "session_id": "sess-fh7",
                                           "map_context": {"layers": [capa]}})
        texto = out.get("final_response") or ""
        print(f"\n[FH.7 ReAct] {llamadas[-1:]} {texto!r}")
        aciertos += _resuelve_al_mayor(texto, [MAYOR])
    assert aciertos >= 2, f"{aciertos}/3"


@pytest.mark.asyncio
async def test_si_el_usuario_filtro_despues_el_seguimiento_no_repite_la_cifra_de_antes():
    """V5 en Chrome (FH.7): la conversación decía «el mayor de los 27 es 059»; después el
    usuario filtró la capa (quedan 3, sin el 059) y preguntó «¿cuál es el lote más grande?».
    El seguimiento repitió 059. La capa filtrada ES su subconjunto: o calcula de nuevo o
    responde sobre los 3 que ve."""
    from geo_copilot.agents.insights_agent.agent import InsightsAgent

    llm = await get_real_llm_or_skip()
    capa = {"id": "layer-1", "name": "Lotes Manzana 008510017", "kind": "vector-geojson", "geometry_type": "Polygon",
            "feature_count": 27, "fields": ["lotcodigo", "lotupredia", "area_m2"], "visible": True, "is_active": True,
            "filtro": [{"field": "lotupredia", "op": ">", "value": 100}], "filtro_count": 3}
    mapa = {"layers": [capa], "acciones": [
        {"op": "set_filter", "layer_id": "layer-1", "author": "user", "args": {"where": "lotupredia > 100"}}]}
    out = await InsightsAgent(llm_client=llm).handle_follow_up(
        query="¿cuál es el lote más grande?", previous_sql=None, previous_results=None, previous_geojson=None,
        conversation_history=[
            {"role": "user", "content": "¿cuál es el lote más grande?"},
            {"role": "assistant", "content": "El lote más grande es el 008510017059, con 2483.22 m² (de los 27)."}],
        map_context=mapa)
    texto = out.get("final_response") or ""
    print(f"\n[FH.7 filtro después] ejecutar={out.get('necesita_ejecutar')} {texto!r}")
    assert out.get("necesita_ejecutar") or "059" not in texto, texto


@pytest.mark.asyncio
async def test_con_la_capa_filtrada_de_antes_el_seguimiento_responde_sobre_lo_que_queda():
    """V5 en Chrome, tal cual: el filtro ya estaba (no hay acciones recientes), la conversación
    dice 059 (el mayor de los 27, fuera del filtro). Con las filas de la capa (3, ya filtradas)
    como hecho, el mayor es el 011; o se calcula de nuevo. Nunca 059."""
    from geo_copilot.agents.insights_agent.agent import InsightsAgent

    llm = await get_real_llm_or_skip()
    quedan = [("008510017011", 139, 2098.19), ("008510017060", 163, 1447.28), ("008510017013", 101, 1398.88)]
    feats = [_feature(i, {"lotcodigo": c, "lotupredia": u, "area_m2": a}) for i, (c, u, a) in enumerate(quedan)]
    capa = {"id": "layer-1", "name": "Lotes Manzana 008510017", "kind": "vector-geojson", "geometry_type": "Polygon",
            "feature_count": 27, "fields": ["lotcodigo", "lotupredia", "area_m2"], "visible": True, "is_active": True,
            "filtro": [{"field": "lotupredia", "op": ">", "value": 100}], "filtro_count": 3}
    for _ in range(3):
        out = await InsightsAgent(llm_client=llm).handle_follow_up(
            query="¿cuál es el lote más grande?",
            conversation_history=[
                {"role": "user", "content": "¿cuál es el lote más grande?"},
                {"role": "assistant", "content": "El lote más grande es el 008510017059, con 2483.22 m²."}],
            map_context={"layers": [capa]},
            map_layers={"layer-1": {"name": capa["name"], "data": {"type": "FeatureCollection", "features": feats}}})
        texto = out.get("final_response") or ""
        print(f"\n[FH.7 filtro vigente] ejecutar={out.get('necesita_ejecutar')} {texto!r}")
        assert out.get("necesita_ejecutar") or ("059" not in texto and "011" in texto), texto
