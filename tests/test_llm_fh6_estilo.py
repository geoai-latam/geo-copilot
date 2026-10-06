"""FH.6 con LLM REAL: el diseñador de simbología respeta lo que el usuario fijó a mano,
salvo que el usuario pida cambiarlo.

Ejecutar: pytest -m llm tests/test_llm_fh6_estilo.py -s
"""
from __future__ import annotations

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[i, 0], [i + 1, 0], [i + 1, 1], [i, 1], [i, 0]]]},
     "properties": {"lotcodigo": f"L{i:02d}", "area_m2": round(50 + i ** 1.7 * 13, 1)}} for i in range(40)]}
ESTILO = {"symbology_type": "graduated_colors", "classification_field": "area_m2", "classification_method": "natural_breaks",
          "num_classes": 5, "color_scheme": "Reds", "pinned": ["color_scheme"]}


async def _disenar(pedido: str, estilo: dict = ESTILO) -> dict:
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    r = await SymbologyAgent(llm_client=await get_real_llm_or_skip()).process(
        query=pedido, context={"geojson": FC, "estilo_actual": estilo})
    d = r.data or {}
    print(f"\n[FH.6] {pedido!r} -> {d.get('symbology_type')} campo={d.get('classification_field')} "
          f"clases={len(d.get('class_breaks') or [])} rampa={d.get('color_scheme')}")
    return d


@pytest.mark.asyncio
async def test_siete_clases_conserva_la_rampa_que_fijo_el_usuario():
    """DoD FH.6: graduado por área, el usuario puso «Reds» a mano; «ahora 7 clases» la conserva."""
    d = await _disenar("ahora clasifícalo en 7 clases")
    assert d.get("symbology_type") == "graduated_colors" and d.get("classification_field") == "area_m2"
    assert len(d.get("class_breaks") or []) == 7
    assert str(d.get("color_scheme")) in ("Reds", "ColorScheme.REDS"), d.get("color_scheme")


@pytest.mark.asyncio
async def test_si_pide_otra_rampa_el_fijado_no_la_bloquea():
    d = await _disenar("ponlo en tonos azules")
    assert "blue" in str(d.get("color_scheme")).lower(), d.get("color_scheme")
