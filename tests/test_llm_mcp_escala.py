"""T3.9 / E3.6 con LLM REAL (`-m llm`): con 60 tools MCP enchufadas el agente sigue eligiendo bien.

Las 60 son sintéticas (10 dominios × 6 servidores, ver test_mcp_busqueda): no se
prueba ningún servicio, se prueba la SELECCIÓN — que el LLM busque con
find_tools, active la del dominio correcto y no se deje absorber por los
servicios cuando la tarea es del núcleo.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.test_mcp_busqueda import sesenta

pytestmark = pytest.mark.llm

# Lo mismo que el hub le cuenta al LLM en producción: qué servicios hay (no sus 60 tools).
RESUMEN = "SERVICIOS MCP CONECTADOS (sus herramientas llevan el prefijo `<servicio>__`):\n" + "\n".join(
    f"  - srv{i} (disponible; 10 herramientas): Servicios geográficos de terceros: rutas, isócronas, "
    "geocodificación, clima, elevación del terreno, catastro, censo, calidad del aire, NDVI."
    for i in range(6)
)

CASOS = [
    ("¿Cuál es la ruta más corta en carro desde el parque Simón Bolívar hasta el aeropuerto?", "__ruta"),
    ("¿Va a llover mañana en el centro de Bogotá?", "__clima"),
    ("¿Qué altura sobre el nivel del mar tiene el cerro de Monserrate?", "__elevacion"),
    ("¿Cómo está la calidad del aire (PM2.5) cerca del parque Simón Bolívar?", "__aire"),
    ("¿Cuántos lotes hay en la manzana 002412028 en nuestra base de datos?", "query_database"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("pedido", "esperada"), CASOS)
async def test_con_60_tools_elige_la_del_dominio(sesenta, pedido, esperada):
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.platform.capabilities import ToolOutcome
    from tests.conftest import get_real_llm_or_skip

    graph = SimpleNamespace(llm=await get_real_llm_or_skip(), agent_metrics=None, hitl_manager=None)
    llamadas: list[str] = []
    original = agent_loop.dispatch_tool

    async def espiar(g, working, name, args):
        llamadas.append(name)
        if name in ("query_database", "search_external"):  # no se toca la BD ni la red
            return ToolOutcome(f"{name}: 1 elemento(s). Datos: [{{\"total\": 30}}]", success=True)
        if name.endswith("__geocodificar"):
            # un geocodificador devuelve coordenadas: con «42» gpt-5.4 (con razón) no seguía
            await original(g, working, name, args)
            return ToolOutcome(f'{name}: {{"lugar": "{args}", "lon": -74.0937, "lat": 4.6584}}', success=True)
        if "__" in name and name != "find_tools":
            # un resultado con datos, como el de un servicio real (un «ok» vacío invita a probar
            # la misma herramienta en los otros 5 servidores)
            await original(g, working, name, args)
            return ToolOutcome(f'{name}: {{"resultado": 42, "unidad": "según el servicio", "args": {args}}}',
                               success=True)
        return await original(g, working, name, args)

    # 6 (producción: 8): buscar → geocodificar el lugar → la del dominio cabe con margen
    ajustes = MagicMock(react_max_reflections=0, react_max_tool_calls=6, react_token_budget=0, mcp_tools_umbral=25)
    hub = SimpleNamespace(resumen_prompt=lambda: RESUMEN)
    with patch.object(agent_loop, "get_settings", return_value=ajustes), \
            patch.object(agent_loop, "dispatch_tool", espiar), \
            patch("geo_copilot.platform.mcp.hub.hub_actual", return_value=hub):
        out = await agent_loop.run(graph, {"query": pedido, "session_id": "s"})
    print(f"\n[V4] {pedido!r} -> {llamadas}\n  {str(out.get('final_response'))[:300]!r}")
    elegidas = [n for n in llamadas if n != "find_tools"]
    assert elegidas, f"no usó ninguna herramienta: {llamadas}"
    # El lugar está NOMBRADO en cada pedido: pedirlo en el mapa es no haber buscado cómo resolverlo.
    assert "request_map_input" not in llamadas, llamadas
    # La del dominio, antes que cualquier otra de dominio (geocodificar el lugar primero vale).
    del_dominio = [n for n in elegidas if not n.endswith("__geocodificar")]
    assert del_dominio and del_dominio[0].endswith(esperada), llamadas
    # Con un resultado en la mano no se repite la misma consulta en los otros servidores.
    assert len(del_dominio) <= 2, f"repitió la herramienta en varios servidores: {llamadas}"
    if esperada == "query_database":
        assert "find_tools" not in llamadas, "buscó en los servicios algo que es de la BD"
