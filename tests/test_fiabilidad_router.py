"""Plan de fiabilidad F1: el router no contesta hechos de memoria.

El LLM del router declara `requiere_fuente`; con `direct_response` y fuente pedida, el turno va al
bucle ReAct (consulta y juez) en vez del responder. El campo lo decide el modelo, no una lista de
palabras: estas pruebas fijan solo la tubería (esquema → datos → ruta).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from geo_copilot.agents.router_agent.esquema import _ESQUEMA_ROUTE


def _grafo():
    from geo_copilot.orchestrator.graph import GeoAgentGraph

    return GeoAgentGraph(llm_client=MagicMock(), db_pool=None, semantic_layer=None)


def _ruta(estado: dict, politica: str = "hybrid") -> str:
    g = _grafo()
    with patch(
        "geo_copilot.orchestrator.graph.get_settings",
        return_value=SimpleNamespace(
            react_mode=False, react_policy=politica, hitl_mode="blocking", enable_planning=True,
        ),
    ):
        return g._route_from_router(estado)


def _estado(requiere_fuente: bool | None) -> dict:
    data = {"intent": "direct_response"}
    if requiere_fuente is not None:
        data["requiere_fuente"] = requiere_fuente
    return {
        "intent": "direct_response",
        "final_response": "respuesta escrita por el router",
        "messages": [{"agent": "router", "data": data, "success": True}],
    }


def test_el_esquema_le_pide_al_llm_el_campo():
    prop = _ESQUEMA_ROUTE["properties"]["requiere_fuente"]
    assert prop["type"] == "boolean" and "herramienta" in prop["description"]


def test_respuesta_directa_que_pide_fuente_va_al_bucle():
    assert _ruta(_estado(True)) == "agent_loop"


@pytest.mark.parametrize("requiere_fuente", [False, None])
def test_charla_o_concepto_sigue_al_responder(requiere_fuente):
    assert _ruta(_estado(requiere_fuente)) == "responder"


def test_sin_bucle_hibrido_no_cambia_nada():
    # con la política clásica no hay bucle al que mandarla: se respeta el camino de siempre
    assert _ruta(_estado(True), politica="off") == "responder"


@pytest.mark.asyncio
async def test_elegir_tarjeta_sin_tarjetas_va_al_bucle_y_no_a_un_texto_fijo():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    router = RouterAgent.__new__(RouterAgent)
    resp = router._respuesta(
        {"intent": "select_service", "reasoning": "eligió", "selected_service_number": 1},
        {"found_services": []},
    )
    assert resp.data["intent"] == "direct_response" and resp.data["requiere_fuente"] is True


@pytest.mark.asyncio
async def test_numero_fuera_de_rango_con_tarjetas_sigue_siendo_un_aviso():
    from geo_copilot.agents.router_agent.agent import RouterAgent

    router = RouterAgent.__new__(RouterAgent)
    resp = router._respuesta(
        {"intent": "select_service", "reasoning": "eligió", "selected_service_number": 9},
        {"found_services": [{"url": "https://ejemplo/1", "title": "uno"}]},
    )
    assert resp.data["intent"] == "direct_response" and resp.data["requiere_fuente"] is False


def _politica(nombre: str):
    return patch(
        "geo_copilot.orchestrator.graph.get_settings",
        return_value=SimpleNamespace(react_mode=False, react_policy=nombre),
    )


@pytest.mark.asyncio
async def test_el_verificador_marca_la_respuesta_directa_que_afirma_hechos():
    from geo_copilot.orchestrator.nodes import router as nodo

    data = {"intent": "direct_response", "direct_response": "Sí, Open-Meteo permite uso comercial."}
    with _politica("hybrid"), patch(
        "geo_copilot.core.verificador_hechos.afirma_hechos", return_value=True
    ) as juicio:
        await nodo._verificar_respuesta_directa(SimpleNamespace(llm=object()), {"query": "¿uso comercial?"}, data)
    assert juicio.await_count == 1 and data["requiere_fuente"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("politica", "data"),
    [
        ("off", {"intent": "direct_response", "direct_response": "x"}),  # sin bucle al que mandarla
        ("hybrid", {"intent": "query_data"}),  # no es respuesta directa
        ("hybrid", {"intent": "direct_response", "direct_response": "x", "requiere_fuente": True}),
    ],
)
async def test_el_verificador_no_se_llama_si_no_hace_falta(politica, data):
    from geo_copilot.orchestrator.nodes import router as nodo

    with _politica(politica), patch("geo_copilot.core.verificador_hechos.afirma_hechos") as juicio:
        await nodo._verificar_respuesta_directa(SimpleNamespace(llm=object()), {"query": "q"}, data)
    assert juicio.await_count == 0


@pytest.mark.asyncio
async def test_sin_juicio_se_conserva_la_respuesta_directa():
    from geo_copilot.core.verificador_hechos import afirma_hechos

    class LLMQueFalla:
        profile = None

        async def chat(self, *a, **k):
            raise RuntimeError("caído")

    assert await afirma_hechos(LLMQueFalla(), "q", "respuesta") is False
    assert await afirma_hechos(None, "q", "respuesta") is False
