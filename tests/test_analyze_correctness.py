"""Correctitud del motor analítico (intent 'analyze') end-to-end — Track A.

Cubre los fixes que hacen que una pregunta analítica de verdad responda:
  * FIX-ROUTER-ANALYZE   — el router acepta/propaga el intent 'analyze'.
  * FND-CONTRACT         — GraphState declara los canales `data`/`visualization`
                           (sin declarar, LangGraph los descarta en el merge).
  * FIX-RESPONDER-VIZ    — _map_final_state respeta la visualización/data que
                           emitió el nodo python_agent en vez de re-inferir.
  * FIX-JSONABLE-NONFINITE — el runner sanea NaN/Inf (Starlette allow_nan=False
                           reventaría con 500).

Todos deterministas (sin LLM real, sin sandbox POSIX). El juicio agéntico del
router (¿elige 'analyze' por la razón correcta?) se valida aparte con -m llm.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.gis_agent.sandbox_runner import _sanitize_nonfinite
from geo_copilot.agents.router_agent.agent import RouterAgent
from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState


# ─────────────────────────── FIX-ROUTER-ANALYZE ────────────────────────────
class TestRouterAcceptsAnalyze:
    def test_capabilities_include_analyze(self):
        caps = RouterAgent(llm_client=MagicMock()).get_capabilities()
        assert "analyze" in caps["intents"]

    @pytest.mark.asyncio
    async def test_process_no_rechaza_intent_analyze(self):
        """Con intent='analyze' del LLM, process() NO cae al fallback de error.

        Antes del fix, 'analyze' no estaba en valid_intents → ValueError →
        el except devolvía data.intent='direct_response'. Ahora debe propagar
        data.intent='analyze'.
        """
        # A3: la decisión llega como tool_call `route` (structured outputs).
        from geo_copilot.core.scripted_llm import tool_call_response
        router = RouterAgent(llm_client=MagicMock())
        router.llm_client.chat = AsyncMock(return_value=tool_call_response("route", {
            "intent": "analyze",
            "reasoning": "el usuario pide clustering sobre la capa cargada",
            "entities": [],
        }))
        resp = await router.process(
            query="agrupa estos lotes en clusters y dame estadísticas",
            context={
                "found_services": [],
                "map_context": None,
                "sandbox_available": True,
                "schema_info": "",
                "conversation_history": [],
            },
        )
        assert resp.data.get("intent") == "analyze", (
            f"esperaba intent 'analyze', obtuve {resp.data.get('intent')!r} "
            "(si es 'direct_response' el router lo rechazó)"
        )


# ─────────────────────────────── FND-CONTRACT ──────────────────────────────
class TestGraphStateDeclaraCanalAnalitico:
    def test_data_y_visualization_declarados(self):
        """LangGraph descarta claves no declaradas en el merge; el nodo
        python_agent emite `data` y `visualization` — deben existir en el
        esquema o se pierden antes de llegar al responder."""
        anns = GraphState.__annotations__
        assert "data" in anns, "GraphState debe declarar 'data' (canal analítico)"
        assert "visualization" in anns, "GraphState debe declarar 'visualization'"


# ────────────────────────────── FIX-RESPONDER-VIZ ──────────────────────────
class TestMapFinalStateRespetaNodo:
    def _map(self, final_state: dict) -> dict:
        # _map_final_state no usa `self`; lo invocamos sin instanciar el grafo.
        return GeoAgentGraph._map_final_state(None, final_state)

    def test_visualization_del_nodo_gana_sobre_lo_inferido(self):
        node_viz = {"type": "chart", "chart_type": "scatter", "x_axis": "area", "y_axis": "pob"}
        out = self._map({
            "visualization": node_viz,
            "data": {"results": [{"area": 1, "pob": 2}]},
            "raw_data": [],
            "final_data": {"visualization": {"type": "map"}},  # lo re-inferido
        })
        assert out["visualization"] == node_viz
        assert out["data"] == {"results": [{"area": 1, "pob": 2}]}

    def test_sin_canal_analitico_cae_al_comportamiento_previo(self):
        out = self._map({
            "raw_data": [{"n": 1}],
            "final_data": {"visualization": {"type": "map"}},
        })
        assert out["visualization"] == {"type": "map"}
        assert out["data"] == {"results": [{"n": 1}]}


# ─────────────────────────── FIX-JSONABLE-NONFINITE ────────────────────────
class TestSanitizeNonFinite:
    def test_nan_e_inf_a_none(self):
        nan = float("nan")
        assert _sanitize_nonfinite(nan) is None
        assert _sanitize_nonfinite(float("inf")) is None
        assert _sanitize_nonfinite(float("-inf")) is None

    def test_finitos_y_otros_tipos_intactos(self):
        assert _sanitize_nonfinite(3.14) == 3.14
        assert _sanitize_nonfinite(0.0) == 0.0
        assert _sanitize_nonfinite(True) is True   # bool no debe convertirse
        assert _sanitize_nonfinite(5) == 5
        assert _sanitize_nonfinite("x") == "x"
        assert _sanitize_nonfinite(None) is None

    def test_estructura_anidada_y_json_serializable_estricto(self):
        payload = {
            "stats": {"corr": float("nan"), "n": 10, "media": 2.5},
            "serie": [1.0, float("inf"), 3.0],
        }
        clean = _sanitize_nonfinite(payload)
        assert clean == {"stats": {"corr": None, "n": 10, "media": 2.5},
                         "serie": [1.0, None, 3.0]}
        # La prueba de fuego: serializa con allow_nan=False como hace Starlette.
        json.dumps(clean, allow_nan=False)
