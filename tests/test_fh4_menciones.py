"""FH.4 — menciones `@`: viajan como referencias y el LLM las ve resueltas (el qué hacer lo decide él)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from geo_copilot.api.models import MapContext
from geo_copilot.core.formatters import format_map_context

MAPA = {
    "layers": [
        {"id": "layer-1", "name": "Lotes", "dataset_id": "ds_aaaaaaaaaaaaaaaa", "fields": ["area_m2"],
         "seleccion": {"ids": [1, 2], "count": 2, "origin": "box"}},
        {"id": "layer-2", "name": "Vías", "fields": ["tipo"]},
        {"id": "layer-3", "name": "Vías 2021", "fields": ["tipo"]},
    ],
}


def test_el_contrato_acepta_menciones_y_rechaza_tipos_inventados():
    mc = MapContext.model_validate({**MAPA, "menciones": [{"tipo": "capa", "layer_id": "layer-2", "texto": "@Vías"}]})
    assert mc.menciones[0].layer_id == "layer-2"
    with pytest.raises(ValidationError):
        MapContext.model_validate({**MAPA, "menciones": [{"tipo": "vista", "layer_id": "x", "texto": "@x"}]})


def test_el_llm_ve_cada_mencion_resuelta_a_su_capa_exacta():
    txt = format_map_context({**MAPA, "menciones": [
        {"tipo": "capa", "layer_id": "layer-2", "texto": "@Vías"},
        {"tipo": "seleccion", "layer_id": "layer-1", "texto": "@selección"},
        {"tipo": "campo", "layer_id": "layer-1", "texto": "@Lotes.area_m2", "campo": "area_m2"},
        {"tipo": "capa", "layer_id": "layer-9", "texto": "@Viejo"},
    ]})
    assert "MENCIONES DEL USUARIO EN ESTE MENSAJE" in txt
    assert '«@Vías» → la capa [layer-2] "Vías"' in txt  # no «Vías 2021»: el usuario eligió esta
    assert '«@selección» → [seleccion]: los 2 elemento(s) seleccionados en [layer-1] "Lotes" (dataset ds_aaaaaaaaaaaaaaaa)' in txt
    assert '«@Lotes.area_m2» → el campo «area_m2» de [layer-1]' in txt
    assert "«@Viejo» → (esa capa ya no está en el mapa)" in txt


def test_sin_menciones_no_hay_bloque():
    assert "MENCIONES" not in format_map_context(MAPA)


# ---------------------------------------------------------------------------
# V5 FH.4: el seguimiento no mide; si el LLM juzga que hay que calcular, pasa el turno
# ---------------------------------------------------------------------------


class _LLM:
    def __init__(self, contenido: str):
        self.contenido = contenido
        self.prompts: list[str] = []

    async def chat(self, mensajes, **_kw):
        from types import SimpleNamespace

        self.prompts.append(mensajes[-1].content)
        return SimpleNamespace(content=self.contenido)


@pytest.mark.asyncio
async def test_follow_up_que_necesita_calcular_lo_dice_en_vez_de_inventar():
    from geo_copilot.agents.insights_agent.agent import InsightsAgent

    llm = _LLM('{"necesita_ejecutar": true, "motivo": "sumar el área de los 3 lotes seleccionados", "respuesta": ""}')
    out = await InsightsAgent(llm_client=llm).handle_follow_up(
        "¿cuánto suman en hectáreas?", previous_results=[{"lotcodigo": f"L{i}"} for i in range(4)])
    assert out["necesita_ejecutar"] is True and "3 lotes" in out["motivo_ejecutar"]
    assert "final_response" not in out
    # el hecho que se le da: una MUESTRA (no los datos para un total)
    assert "MUESTRA de 3 de esas 4 fila(s)" in llm.prompts[0]


@pytest.mark.asyncio
async def test_follow_up_con_la_cifra_ya_dicha_responde_texto():
    from geo_copilot.agents.insights_agent.agent import InsightsAgent

    llm = _LLM('{"necesita_ejecutar": false, "motivo": "", "respuesta": "Eran 30 lotes."}')
    out = await InsightsAgent(llm_client=llm).handle_follow_up("¿cuántos eran?")
    assert out["final_response"] == "Eran 30 lotes." and not out.get("necesita_ejecutar")


@pytest.mark.asyncio
async def test_el_nodo_pasa_el_turno_al_bucle_react(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.orchestrator.nodes import insights as nodo

    graph = SimpleNamespace(insights_agent=SimpleNamespace(handle_follow_up=AsyncMock(
        return_value={"necesita_ejecutar": True, "motivo_ejecutar": "sumar áreas"})))
    bucle = AsyncMock(return_value={"final_response": "Suman 0,18 ha."})
    monkeypatch.setattr(agent_loop, "run", bucle)
    monkeypatch.setattr("geo_copilot.orchestrator.graph._resolve_react_policy", lambda _s: "hybrid")
    out = await nodo.run(graph, {"intent": "follow_up", "query": "¿cuánto suman?"})
    assert out["final_response"] == "Suman 0,18 ha."
    assert bucle.await_args.args[1]["intent"] == "analyze"
    assert bucle.await_args.args[1]["interpretacion_previa"] == "sumar áreas"  # la lectura del seguimiento


def test_el_llm_ve_si_el_chip_de_la_seleccion_quedo_o_se_quito():
    con = format_map_context({**MAPA, "alcance_seleccion": True})
    assert ("ALCANCE QUE EL USUARIO DEJÓ PUESTO AL ENVIAR: sobre su cuadro de texto estaba el chip "
            "«2 seleccionados de Lotes» ([seleccion]); podía quitarlo y no lo hizo.") in con
    capas = [{k: v for k, v in c.items() if k != "seleccion"} for c in MAPA["layers"]]
    sin = format_map_context({"layers": capas, "seleccion_excluida":
                              {"layer_id": "layer-1", "layer_name": "Lotes", "count": 2}})
    assert "el usuario QUITÓ para este mensaje el chip «2 seleccionados de Lotes»" in sin
    assert "«sin selección: Lotes entera» ([layer-1])" in sin
    assert MapContext.model_validate({"seleccion_excluida": {"layer_id": "l", "layer_name": "L", "count": 3}})
