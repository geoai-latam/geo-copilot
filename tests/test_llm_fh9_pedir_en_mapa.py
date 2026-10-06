"""FH.9 con LLM REAL: «¿qué hay cerca?» sin punto, dibujo ni selección → el agente PIDE un punto en
el mapa (request_map_input) en vez de adivinar; con la respuesta del usuario (el punto) sigue sin
volver a pedirlo. Nada lo fuerza: el router dice `clarify` y el bucle decide cómo resolverlo.

Ejecutar: pytest -m llm tests/test_llm_fh9_pedir_en_mapa.py -s
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

CAPA = {"id": "layer-1", "name": "Lotes Manzana 008510017", "kind": "vector-geojson", "geometry_type": "Polygon",
        "feature_count": 27, "fields": ["lotcodigo", "area_m2"], "visible": True, "is_active": True}
PREGUNTA_ROUTER = "¿Cerca de qué lugar quieres que busque, y qué tipo de elementos te interesan?"


async def _turno(monkeypatch, map_context: dict) -> tuple[list[tuple[str, dict]], dict]:
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.platform.capabilities import ToolOutcome

    graph = MagicMock()
    graph.llm = await get_real_llm_or_skip()
    graph.agent_metrics = None
    llamadas: list[tuple[str, dict]] = []
    original = agent_loop.dispatch_tool

    async def espia(g, working, name, args):
        llamadas.append((name, dict(args or {})))
        if name == "request_map_input":
            return await original(g, working, name, args)  # la real: termina el turno con su orden
        return ToolOutcome(f"{name}: 4 construcciones a menos de 100 m del punto", success=True)

    monkeypatch.setattr(agent_loop, "dispatch_tool", espia)
    out = await agent_loop.run(graph, {"query": "¿qué hay cerca?", "session_id": "sess-fh9", "intent": "clarify",
                                       "final_response": PREGUNTA_ROUTER, "map_context": map_context})
    print(f"\n[FH.9] {llamadas} -> {out.get('final_response')!r} cmds={out.get('map_commands')}")
    return llamadas, out


@pytest.mark.asyncio
async def test_sin_donde_pide_un_punto_en_el_mapa(monkeypatch):
    llamadas, out = await _turno(monkeypatch, {"layers": [CAPA]})
    pedidos = [a for n, a in llamadas if n == "request_map_input"]
    assert pedidos and pedidos[0]["mode"] in ("pick_point", "draw_area"), llamadas
    cmd = (out.get("map_commands") or [])[-1]
    assert cmd["op"] == "request_input" and cmd["args"]["prompt"] == out["final_response"]


@pytest.mark.asyncio
async def test_con_el_punto_respondido_sigue_sin_volver_a_pedirlo(monkeypatch):
    mc = {"layers": [CAPA], "clicked_point": {"lon": -74.0521, "lat": 4.7208},
          "respuesta_mapa": {"modo": "pick_point", "pedido": "Marca en el mapa el punto de referencia"}}
    llamadas, out = await _turno(monkeypatch, mc)
    assert "request_map_input" not in [n for n, _ in llamadas], llamadas
    # La pregunta ofrecida era DÓNDE *y* QUÉ; el usuario respondió solo el dónde. Usar el punto con
    # una herramienta o preguntar solo el QUÉ (sin volver a pedir el lugar) cumplen FH.9. Antes el
    # test exigía una herramienta y fallaba también en `main` (4/4, auditoría pre-producción): el
    # agente preguntaba qué buscar citando el punto ya marcado, que es lo correcto.
    texto = str(out.get("final_response") or "").lower()
    if not llamadas:
        import re

        vuelve_a_pedir = re.search(r"\b(marca|señala|indica) (un|el|en el|otro) (punto|lugar|mapa)|¿(dónde|cerca de qué)", texto)
        pregunta_el_que = re.search(r"qué (tipo|elementos|buscas|te interesa|quieres)|sobre qué", texto)  # gpt-5.4: «sobre qué quieres buscar»
        assert not vuelve_a_pedir and pregunta_el_que, texto


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "capas", "pregunta"), [
    ("crúzalas", [{"id": "l1", "name": "Lotes", "geometry_type": "Polygon", "feature_count": 150, "visible": True},
                  {"id": "l2", "name": "Vías", "geometry_type": "LineString", "feature_count": 80, "visible": True},
                  {"id": "l3", "name": "Barrios", "geometry_type": "Polygon", "feature_count": 20, "visible": True}],
     "¿Qué dos capas quieres cruzar: Lotes, Vías o Barrios?"),
    ("mejóralo", [], "¿Qué quieres que mejore?"),
])
async def test_una_ambiguedad_genuina_se_pregunta_no_se_adivina(monkeypatch, query, capas, pregunta):
    """El `clarify` del router ahora pasa por el bucle en hybrid: debe seguir PREGUNTANDO (texto o
    en el mapa), nunca operar adivinando (A5)."""
    from geo_copilot.orchestrator.nodes import agent_loop

    graph = MagicMock()
    graph.llm = await get_real_llm_or_skip()
    graph.agent_metrics = None
    llamadas: list[str] = []
    original = agent_loop.dispatch_tool

    async def espia(g, working, name, args):
        llamadas.append(name)
        return await original(g, working, name, args) if name == "request_map_input" else None

    monkeypatch.setattr(agent_loop, "dispatch_tool", espia)
    out = await agent_loop.run(graph, {"query": query, "session_id": "sess-fh9b", "intent": "clarify",
                                       "final_response": pregunta, "map_context": {"layers": capas}})
    texto = out.get("final_response") or ""
    print(f"\n[FH.9 ambigua] {query!r} {llamadas} -> {texto!r}")
    assert set(llamadas) <= {"request_map_input"}, llamadas  # no operó adivinando
    assert "?" in texto or "¿" in texto or llamadas == ["request_map_input"], texto
