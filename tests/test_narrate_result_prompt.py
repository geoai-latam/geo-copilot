"""La narración de un conteo da el número EN CIFRAS y no inventa una capa.

Destapado al fijar la referencia de regresión del núcleo (T0.12): el prompt
decía "VERBALIZA ese número" y el LLM escribía "novecientos treinta y tres mil
cuatrocientos setenta y tres lotes en la capa 'Datos'" — ilegible para un
analista, y el mismo estilo que escondió el conteo inventado de hospitales en F0
(un regex de cifras no lo ve). "Datos" era el fallback de `layer_name`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from geo_copilot.core.llm_client import LLMResponse


async def _prompt(state: dict) -> str:
    from geo_copilot.orchestrator.nodes.insights import narrate_result

    capturado: dict = {}

    async def chat(messages, **_):
        capturado["prompt"] = messages[0].content
        return LLMResponse(content="ok", model="fake")

    graph = MagicMock()
    graph.llm.chat = chat
    await narrate_result(graph, state)
    return capturado["prompt"]


@pytest.mark.asyncio
async def test_conteo_se_pide_en_cifras():
    p = await _prompt({"query": "¿cuántos lotes hay?", "raw_data": [{"count": 933473}]})
    assert "933473" in p
    assert "EN CIFRAS" in p
    assert "VERBALIZA" not in p


@pytest.mark.asyncio
async def test_sin_capa_no_se_inventa_datos():
    p = await _prompt({"query": "¿cuántos lotes hay?", "raw_data": [{"count": 933473}]})
    assert 'Nombre de capa: Datos' not in p
    assert "no menciones capas" in p


@pytest.mark.asyncio
async def test_con_capa_se_nombra():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {}}] * 3}
    p = await _prompt({"query": "trae lotes", "geojson": fc, "layer_name": "Lotes catastrales"})
    assert "Nombre de capa: Lotes catastrales" in p


@pytest.mark.asyncio
async def test_un_conteo_por_grupo_llega_con_sus_valores():
    """V5 F4: «¿cuántos lotes tiene cada manzana?» → 2 filas (44 y 25); el prompt solo
    decía «2 registros» y el LLM narró «4 lotes en total»."""
    p = await _prompt({"query": "¿cuántos lotes tiene cada manzana?", "raw_data": [
        {"manzcodigo": "004503003", "cantidad_lotes": 44}, {"manzcodigo": "004503005", "cantidad_lotes": 25}]})
    assert "44" in p and "25" in p and "004503003" in p


@pytest.mark.asyncio
async def test_con_capa_no_se_vuelcan_los_atributos():
    capa = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {}}] * 3}
    p = await _prompt({"query": "trae los lotes", "geojson": capa, "layer_name": "Lotes",
                       "raw_data": [{"lotcodigo": "X1"}, {"lotcodigo": "X2"}, {"lotcodigo": "X3"}]})
    assert "X1" not in p
