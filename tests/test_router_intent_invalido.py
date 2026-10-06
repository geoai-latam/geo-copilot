"""V5 (hello): «dibújame un círculo de 500 m alrededor de -74.05, 4.65» → el router devolvió como intent
el NOMBRE de una herramienta («hello__circle») y el usuario recibió «No pude procesar tu consulta» 2 de
cada 3 veces. Ahora se le dice el hecho y decide otra vez (una vez)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.router_agent.agent import RouterAgent


@pytest.mark.asyncio
async def test_un_intent_invalido_se_corrige_una_vez(monkeypatch):
    from geo_copilot.core import structured_output

    llamadas = []

    async def falso(cliente, mensajes, **k):
        llamadas.append(list(mensajes))
        return ({"intent": "hello__circle", "reasoning": "x"} if len(llamadas) == 1
                else {"intent": "connected_service", "reasoning": "lo hace el servicio hello"})

    monkeypatch.setattr(structured_output, "structured_call", falso)
    r = await RouterAgent(llm_client=MagicMock()).process("dibújame un círculo de 500 m", context={})
    assert r.data["intent"] == "connected_service" and len(llamadas) == 2
    assert "«hello__circle» no es un intent" in llamadas[1][-1].content


@pytest.mark.asyncio
async def test_si_vuelve_a_fallar_el_error_sigue_siendo_honesto(monkeypatch):
    from geo_copilot.core import structured_output

    monkeypatch.setattr(structured_output, "structured_call",
                        AsyncMock(return_value={"intent": "hello__circle", "reasoning": "x"}))
    r = await RouterAgent(llm_client=MagicMock()).process("dibújame un círculo", context={})
    assert r.success is False and "intent inválido" in str(r.data.get("error_context"))
