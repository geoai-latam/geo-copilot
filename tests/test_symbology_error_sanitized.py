"""Regresión P1 (diagnóstico 2026-06-19): el error de simbología no debe
filtrar internals a la UI.

Antes ``SymbologyAgent.process`` devolvía ``message=f"Error generando
simbología: {str(e)}"`` — exponía el ``str`` crudo de la excepción (que puede
traer rutas de archivo, nombres de módulo o detalles de la BD). Ahora pasa por
``core.error_sanitizer.sanitize_error``: log completo, mensaje honesto sin
internals para el usuario.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.symbology_agent import SymbologyAgent


@pytest.mark.asyncio
async def test_process_sanitizes_exception_message(monkeypatch):
    agent = SymbologyAgent(llm_client=MagicMock())

    # analyze_data revienta con un mensaje que filtra una ruta interna.
    leaky = "psycopg error en C:/Users/Sebas/Documents/GitHub/GEO_COPILOT/secret.py"
    agent.analyze_data = AsyncMock(side_effect=RuntimeError(leaky))

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {"v": 1},
            }
        ],
    }

    resp = await agent.process("pinta los puntos", context={"geojson": geojson})

    assert resp.success is False
    # El internal NO se filtra al usuario.
    assert "secret.py" not in resp.message
    assert "C:/Users" not in resp.message
    assert "psycopg" not in resp.message
    # Sí da un mensaje honesto.
    assert "simbología" in resp.message.lower()
