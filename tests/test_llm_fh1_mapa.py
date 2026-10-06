"""FH.1 con LLM REAL: operar el mapa es `map_control`; lo que no lo es, no.

Ejecutar: pytest -m llm tests/test_llm_fh1_mapa.py
"""
from __future__ import annotations

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

MAPA = {"layers": [
    {"id": "raster-1", "name": "NDVI 2026-08-10", "kind": "raster-xyz", "visible": True, "opacity": 1},
    {"id": "layer-2", "name": "Lotes Manzana 004503004", "kind": "vector-geojson", "geometry_type": "Polygon",
     "feature_count": 30, "fields": ["lotcodigo", "area_m2"], "visible": True, "is_active": True, "opacity": 1},
]}
CTX = {"schema_info": "Tablas: catastro.lotes, catastro.construcciones", "active_data_source": "internal",
       "active_feature_count": 30, "map_context": MAPA}


async def _intent(q: str) -> str:
    from geo_copilot.agents.router_agent.agent import RouterAgent

    resp = await RouterAgent(llm_client=await get_real_llm_or_skip()).process(q, context=CTX)
    data = resp.data or {}
    print(f"\n[FH.1] {q!r} -> {data.get('intent')}")
    return str(data.get("intent"))


@pytest.mark.asyncio
@pytest.mark.parametrize("q", [
    "acércate a los lotes",
    "oculta el NDVI",
    "pon el NDVI por debajo de los lotes",
    "déjame el NDVI semitransparente",
    "ponle a los lotes una etiqueta con su código",
    "quita la capa del NDVI",
])
async def test_operar_el_mapa_es_map_control(q):
    assert await _intent(q) == "map_control"


@pytest.mark.asyncio
@pytest.mark.parametrize("q,esperado", [
    ("colorea los lotes por área", "apply_symbology"),
    ("trae las construcciones de la manzana 004503004", "query_data"),
])
async def test_lo_que_no_es_operar_el_mapa_no_va_ahi(q, esperado):
    assert await _intent(q) == esperado


@pytest.mark.asyncio
async def test_el_agente_sabe_que_el_usuario_deshizo_su_simbologia():
    """DoD FH.1 (V5): tras Ctrl+Z, «¿por qué ya no veo los colores?» → lo deshizo el usuario."""
    from geo_copilot.agents.router_agent.agent import RouterAgent

    mapa = {**MAPA, "acciones": [{"op": "set_style", "layer_id": "layer-2", "layer_name": "Lotes Manzana 004503004",
                                  "author": "agent", "at": "2026-09-25T18:40:40Z",
                                  "args": {"symbology_type": "unique_values", "field": "lotcodigo"}, "undone": True}]}
    # Una TASA: el router (prompt frágil, no se toca) aún duda a veces («es posible que…»)
    # aunque el deshacer es un hecho del registro. Mayoría en 3 intentos.
    seguros = 0
    for _ in range(3):
        resp = await RouterAgent(llm_client=await get_real_llm_or_skip()).process(
            "¿por qué ya no veo los colores de los lotes?", context={**CTX, "map_context": mapa})
        data = resp.data or {}
        texto = str(data.get("direct_response") or data.get("response") or "")
        print(f"\n[FH.1 deshecho] {data.get('intent')}: {texto}")
        assert data.get("intent") in ("direct_response", "follow_up")
        assert any(p in texto.lower() for p in ("deshic", "deshiz", "deshecho", "ctrl+z")), texto
        seguros += "puede que" not in texto.lower() and "es posible que" not in texto.lower()
    assert seguros >= 2, f"lo supuso en vez de afirmarlo: {3 - seguros}/3"
