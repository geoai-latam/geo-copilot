"""Tests con LLM REAL (marker `llm`) — Fase 4: clarify (A5) + structured (A3).

DoD A5: consulta GENUINAMENTE ambigua ⇒ clarify con pregunta útil; consultas
claras ⇒ NUNCA clarify (anti-pereza — el agente no puede volverse preguntón).

Bonus A3: el campo `explicit_user_request` del diseño de simbología refleja
si el usuario nombró textualmente el tipo (con el LLM real).

Ejecutar: pytest -m llm tests/test_llm_fase4.py
"""

from __future__ import annotations

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


def _ctx_tres_capas():
    return {
        "schema_info": "Tablas: catastro.lotes, catastro.construcciones",
        "active_data_source": "internal",
        "active_feature_count": 150,
        "map_context": {
            "layers": [
                {"id": "l1", "name": "Lotes", "geometry_type": "Polygon",
                 "feature_count": 150, "visible": True},
                {"id": "l2", "name": "Vías", "geometry_type": "LineString",
                 "feature_count": 80, "visible": True},
                {"id": "l3", "name": "Barrios", "geometry_type": "Polygon",
                 "feature_count": 20, "visible": True},
            ],
        },
    }


@pytest.mark.asyncio
async def test_ambigua_con_tres_capas_pide_aclaracion():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())  # LLM real
    resp = await agent.process("crúzalas", context=_ctx_tres_capas())
    assert resp.success, resp.message
    # Con TRES capas, "crúzalas" no tiene interpretación dominante: el agente
    # debe preguntar cuáles — y la pregunta debe ser útil (menciona capas).
    assert resp.data["intent"] == "clarify", resp.data
    pregunta = (resp.data.get("direct_response") or "").lower()
    assert "?" in pregunta or "¿" in pregunta
    assert "capa" in pregunta or "lotes" in pregunta or "vías" in pregunta, pregunta


@pytest.mark.asyncio
async def test_referente_inexistente_pide_aclaracion():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())
    # "mejóralo" sin NINGÚN contexto: no hay referente al que aplicarlo.
    resp = await agent.process("mejóralo", context={"schema_info": ""})
    assert resp.success, resp.message
    assert resp.data["intent"] in ("clarify", "direct_response"), resp.data
    texto = (resp.data.get("direct_response") or "").lower()
    # Sea clarify o direct_response, debe PREGUNTAR/pedir el referente.
    assert any(w in texto for w in ("qué", "cuál", "?", "¿", "especifica")), texto


@pytest.mark.asyncio
async def test_anti_pereza_consultas_claras_no_clarifican():
    """Las consultas CLARAS del benchmark jamás deben volverse preguntas."""
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())
    capa_activa = {
        "schema_info": "Tablas: catastro.lotes (MULTIPOLYGON), catastro.construcciones",
        "active_data_source": "internal",
        "active_feature_count": 60,
        "active_geometry_type": "Point",
        "active_field_names": ["uso", "valor", "pisos"],
    }
    claras = [
        ("¿cuántos lotes hay en total en la base de datos?", {}),
        ("muéstrame 25 lotes de catastro", {}),
        ("haz un buffer de 300 metros alrededor de estos puntos", capa_activa),
        ("colorea la capa por el campo uso", capa_activa),
        ("agrupa estos puntos en clusters con DBSCAN", capa_activa),
        ("busca datasets de estaciones de bomberos en datos abiertos", {}),
        ("hola, ¿qué sabes hacer?", {}),
        ("¿cuál es la altura promedio de las construcciones?", {}),
    ]
    perezosas = []
    for query, ctx in claras:
        resp = await agent.process(query, context={"schema_info": "catastro.lotes, catastro.construcciones", **ctx})
        if resp.success and resp.data.get("intent") == "clarify":
            perezosas.append(query)
    assert not perezosas, f"clarify perezoso en consultas claras: {perezosas}"


def _puntos(n=40):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [-74.1 + (i % 8) * 0.01, 4.6 + (i // 8) * 0.01]},
            "properties": {"uso": ("res", "com", "ind")[i % 3], "valor": i * 10},
        } for i in range(n)],
    }


@pytest.mark.asyncio
async def test_explicit_user_request_refleja_lo_literal():
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    agent = SymbologyAgent(llm_client=await get_real_llm_or_skip())  # LLM real; sin hub
    literal = await agent.analyze_data(_puntos(), query="muéstralos como mapa de calor")
    assert literal["design"]["symbology_type"] == "heatmap", literal["design"]
    assert literal["design"].get("explicit_user_request") is True, literal["design"]

    libre = await agent.analyze_data(_puntos(), query="coloréalos como mejor te parezca")
    assert libre["design"].get("explicit_user_request") in (False, None), libre["design"]
