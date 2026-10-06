"""Simbología: un diseño que los datos no permiten aplicar se corrige con el
hecho, no se degrada en silencio.

Destapado por la regresión del núcleo en F1 (recorrido 04): ante "colorea los
lotes por lotdispers" el LLM a veces pedía `graduated_colors` sobre un campo
CATEGÓRICO (N/D). Sin rangos que calcular, la construcción degradaba en silencio
a single_symbol: color plano, sin aviso.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
from geo_copilot.core.scripted_llm import ScriptedLLM, tool_call_response

CAMPOS = {
    "lotdispers": {"data_type": "categorical", "unique_values": ["N", "D"], "count": 50},
    "area": {"data_type": "numeric_continuous",
             "statistics": {"min": 10, "max": 900, "std": 120.0}, "count": 50},
}


def _agente(guion) -> tuple[SymbologyAgent, ScriptedLLM]:
    llm = ScriptedLLM(guion)
    agente = SymbologyAgent(llm_client=MagicMock())
    agente.llm_client = llm
    return agente, llm


def _diseno(**d):
    return tool_call_response("design_symbology", {"reasoning": "r", **d})


def test_graduado_sobre_categorico_es_incoherente():
    msg = SymbologyAgent._diseno_incoherente(
        {"symbology_type": "graduated_colors", "classification_field": "lotdispers"}, CAMPOS,
    )
    assert msg and "lotdispers" in msg and "NUMÉRICO" in msg


@pytest.mark.parametrize("diseno", [
    {"symbology_type": "graduated_colors", "classification_field": "area"},
    {"symbology_type": "unique_values", "classification_field": "lotdispers"},
    {"symbology_type": "single_symbol"},
    {"symbology_type": "graduated_colors", "classification_field": "lotdispers",
     "manual_class_breaks": [{"min_value": 0, "max_value": 1, "color": "#ff0000"}]},
])
def test_disenos_aplicables(diseno):
    assert SymbologyAgent._diseno_incoherente(diseno, CAMPOS) is None


@pytest.mark.asyncio
async def test_repregunta_con_el_hecho_y_acepta_la_correccion():
    agente, llm = _agente([
        _diseno(symbology_type="graduated_colors", classification_field="lotdispers"),
        _diseno(symbology_type="unique_values", classification_field="lotdispers"),
    ])
    out = await agente._llm_design_symbology(
        "colorea los lotes por lotdispers", CAMPOS, "Polygon", [{"lotdispers": "N"}], 50,
    )
    assert out["symbology_type"] == "unique_values"
    assert len(llm.calls) == 2
    correccion = llm.calls[1]["messages"][-1].content
    assert "lotdispers" in correccion and "NUMÉRICO" in correccion


@pytest.mark.asyncio
async def test_si_insiste_la_degradacion_se_declara():
    agente, _ = _agente([
        _diseno(symbology_type="graduated_colors", classification_field="lotdispers"),
        _diseno(symbology_type="graduated_symbols", classification_field="lotdispers"),
    ])
    out = await agente._llm_design_symbology(
        "colorea por lotdispers", CAMPOS, "Polygon", [{"lotdispers": "N"}], 50,
    )
    assert out["symbology_type"] == "single_symbol"
    assert out["reasoning"].startswith("[DEGRADED]")


@pytest.mark.asyncio
async def test_diseno_coherente_no_repregunta():
    agente, llm = _agente([
        _diseno(symbology_type="graduated_colors", classification_field="area"),
    ])
    await agente._llm_design_symbology("colorea por área", CAMPOS, "Polygon", [{"area": 1}], 50)
    assert len(llm.calls) == 1


@pytest.mark.parametrize("valores, clases_esperadas", [
    ([1, 1, 1], [(1, 1, 3)]),                        # todos iguales: UNA clase, no «1–1 (0)» ×2
    ([1, 5, 163, 163, 139], None),                   # sin bordes repetidos en la leyenda
])
def test_bordes_repetidos_no_dan_clases_vacias_ni_duplicadas(valores, clases_esperadas):
    """V5 EH.7: pocos valores distintos daban «1–1 (0), 1–1 (0), 1–1 (3)» y «163–163 (1)»."""
    from geo_copilot.agents.symbology_agent.styles import ClassificationMethod, ColorScheme

    agente = SymbologyAgent(llm_client=MagicMock())
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"v": v}} for v in valores]}
    for metodo in ClassificationMethod:
        breaks = agente._calculate_numeric_breaks(fc, "v", metodo, ColorScheme.BLUES, 5)
        if metodo.value not in ("quantile", "equal_interval", "natural_breaks", "std_deviation"):
            continue
        tramos = [(b.min_value, b.max_value) for b in breaks]
        assert len(set(tramos)) == len(tramos), (metodo, tramos)
        assert all(lo < hi for lo, hi in tramos) or len(tramos) == 1, (metodo, tramos)
        assert sum(b.count for b in breaks) == len(valores), (metodo, tramos)
        if clases_esperadas:
            assert [(b.min_value, b.max_value, b.count) for b in breaks] == clases_esperadas


@pytest.mark.asyncio
async def test_un_campo_que_la_capa_no_tiene_se_dice_con_los_que_si():
    """V3 F5 (regresión 04): «colorea por manzcodigo» sobre una capa traída sin ese campo acababa en
    un color único con un motivo confuso; el bucle necesita el hecho para volver a traer la capa."""
    agente, _ = _agente([
        _diseno(symbology_type="unique_values", classification_field="manzcodigo"),
        _diseno(symbology_type="unique_values", classification_field="manzcodigo"),
    ])
    out = await agente._llm_design_symbology("colorea por manzana (manzcodigo)", CAMPOS, "Polygon",
                                             [{"lotdispers": "N"}], 50)
    assert "NO tiene el campo «manzcodigo»" in out["reasoning"]
    assert "lotdispers" in out["reasoning"] and "area" in out["reasoning"]
