"""T3.0 — colores por categoría DECIDIDOS por el LLM (`category_colors`).

El código ya no fija la paleta de un resultado LISA/Gi*: el diseñador de
simbología (LLM) la elige —por pedido del usuario o por convención del dominio
que conoce— y el código solo la valida y la aplica.
"""

from __future__ import annotations

import pytest

from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

CLASES = ["HH"] * 5 + ["LL"] * 3 + ["HL"] * 2 + ["LH"] * 2 + ["ns"] * 20
FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
     "properties": {"lisa_clase": c, "connpisos": i % 5}}
    for i, c in enumerate(CLASES)
]}


def _agente(diseno: dict) -> SymbologyAgent:
    sym = SymbologyAgent(llm_client=False)

    async def fake(*_a, **_k):
        return diseno

    sym._llm_design_symbology = fake  # type: ignore[method-assign]
    return sym


@pytest.mark.asyncio
async def test_el_color_que_el_llm_elige_por_categoria_se_aplica():
    sym = _agente({
        "symbology_type": "unique_values", "classification_field": "lisa_clase",
        "color_scheme": "Set2", "num_classes": 5, "label_field": None,
        "category_colors": {"HH": "#d7191c", "LL": "#2c7bb6", "ns": "gris"},
        "reasoning": "convención LISA",
    })
    resp = await sym.process("muéstrame los clusters", {"geojson": FC})
    assert resp.success, resp.message
    colores = {b["label"]: b["color"] for b in resp.data["class_breaks"]}
    assert colores["HH"] == "#d7191c" and colores["LL"] == "#2c7bb6"
    assert colores["ns"] == "#999999"  # nombre de color → hex
    # las que el LLM no fijó toman el color del esquema (no quedan sin color)
    assert colores["HL"].startswith("#") and colores["LH"].startswith("#")


def test_colores_invalidos_se_descartan():
    sym = SymbologyAgent(llm_client=False)
    assert sym._sanitize_category_colors({"HH": "rojo-feo", "LL": "#12345"}) is None
    assert sym._sanitize_category_colors({"HH": "#D7191C"}) == {"HH": "#d7191c"}
    assert sym._sanitize_category_colors(None) is None


def test_el_disenador_recibe_el_pedido_del_usuario_y_el_del_paso():
    from geo_copilot.orchestrator.nodes.symbology import _pedido_completo

    q = _pedido_completo({"query": "coropleto por lisa_clase en 5 clases",
                          "original_query": "¿hay agrupamiento? muéstrame los clusters LISA"})
    assert "muéstrame los clusters LISA" in q and "coropleto por lisa_clase" in q
    assert _pedido_completo({"query": "colorea de rojo", "original_query": "colorea de rojo"}) == "colorea de rojo"
    assert _pedido_completo({"query": "colorea de rojo"}) == "colorea de rojo"
