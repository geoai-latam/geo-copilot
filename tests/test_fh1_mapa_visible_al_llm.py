"""FH.1 — lo que el usuario ve en el mapa, y lo que pasó en él, lo ve el LLM.

El bloque de `format_map_context` es el que leen el router y el bucle ReAct.
"""
from __future__ import annotations

import pytest

from geo_copilot.core.formatters import format_map_context

MAPA = {
    "layers": [
        {"id": "raster-1", "name": "NDVI", "kind": "raster-xyz", "visible": True, "opacity": 0.4},
        {"id": "layer-2", "name": "Lotes", "kind": "vector-geojson", "geometry_type": "Polygon",
         "feature_count": 30, "fields": ["area_m2"], "visible": True, "is_active": True, "opacity": 1,
         "style": {"symbology_type": "graduated_colors", "classification_field": "area_m2",
                   "class_breaks": [{"label": "0-100", "color": "#fee"}, {"label": "100-355", "color": "#a00"}]},
         "label_field": "lotcodigo"},
    ],
    "acciones": [
        {"op": "set_style", "layer_id": "layer-2", "layer_name": "Lotes", "author": "agent",
         "at": "2026-09-25T10:00:00Z", "args": {"symbology_type": "graduated_colors"}, "undone": True},
        {"op": "reorder", "layer_id": "raster-1", "layer_name": "NDVI", "author": "user",
         "at": "2026-09-25T10:01:00Z", "args": {"to": "top"}},
    ],
}


def test_el_aspecto_de_cada_capa_llega_al_llm():
    txt = format_map_context(MAPA)
    assert "la primera queda ABAJO" in txt
    assert "opacidad 40 %" in txt
    assert "estilo graduated_colors por «area_m2», 2 clase(s): 0-100=#fee, 100-355=#a00" in txt
    assert "etiquetas con «lotcodigo»" in txt
    # sin opacidad reducida, no se menciona
    assert txt.count("opacidad") == 1


def test_las_acciones_desde_el_ultimo_turno_llegan_con_autor_y_si_se_deshicieron():
    txt = format_map_context(MAPA)
    assert "ACCIONES EN EL MAPA DESDE TU ÚLTIMA RESPUESTA" in txt
    assert "TÚ (el agente) cambió el estilo de «Lotes»" in txt
    assert "el USUARIO lo DESHIZO después (Ctrl+Z)" in txt
    assert "el USUARIO cambió el orden de dibujo de «NDVI» (to=top)" in txt


def test_sin_acciones_no_hay_bloque():
    assert "ACCIONES" not in format_map_context({"layers": MAPA["layers"]})


async def test_el_seguimiento_ve_la_conversacion_y_el_mapa():
    """V5 FH.1: «¿qué pasó con los colores que te pedí?» por follow_up respondía «no veo
    ninguna instrucción previa sobre colores»: ese camino no recibía ni historial ni mapa."""
    from geo_copilot.agents.insights_agent.agent import InsightsAgent
    from geo_copilot.core.llm_client import LLMResponse

    capturado: dict = {}

    class LLM:
        async def chat(self, messages, **_):
            capturado["prompt"] = "\n".join(m.content for m in messages)
            return LLMResponse(content="ok", model="fake")

    agente = InsightsAgent.__new__(InsightsAgent)
    agente.llm_client = LLM()
    await agente.handle_follow_up(
        query="¿qué pasó con los colores que te pedí?",
        conversation_history=[{"role": "user", "content": "colorea cada lote según su lotcodigo"},
                              {"role": "assistant", "content": "He coloreado cada lote."}],
        map_context=MAPA,
    )
    p = capturado["prompt"]
    assert "colorea cada lote según su lotcodigo" in p
    assert "el USUARIO lo DESHIZO" in p


@pytest.mark.asyncio
async def test_fh9_pedir_en_el_mapa_termina_el_turno_con_su_orden():
    from geo_copilot.orchestrator.capabilities_mapa import _pedir_en_el_mapa

    working = {"map_context": {"layers": [{"id": "layer-1", "name": "Lotes"}]}, "map_commands": []}
    out = await _pedir_en_el_mapa(None, working, {"mode": "pick_point", "prompt": "Marca el punto"})
    assert out.is_final and out.final_text == "Marca el punto"
    assert out.delta["map_commands"][-1] == {"op": "request_input", "layer_id": None, "reason": None,
                                             "args": {"mode": "pick_point", "prompt": "Marca el punto"}}
    # elegir en un mapa sin capas: no hay qué elegir (el LLM lo pregunta de otro modo)
    vacio = await _pedir_en_el_mapa(None, {"map_context": {"layers": []}}, {"mode": "pick_layer", "prompt": "¿Cuál?"})
    assert not vacio.success and not vacio.is_final
    malo = await _pedir_en_el_mapa(None, working, {"mode": "adivina", "prompt": "x"})
    assert not malo.success


def test_fh9_la_respuesta_en_el_mapa_es_un_hecho_para_el_agente():
    from geo_copilot.core.formatters import format_map_context

    capa = {"id": "layer-1", "name": "Lotes", "seleccion": {"count": 3}}
    punto = format_map_context({"layers": [capa], "clicked_point": {"lon": -74.05, "lat": 4.72},
                                "respuesta_mapa": {"modo": "pick_point", "pedido": "Marca el punto"}})
    assert "RESPUESTA DEL USUARIO EN EL MAPA a tu pedido «Marca el punto»: marcó un punto: lon -74.050000" in punto
    elementos = format_map_context({"layers": [capa], "respuesta_mapa": {
        "modo": "pick_features", "pedido": "Elige los lotes", "layer_id": "layer-1"}})
    assert "seleccionó 3 elemento(s) en [layer-1] «Lotes» (referencia `seleccion`)" in elementos
    cancelado = format_map_context({"layers": [capa], "respuesta_mapa": {
        "modo": "draw_area", "pedido": "Dibuja la zona", "cancelado": True}})
    assert "CANCELÓ" in cancelado and "No lo vuelvas a pedir" in cancelado


def test_deshacer_un_reestilo_dice_a_que_estilo_volvio_la_capa():
    """EH.6 (V5): sin esto, tras Ctrl+Z el agente no sabía qué se deshizo y lo adivinaba mal."""
    txt = format_map_context({"layers": MAPA["layers"], "acciones": [{
        "op": "set_style", "layer_id": "l1", "layer_name": "Lotes", "author": "agent", "undone": True,
        "at": "2026-09-26T19:23:00Z",
        "args": {"estilo": "graduated_colors por lotupredia, 6 clases, Purples",
                 "antes": "graduated_colors por lotupredia, 7 clases, Purples"}}]})
    assert "estilo=graduated_colors por lotupredia, 6 clases, Purples" in txt
    assert "esa capa volvió a su estilo anterior (graduated_colors por lotupredia, 7 clases, Purples)" in txt


def test_cada_capa_dice_de_que_pregunta_salio():
    """V5 EH.9: «… · spatial_operation» no decía qué tenía y el agente la describió al revés."""
    txt = format_map_context({"layers": [{
        "id": "layer-6", "name": "Lotes Manzana 008510017 · spatial_operation", "kind": "vector-geojson",
        "geometry_type": "Polygon", "feature_count": 1, "fields": ["lotcodigo"], "visible": True,
        "origin": {"capability": "core.follow_up", "arguments": {"query": "¿qué lote tiene el lotupredia más alto?"}}}]})
    assert "origen core.follow_up (resultado de «¿qué lote tiene el lotupredia más alto?»)" in txt


def test_el_punto_marcado_solo_aparece_cuando_existe():
    """Revisión (TH.16): una línea «PUNTO MARCADO: ninguno» en CADA contexto degradó tareas que no
    tenían nada que ver con «aquí» (LLM real 5/8 vs 8/8): la ausencia va en la referencia `punto`."""
    capas = MAPA["layers"]
    assert "PUNTO MARCADO" not in format_map_context({"layers": capas})
    con = format_map_context({"layers": capas, "clicked_point": {"lon": -74.31, "lat": 4.99}})
    assert "PUNTO MARCADO por el usuario en el mapa (su «aquí»): lon -74.310000, lat 4.990000" in con
