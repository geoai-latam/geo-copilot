"""Tests con LLM REAL (marker `llm`) — F3: el router ve los servicios MCP enchufados.

El intent ya no es «imagery»: es `connected_service`, y lo que el LLM sabe de
cada servicio es lo que el hub le cuenta (nombre, estado, descripción del YAML).

(a) petición satelital con un servicio de imagery DISPONIBLE ⇒ connected_service;
(b) la misma petición sin servicios conectados ⇒ NUNCA connected_service (honesto);
(c) consulta de BD normal ⇒ query_data (el servicio no absorbe lo que es de la BD);
(d) el servicio conectado no sirve para lo pedido ⇒ no se elige por estar ahí.

Ejecutar: pytest -m llm tests/test_llm_connected_service.py
"""

from __future__ import annotations

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

_CTX = {
    "schema_info": "Tablas: catastro.lotes (MULTIPOLYGON), catastro.construcciones",
    "active_data_source": "internal",
    "active_feature_count": 120,
    "active_geometry_type": "Polygon",
    "active_field_names": ["lotcodigo", "manzcodigo"],
}

IMAGERY = (
    "SERVICIOS MCP CONECTADOS (sus herramientas llevan el prefijo `<servicio>__`):\n"
    "  - imagery (disponible; 5 herramientas): Imagery satelital Sentinel-2 (NDVI, cambio "
    "entre fechas, NDVI por feature, composiciones de color)."
)
SOLO_CIRCULOS = (
    "SERVICIOS MCP CONECTADOS (sus herramientas llevan el prefijo `<servicio>__`):\n"
    "  - hello (disponible; 2 herramientas): Círculos métricos alrededor de un punto."
)


async def _intent(query: str, servicios: str) -> dict:
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())  # LLM real
    resp = await agent.process(query, context={**_CTX, "connected_services": servicios})
    assert resp.success, resp.message
    return resp.data


@pytest.mark.asyncio
async def test_ndvi_va_al_servicio_conectado():
    data = await _intent("dame el NDVI de cada uno de estos lotes", IMAGERY)
    assert data["intent"] == "connected_service", data


@pytest.mark.asyncio
async def test_sin_servicios_no_elige_connected_service():
    data = await _intent("dame el NDVI de cada uno de estos lotes", "")
    assert data["intent"] != "connected_service", data


@pytest.mark.asyncio
async def test_consulta_bd_no_es_absorbida_por_el_servicio():
    data = await _intent("¿cuántos lotes hay en la manzana 002412028?", IMAGERY)
    # lo que se vigila: el servicio conectado NO absorbe una consulta de datos. Con una capa activa que
    # trae `manzcodigo`, gpt-5.4 la resuelve sobre ella (`follow_up`); gpt-4.1-mini va a la BD (`query_data`)
    assert data["intent"] in ("query_data", "follow_up"), data


@pytest.mark.asyncio
async def test_un_servicio_que_no_sirve_no_se_elige_por_estar_ahi():
    data = await _intent("dame el NDVI de cada uno de estos lotes", SOLO_CIRCULOS)
    assert data["intent"] != "connected_service", data


_HIST_REINTENTO = [
    {"role": "user", "content": "Dame el NDVI de la zona visible"},
    {"role": "assistant", "content": "Intenté obtener el NDVI promedio para la zona visible, pero el servicio de "
                                     "imágenes satelitales no respondió. ¿Quieres que lo intente de nuevo?"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("pedido", ["Ya levanté el servicio, prueba de nuevo", "inténtalo otra vez"])
async def test_reintentar_repite_el_ultimo_pedido(pedido):
    """V5 F3: «ya levanté el servicio, prueba de nuevo» → «dime qué quieres que haga»."""
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())
    resp = await agent.process(pedido, context={**_CTX, "connected_services": IMAGERY,
                                                "conversation_history": _HIST_REINTENTO})
    assert resp.success, resp.message
    assert resp.data["intent"] == "connected_service", resp.data


@pytest.mark.asyncio
async def test_reintentar_con_el_historial_real_de_la_sesion():
    """El caso literal de V5 (4/4 direct_response antes del arreglo): historial real de
    diez turnos, el último del asistente ofrece alternativas en vez de reintentar."""
    import json
    from pathlib import Path

    from geo_copilot.agents.router_agent.agent import RouterAgent

    historial = json.loads((Path(__file__).parent / "data" / "historial_v5_f3_reintento.json")
                           .read_text(encoding="utf-8"))
    # Como lo arma hoy la aplicación (FH.1): el último turno lleva qué se intentó y qué falló.
    from geo_copilot.orchestrator.conversation import ConversationContext

    ctx = ConversationContext(session_id="t")
    for i, m in enumerate(historial):
        if m["role"] == "user":
            ctx.add_user_message(m["content"])
        elif i == len(historial) - 1:
            ctx.add_assistant_message(m["content"], artefactos=[], intent="connected_service",
                                      fallidas=["imagery__imagery_ndvi"])
        else:
            ctx.add_assistant_message(m["content"])
    historial = ctx.get_messages_for_llm(max_messages=len(historial))
    agent = RouterAgent(llm_client=await get_real_llm_or_skip())
    resp = await agent.process("Ya levanté el servicio, prueba de nuevo", context={
        **_CTX, "connected_services": IMAGERY, "conversation_history": historial})
    assert resp.success, resp.message
    assert resp.data["intent"] == "connected_service", resp.data


# ---------------------------------------------------------------------------
# V5 F4 (E4.5): «¿qué valor tiene el NDVI aquí?» con la capa NDVI y un punto marcado.
# El router respondió de memoria con la MEDIA de la zona presentada como el valor
# del punto («aproximadamente 0.25»): una cifra inventada con cara de medida.
# ---------------------------------------------------------------------------
_MAPA_NDVI = {
    "layers": [{
        "id": "raster-1", "name": "NDVI 2026-08-10", "kind": "raster-xyz", "visible": True, "is_active": True,
        "url": "/api/v1/proxy/mcp/imagery/tiles/S2C_18NWL_20260810_0_L2A/{z}/{x}/{y}.png?rescale=-0.03,0.83",
        "origin": {"capability": "mcp.imagery.imagery_ndvi", "arguments": {}},
        "legend": {"field": "NDVI", "min": -0.03, "max": 0.83},
        "bbox": [-74.07, 4.72, -74.02, 4.75],
    }],
    "clicked_point": {"lon": -74.057102, "lat": 4.73536},
    "viewport": {"bbox": [-74.07, 4.72, -74.02, 4.75], "crs": "EPSG:4326"},
}
_HISTORIAL_NDVI = [
    {"role": "user", "content": "calcula el NDVI de la zona visible"},
    {"role": "assistant", "content": "El NDVI de la zona visible (Sentinel-2 del 10 de agosto de 2026, 1.3 % de "
                                     "nubes) tiene media 0.25, entre -0.41 y 0.97."},
]


@pytest.mark.asyncio
async def test_el_valor_en_un_punto_se_mide_no_se_deduce_de_la_media():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())
    resp = await agent.process("¿qué valor tiene el NDVI aquí?", context={
        **_CTX, "connected_services": IMAGERY, "map_context": _MAPA_NDVI,
        "conversation_history": _HISTORIAL_NDVI,
    })
    data = resp.data or {}
    print(f"\n[E4.5] intent={data.get('intent')} reason={data.get('reasoning')} response={data.get('response')}")
    # el valor del punto no está en los hechos (solo la media de la zona): hay que medirlo
    assert data.get("intent") == "connected_service", data


# ---------------------------------------------------------------------------
# V5 F4 (T4.9): «hazme un gráfico de barras con el área de cada lote de esa capa»
# con los 4 lotes cargados y sus áreas YA dichas en el chat. El router eligió
# `follow_up` («ya tiene las áreas calculadas») y el turno terminó sin gráfico:
# una vez con un tutorial de matplotlib, otra afirmando haberlo hecho.
# Pedir un ARTEFACTO no es una pregunta sobre lo que ya hay: hay que producirlo.
# ---------------------------------------------------------------------------
_LOTES = [
    {"lotcodigo": "004503009001", "area_m2": 2165.01}, {"lotcodigo": "004503009026", "area_m2": 1805.04},
    {"lotcodigo": "004503009003", "area_m2": 1842.39}, {"lotcodigo": "004503009002", "area_m2": 1845.61},
]
_HISTORIAL_LOTES = [
    {"role": "user", "content": "muéstrame los lotes de la manzana 004503009 con una tabla del área de cada lote"},
    {"role": "assistant", "content": "Aquí tienes los 4 lotes de la manzana 004503009 con sus áreas: "
                                     + "; ".join(f"{r['lotcodigo']}: {r['area_m2']} m²" for r in _LOTES) + "."},
]


def _historial_envenenado() -> list[dict]:
    """El turno anterior AFIRMÓ un gráfico que no se entregó (narración falsa, V5 F4).
    Se arma por el mismo camino que producción: el historial lleva lo ENTREGADO."""
    from geo_copilot.orchestrator.conversation import ConversationContext

    ctx = ConversationContext(session_id="t49")
    ctx.add_user_message(_HISTORIAL_LOTES[0]["content"])
    ctx.add_assistant_message(_HISTORIAL_LOTES[1]["content"], artefactos=["layer", "table"])
    ctx.add_user_message("hazme un gráfico de barras con el área de cada lote de esa capa")
    ctx.add_assistant_message("He generado un gráfico de barras con el área de cada lote de la manzana "
                              "004503009. Además, la capa está coloreada con un coropleto por área en 4 clases.",
                              artefactos=["table"])
    return ctx.get_messages_for_llm()


@pytest.mark.asyncio
@pytest.mark.parametrize("pedido,historial", [
    ("hazme un gráfico de barras con el área de cada lote de esa capa", _HISTORIAL_LOTES),
    ("ahora ponlo en un gráfico", _HISTORIAL_LOTES),
    pytest.param("hazme un gráfico de barras con el área de cada lote de esa capa", "envenenado", marks=pytest.mark.xfail(
        strict=False, reason="HALLAZGO ABIERTO V5 F4: con una respuesta previa que AFIRMA el gráfico, "
        "gpt-4.1-mini y gpt-4o eligen follow_up aunque el historial diga «[Entregado…: tabla]». "
        "Se ataca en origen: el juez de la respuesta ya rechaza afirmar lo no entregado.")),
])
async def test_pedir_un_grafico_de_lo_ya_calculado_lo_produce(pedido, historial):
    historial = _historial_envenenado() if historial == "envenenado" else historial
    from geo_copilot.agents.router_agent.agent import RouterAgent

    agent = RouterAgent(llm_client=await get_real_llm_or_skip())
    resp = await agent.process(pedido, context={
        **_CTX, "active_feature_count": 4, "active_field_names": ["lotcodigo", "manzcodigo", "area_m2"],
        "previous_sql": "SELECT lotcodigo, shape FROM catastro.lotes WHERE manzcodigo = '004503009'",
        "previous_results": _LOTES, "conversation_history": historial,
    })
    data = resp.data or {}
    print(f"\n[T4.9] {pedido!r} -> intent={data.get('intent')} reason={data.get('reasoning')}")
    # follow_up / direct_response solo devuelven texto: un gráfico ahí no existe.
    assert data.get("intent") not in ("follow_up", "direct_response"), data
