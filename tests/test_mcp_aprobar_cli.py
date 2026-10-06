"""F5 del plan de calidad: re-aprobar herramientas MCP por comando (sin el panel).

Contra el servidor hello-geo REAL en proceso: el pin «viejo» deshabilita `hello_circle` como lo
haría un cambio de descripción, y el comando la re-aprueba solo con --por y --si.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geo_copilot.platform.capabilities import registry
from geo_copilot.platform.mcp import aprobar
from geo_copilot.platform.mcp.hub import McpHub, MemoryPinStore
from tests.mcp_helpers import CLAVE
from tests.test_mcp_hub import _config


@pytest.fixture
def entorno(hello_mcp_url, monkeypatch):
    monkeypatch.setenv("HELLO_TEST_KEY", CLAVE)
    pins = MemoryPinStore()
    auditadas: list[tuple[str, str]] = []

    async def hub(_settings):
        await pins.set("hello:hello_circle", "huella-de-la-descripcion-anterior")
        h = McpHub(_config(hello_mcp_url), pins=pins)
        await h.refrescar()
        return h

    async def auditar(_settings, e, por):
        auditadas.append((f"{e.servidor}:{e.tool}", por))

    monkeypatch.setattr(aprobar, "_hub", hub)
    monkeypatch.setattr(aprobar, "_auditar", auditar)
    yield SimpleNamespace(pins=pins, auditadas=auditadas)
    for n in ("hello__hello_circle", "hello__hello_about"):
        registry().unregister(n)


@pytest.mark.asyncio
async def test_sin_si_solo_muestra_lo_que_aprobaria(entorno, capsys):
    assert await aprobar.ejecutar(["--pendientes", "--por", "ana"]) == 0
    salida = capsys.readouterr().out
    assert "hello:hello_circle" in salida and "PENDIENTE" in salida
    assert "descripción que leerá el LLM" in salida  # se aprueba leyendo, no a ciegas
    assert await entorno.pins.get("hello:hello_circle") == "huella-de-la-descripcion-anterior"
    assert entorno.auditadas == []


@pytest.mark.asyncio
async def test_con_por_y_si_aprueba_audita_y_la_tool_vuelve(entorno, hello_mcp_url, capsys):
    assert await aprobar.ejecutar(["--servidor", "hello", "--tool", "hello_circle", "--por", "ana", "--si"]) == 0
    assert entorno.auditadas == [("hello:hello_circle", "ana")]
    # lo que hará la app en su próxima revalidación: el pin ya es la huella actual
    hub = McpHub(_config(hello_mcp_url), pins=entorno.pins)
    await hub.refrescar()
    assert registry().get("hello__hello_circle") is not None


@pytest.mark.asyncio
async def test_una_tool_que_no_existe_lo_dice(entorno, capsys):
    assert await aprobar.ejecutar(["--servidor", "hello", "--tool", "no_existe"]) == 1
    assert "no existe" in capsys.readouterr().out


def test_aprobar_exige_decir_quien():
    with pytest.raises(SystemExit):
        aprobar._argumentos(["--pendientes", "--si"])
    with pytest.raises(SystemExit):
        aprobar._argumentos(["--servidor", "hello"])  # sin --tool


@pytest.mark.asyncio
async def test_sin_redis_no_alcanza_los_pins_de_la_app_y_lo_dice():
    with pytest.raises(SystemExit, match="MEMORIA de la app"):
        await aprobar._hub(SimpleNamespace(session_backend="memory"))
