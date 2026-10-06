"""Hallazgos V5 de F3 (navegador, usuario final) convertidos en test.

- Una consulta que trae una capa no decía que era una capa con geometría ni cómo
  referirse a ella: el LLM repetía la consulta «con su geometría» (otra
  aprobación) y después pedía confirmación para «cargarla como capa» en vez de
  pasarla a la herramienta del servicio conectado.
"""

from __future__ import annotations

from geo_copilot.orchestrator.react_tools import _observe

LOTES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"lotcodigo": str(i)},
     "geometry": {"type": "Point", "coordinates": [-74.15, 4.57]}} for i in range(30)]}


def test_una_consulta_con_capa_dice_que_es_la_capa_activa_y_como_referirla():
    obs, ok = _observe("query_database", {"geojson": LOTES, "sql": "SELECT … LIMIT 10000"})
    assert ok and "30 elemento(s)" in obs
    assert "capa activa" in obs and "`activa`" in obs and "geometría" in obs


def test_un_resultado_sin_capa_no_dice_que_hay_capa():
    obs, _ = _observe("query_database", {"raw_data": [{"total": 30}], "sql": "SELECT count(*)"})
    assert "capa activa" not in obs and "30" in obs


# ---------------------------------------------------------------------------
# Con SESSION_BACKEND=redis la sesión se deserializa en cada turno: si la ruta no
# la guarda al final, se pierde el historial (el «sí, sube el umbral de nubes»
# llegó sin la pregunta que respondía y el router pidió aclaración).
# ---------------------------------------------------------------------------


class _StoreQueSerializa:
    """Como Redis: guarda un dict y devuelve un objeto NUEVO en cada get."""

    def __init__(self):
        self._d: dict[str, dict] = {}

    def get(self, sid):
        from geo_copilot.orchestrator.conversation import ConversationContext

        return ConversationContext.from_dict(self._d[sid]) if sid in self._d else None

    def save(self, ctx):
        import json

        self._d[ctx.session_id] = json.loads(json.dumps(ctx.to_dict(), default=str))

    def count(self):
        return len(self._d)

    def cleanup_expired(self):
        return 0

    def exists(self, sid):
        return sid in self._d


def test_la_sesion_persiste_entre_turnos_con_un_backend_que_serializa(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi.testclient import TestClient

    from geo_copilot.api import dependencies as deps
    from geo_copilot.api.app import create_app
    from geo_copilot.orchestrator.conversation import ConversationManager

    monkeypatch.setattr(deps, "get_app_state", lambda: SimpleNamespace(dataset_store=None))
    monkeypatch.setattr("geo_copilot.api.websocket.send_result", AsyncMock())
    monkeypatch.setattr("geo_copilot.api.websocket.send_status", AsyncMock())
    graph = MagicMock()
    graph.process = AsyncMock(return_value={
        "success": True, "intent": "connected_service", "data": None,
        "message": "¿Quieres que amplíe el umbral de nubes?"})
    app = create_app()
    manager = ConversationManager(store=_StoreQueSerializa())
    manager.create_session("sess-r")
    app.dependency_overrides[deps.get_conversation_manager] = lambda: manager
    app.dependency_overrides[deps.get_agent_graph] = lambda: graph
    http = TestClient(app)

    for texto in ("dame el NDVI de cada lote", "sí, sube el umbral de nubes"):
        assert http.post("/api/v1/query/", json={"query": texto, "session_id": "sess-r"}).status_code == 200

    historial = graph.process.call_args.kwargs["conversation_history"]
    # (V5 F4: cada respuesta lleva delante lo que el turno entregó de verdad)
    respuestas = [m["content"] for m in historial if m["role"] == "assistant"]
    assert len(respuestas) == 1
    assert respuestas[0].startswith("[Entregado al usuario en este turno: solo texto")
    assert respuestas[0].endswith("¿Quieres que amplíe el umbral de nubes?")
    assert historial[0] == {"role": "user", "content": "dame el NDVI de cada lote"}


# ---------------------------------------------------------------------------
# El bucle ReAct no veía la conversación: «inténtalo de nuevo» tras pedir el NDVI
# POR LOTE de ENERO-FEBRERO llegó solo, y el agente calculó el NDVI general de una
# escena de agosto.
# ---------------------------------------------------------------------------


import pytest


@pytest.mark.asyncio
async def test_el_bucle_react_ve_la_conversacion_reciente():
    from unittest.mock import MagicMock, patch

    from geo_copilot.core.scripted_llm import ScriptedLLM, tool_call_response
    from geo_copilot.orchestrator.nodes import agent_loop

    llm = ScriptedLLM([tool_call_response("answer", {"text": "listo"})])
    graph = MagicMock(llm=llm, agent_metrics=None)
    historial = [
        {"role": "user", "content": "Vuelve a calcular el NDVI de cada lote con imágenes de enero o febrero de 2026"},
        {"role": "assistant", "content": "El servicio de imágenes no está disponible en este momento."},
        {"role": "user", "content": "Inténtalo de nuevo"},  # el turno actual ya va en el historial
    ]
    with patch.object(agent_loop, "get_settings", return_value=MagicMock(
            react_max_reflections=0, react_max_tool_calls=8, react_token_budget=0)):
        await agent_loop.run(graph, {"query": "Inténtalo de nuevo", "conversation_history": historial})
    mensajes = llm.calls[0]["messages"]
    roles = [m.role for m in mensajes]
    assert roles == ["system", "user", "assistant", "user"], roles
    assert "enero o febrero" in mensajes[1].content
    assert mensajes[-1].content == "Inténtalo de nuevo"  # sin duplicar el turno actual
