"""V5 FH.4 con LLM REAL: el seguimiento no inventa cifras; si hay que medir, lo pide.

Ejecutar: pytest -m llm tests/test_llm_fh4_follow_up.py -s
"""
from __future__ import annotations

import re

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

MAPA = {"layers": [
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
                 "author": "user", "undone": False}], "alcance_seleccion": True}
HISTORIAL = [
    {"role": "user", "content": "haz un buffer de 20 m a @Lotes Manzana 004503004"},
    {"role": "assistant", "content": "He creado un buffer de 20 metros alrededor de cada lote de la manzana "
     "004503004. El área total combinada de estos buffers es aproximadamente 1.44 hectáreas."},
]
FILAS = [{"objectid": i, "lotcodigo": f"00450300900{i}", "manzcodigo": "004503009"} for i in range(4)]


async def _seguimiento(pregunta: str, historial=HISTORIAL, mapa=MAPA, filas=FILAS) -> dict:
    from geo_copilot.agents.insights_agent.agent import InsightsAgent

    out = await InsightsAgent(llm_client=await get_real_llm_or_skip()).handle_follow_up(
        pregunta, previous_results=filas, conversation_history=historial, map_context=mapa)
    print(f"\n[FH.4 seguimiento] {pregunta!r} -> {out.get('necesita_ejecutar')} "
          f"{out.get('motivo_ejecutar') or out.get('final_response')!r}")
    return out


@pytest.mark.asyncio
async def test_la_suma_de_la_seleccion_no_se_inventa():
    """El caso V5: respondió «**X hectáreas**» sobre otra manzana."""
    out = await _seguimiento("¿cuánto suman en hectáreas?")
    if out.get("necesita_ejecutar"):
        return  # lo mide el bucle ReAct
    texto = out["final_response"]
    assert not re.search(r"\bX\b", texto), texto  # ningún marcador
    # si responde con texto, solo puede usar la cifra ya dicha (la de los buffers)
    numeros = set(re.findall(r"\d+[.,]\d+", texto))
    assert numeros <= {"1.44", "1,44"}, texto


@pytest.mark.asyncio
async def test_una_cifra_ya_dicha_se_responde_sin_ejecutar():
    historial = [{"role": "user", "content": "¿cuántos lotes tiene la manzana 004503004?"},
                 {"role": "assistant", "content": "La manzana 004503004 tiene 30 lotes."}]
    out = await _seguimiento("¿cuántos eran?", historial=historial, mapa={"layers": []}, filas=None)
    assert not out.get("necesita_ejecutar"), out
    assert "30" in out["final_response"]


@pytest.mark.asyncio
async def test_sin_el_chip_de_seleccion_es_la_capa_entera_de_esa_seleccion():
    """V5 FH.4: tras medir los 3 seleccionados de 004503004, el usuario quita el chip y
    pregunta «¿y cuánto suman ahora?»: es 004503004 entera, no la última consulta (004503009)."""
    capas = [{k: v for k, v in c.items() if k != "seleccion"} for c in MAPA["layers"]]
    mapa = {"layers": capas, "seleccion_excluida": {"layer_id": "layer-1", "layer_name": "Lotes Manzana 004503004",
                                                    "count": 3}}
    historial = [*HISTORIAL,
                 {"role": "user", "content": "¿cuánto suman en hectáreas?"},
                 {"role": "assistant", "content": "La suma del área de los 3 lotes seleccionados de la manzana "
                  "004503004 es aproximadamente 0.061 hectáreas."}]
    out = await _seguimiento("¿y cuánto suman en hectáreas ahora?", historial=historial, mapa=mapa)
    texto = out.get("motivo_ejecutar") or out.get("final_response") or ""
    assert out.get("necesita_ejecutar"), out
    assert "004503004" in texto and "004503009" not in texto, texto
