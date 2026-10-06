"""FH.6 — el editor manual de simbología calcula con el MISMO código que el agente, y lo
que el usuario fija a mano llega al diseñador como hecho."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from geo_copilot.agents.symbology_agent.manual import clasificar_manual, rampas
from geo_copilot.agents.symbology_agent.styles import COLOR_PALETTES, ColorScheme
from geo_copilot.core.formatters import format_map_context

FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, 0]},
     "properties": {"area": (i + 1) * 10, "uso": ["res", "com", "ind"][i % 3]}} for i in range(30)]}


@pytest.mark.asyncio
async def test_graduado_con_la_rampa_elegida_y_sus_colores():
    e = await clasificar_manual(FC, {"symbology_type": "graduated_colors", "classification_field": "area",
                                     "classification_method": "quantile", "num_classes": 4, "color_scheme": "viridis"})
    assert e["symbology_type"] == "graduated_colors" and e["color_scheme"] == "viridis"
    assert len(e["class_breaks"]) == 4
    viridis = COLOR_PALETTES[ColorScheme("viridis")]
    assert all(b["color"] in viridis for b in e["class_breaks"])
    assert e["reasoning"] is None  # nadie razonó: lo eligió el usuario


@pytest.mark.asyncio
async def test_categorias_y_color_unico():
    cat = await clasificar_manual(FC, {"symbology_type": "unique_values", "classification_field": "uso",
                                       "color_scheme": "Set2"})
    assert {b["label"] for b in cat["class_breaks"]} >= {"res", "com", "ind"}
    uno = await clasificar_manual(FC, {"symbology_type": "single_symbol", "fill_color": "#ff8800"})
    assert uno["symbology_type"] == "single_symbol"
    assert (uno.get("marker") or uno.get("fill") or {}).get("color") == "#ff8800"


def test_las_rampas_son_las_del_agente():
    r = rampas()
    assert "viridis" in r and "Blues" in r and len(r) == len(COLOR_PALETTES)


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr("geo_copilot.api.routes.workspace.get_app_state", lambda: SimpleNamespace(dataset_store=None))
    from geo_copilot.api.app import create_app

    return TestClient(create_app())


def test_endpoint_calcula_el_estilo_y_solo_acepta_fijados_editables(cliente):
    r = cliente.post("/api/v1/workspace/sess-a/estilo", json={
        "diseno": {"symbology_type": "graduated_colors", "classification_field": "area",
                   "classification_method": "equal_interval", "num_classes": 3, "color_scheme": "Reds"},
        "geojson": FC, "titulo": "Lotes", "pinned": ["color_scheme", "cualquier_cosa"]})
    assert r.status_code == 200, r.text
    st = r.json()["style"]
    assert st["color_scheme"] == "Reds" and len(st["class_breaks"]) == 3 and st["layer_title"] == "Lotes"
    assert st["pinned"] == ["color_scheme"]
    assert cliente.get("/api/v1/workspace/rampas").json()["rampas"]["Reds"]
    assert cliente.post("/api/v1/workspace/sess-a/estilo", json={"diseno": {"symbology_type": "single_symbol"}}
                        ).status_code == 400


def test_el_llm_ve_el_estilo_actual_y_lo_fijado():
    from geo_copilot.agents.symbology_agent.agent import _hecho_estilo_actual
    from geo_copilot.orchestrator.nodes.symbology import _estilo_actual

    estilo = {"symbology_type": "graduated_colors", "classification_field": "area", "classification_method": "quantile",
              "num_classes": 5, "color_scheme": "viridis", "pinned": ["color_scheme"]}
    estado = {"target_layer_id": "l2", "map_context": {"layers": [
        {"id": "l1", "is_active": True, "style": {"symbology_type": "single_symbol"}},
        {"id": "l2", "style": estilo}]}}
    assert _estilo_actual(estado) == estilo  # la objetivo, no la activa
    assert _estilo_actual({"map_context": estado["map_context"]}) == {"symbology_type": "single_symbol"}
    hecho = _hecho_estilo_actual(estilo)
    assert "ESTILO QUE LA CAPA TIENE AHORA EN EL MAPA: tipo graduated_colors, campo area, método quantile, " \
           "clases 5, rampa (color_scheme) viridis." in hecho
    assert "EL USUARIO FIJÓ A MANO en el editor de estilo: color_scheme='viridis'." in hecho
    assert _hecho_estilo_actual(None) == ""
    txt = format_map_context({"layers": [{"id": "l2", "name": "Lotes", "fields": ["area"], "style": estilo}]})
    assert "(método quantile, rampa viridis); el USUARIO FIJÓ A MANO: color_scheme='viridis'" in txt
