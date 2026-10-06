"""Tests con LLM REAL (marker `llm`) — R5.1/R5.2: los overrides por keyword
fueron reemplazados por juicio del LLM guiado por prompt.

Cubren AMBOS lados de cada regla (el caso que el override resolvía Y el caso
que el override rompía):

R5.1 (router, feature seleccionada):
  (a) pregunta sobre LA feature clicada → query_data (lo que el override forzaba)
  (b) follow_up genérico sobre los resultados previos con una feature clicada
      colgada → follow_up (lo que el override ROMPÍA)

R5.2 (symbology, instrucción literal):
  (a) "mapa de calor" pedido textualmente → heatmap (lo que el override forzaba)
  (b) "sin agrupar" → NUNCA cluster (lo que el override ROMPÍA: substring ciego
      a la negación)

Ejecutar: pytest -m llm tests/test_llm_overrides.py
"""

from __future__ import annotations

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


def _map_context_con_feature():
    return {
        "layers": [
            {
                "id": "layer-1", "name": "Lotes", "source": "map",
                "geometry_type": "Polygon", "feature_count": 150,
                "fields": ["objectid", "lotcodigo", "manzcodigo"],
                "visible": True, "is_active": True,
            }
        ],
        "selected_feature": {
            "layer_id": "Lotes",
            "properties": {"objectid": 907046, "lotcodigo": "002539015", "manzcodigo": "0025390"},
        },
        "viewport": {"bbox": [-74.2, 4.5, -73.9, 4.8], "zoom": 12, "crs": "EPSG:4326"},
    }


def _router_context(**extra):
    ctx = {
        "schema_info": "Tablas geoespaciales disponibles:\n- catastro.lotes (MULTIPOLYGON)",
        "conversation_history": [
            {"role": "user", "content": "trae 150 lotes de catastro"},
            {"role": "assistant", "content": "Se encontraron 150 lotes en la capa 'Lotes'."},
        ],
        "previous_results": [
            {"objectid": 1, "lotcodigo": "001", "manzcodigo": "00A"},
            {"objectid": 2, "lotcodigo": "002", "manzcodigo": "00B"},
        ],
        "active_data_source": "internal",
        "active_feature_count": 150,
        "active_geometry_type": "Polygon",
        "active_field_names": ["objectid", "lotcodigo", "manzcodigo"],
        "map_context": _map_context_con_feature(),
    }
    ctx.update(extra)
    return ctx


@pytest.mark.asyncio
async def test_router_pregunta_sobre_feature_clicada_va_a_query_data():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())  # LLM real
    resp = await agent.process(
        "¿cuál es el área del lote que seleccioné?", context=_router_context()
    )
    assert resp.success, resp.message
    assert resp.data["intent"] == "query_data", resp.data


@pytest.mark.asyncio
async def test_router_follow_up_generico_no_es_absorbido_por_la_feature():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())
    # La pregunta es sobre los RESULTADOS previos, no sobre la feature clicada
    # (que quedó colgada de un click viejo). El override viejo forzaba
    # query_data aquí — SQL innecesario.
    resp = await agent.process(
        "¿qué campos tenían esos resultados que me mostraste?",
        context=_router_context(),
    )
    assert resp.success, resp.message
    assert resp.data["intent"] == "follow_up", resp.data


def _puntos_con_uso(n=40):
    feats = []
    for i in range(n):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-74.1 + (i % 8) * 0.01, 4.6 + (i // 8) * 0.01]},
            "properties": {"uso": ["residencial", "comercial", "industrial"][i % 3], "valor": i * 10},
        })
    return {"type": "FeatureCollection", "features": feats}


@pytest.mark.asyncio
async def test_symbology_mapa_de_calor_literal_gana():
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    agent = SymbologyAgent(llm_client=await get_real_llm_or_skip())  # LLM real; sin hub → A2A skip
    analysis = await agent.analyze_data(
        _puntos_con_uso(), query="muéstralos como mapa de calor"
    )
    assert analysis["design"]["symbology_type"] == "heatmap", analysis["design"]


@pytest.mark.asyncio
async def test_symbology_sin_agrupar_nunca_es_cluster():
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    agent = SymbologyAgent(llm_client=await get_real_llm_or_skip())
    # El override viejo por substring veía "agrupa" dentro de "sin agrupar" y
    # FORZABA cluster — exactamente lo contrario de lo pedido.
    analysis = await agent.analyze_data(
        _puntos_con_uso(), query="coloréalos por uso, sin agrupar"
    )
    assert analysis["design"]["symbology_type"] != "cluster", analysis["design"]
