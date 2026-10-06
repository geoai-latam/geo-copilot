"""T3.9 (S3.7) — selección de herramientas a escala.

Con 60 tools MCP enchufadas el LLM no las ve todas: ve el núcleo + `find_tools`
y activa las que busca. Aquí se fija el mecanismo (determinista); el
comportamiento del LLM con 60 tools está en `test_llm_mcp_escala.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from geo_copilot.platform.capabilities import Capability, ToolOutcome, registry
from geo_copilot.platform.mcp.busqueda import ACTIVADAS, buscar, ejecutar_find_tools, seleccion

DOMINIOS = [
    ("ndvi", "Índice de vegetación NDVI de una zona con imágenes Sentinel-2."),
    ("zonal", "NDVI por feature: estadística zonal del índice de vegetación por polígono o lote."),
    ("ruta", "Ruta más corta entre dos puntos por la red vial."),
    ("isocrona", "Área alcanzable en N minutos caminando o en carro desde un punto."),
    ("geocodificar", "Convierte una dirección postal en coordenadas."),
    ("clima", "Pronóstico de lluvia y temperatura para un punto."),
    ("elevacion", "Altura del terreno (modelo digital de elevación) en metros."),
    ("catastro", "Consulta la ficha catastral de un predio por su código."),
    ("censo", "Población y hogares por manzana según el censo."),
    ("aire", "Calidad del aire (PM2.5) en estaciones cercanas."),
]


_PUNTO = {"lat": {"type": "number"}, "lon": {"type": "number"}}
# Parámetros como los de un servicio real: sin ellos (antes `properties: {}`) la herramienta no puede
# recibir el lugar que nombró el usuario, y pedírselo en el mapa era lo único coherente (auditoría
# pre-producción: la «regresión» de test_llm_mcp_escala era en parte el propio test).
PARAMETROS = {
    "ndvi": {"bbox": {"type": "array", "items": {"type": "number"}}},
    "zonal": {"capa": {"type": "string"}},
    "ruta": {"origen": {"type": "string", "description": "dirección, lugar o 'lat,lon'"},
             "destino": {"type": "string", "description": "dirección, lugar o 'lat,lon'"}},
    "isocrona": {**_PUNTO, "minutos": {"type": "integer"}},
    "geocodificar": {"direccion": {"type": "string", "description": "dirección o nombre del lugar"}},
    "clima": _PUNTO,
    "elevacion": _PUNTO,
    "catastro": {"codigo": {"type": "string"}},
    "censo": {"manzana": {"type": "string"}},
    "aire": _PUNTO,
}


def _capacidades(n_servidores: int = 6) -> list[Capability]:
    async def _ok(graph, working, args):
        return ToolOutcome("ok", success=True)

    caps = []
    for s in range(n_servidores):
        for nombre, desc in DOMINIOS:
            caps.append(Capability(
                id=f"mcp.srv{s}.{nombre}", tool_name=f"srv{s}__{nombre}",
                description=f"[Servidor externo «srv{s}»] {desc}", parameters={"type": "object", "properties": PARAMETROS[nombre]},
                executor=_ok, blurb=desc, provider=f"mcp:srv{s}",
            ))
    return caps  # 60


@pytest.fixture
def sesenta():
    from geo_copilot.orchestrator.capabilities_core import ensure_core

    ensure_core()
    caps = _capacidades()
    for c in caps:
        registry().register(c, replace=True)
    yield caps
    for c in caps:
        registry().unregister(c.tool_name)


def test_la_busqueda_encuentra_por_significado_del_dominio(sesenta):
    assert buscar("índice de vegetación por lote", sesenta, limite=12)[0].tool_name.endswith("__zonal")
    top = {c.tool_name.split("__")[1] for c in buscar("cuánto tardo caminando, área alcanzable", sesenta, limite=6)}
    assert top == {"isocrona"}
    assert buscar("xyzzy", sesenta) == []


def test_por_debajo_del_umbral_se_ven_todas_por_encima_solo_las_activadas(sesenta):
    todas = {c.tool_name for c in seleccion(None, [], umbral=100)}
    assert {c.tool_name for c in sesenta} <= todas and "find_tools" not in todas
    pocas = {c.tool_name for c in seleccion(None, ["srv1__ruta"], umbral=25)}
    assert "srv1__ruta" in pocas and "srv2__ruta" not in pocas and "find_tools" in pocas
    assert "query_database" in pocas  # el núcleo siempre


@pytest.mark.asyncio
async def test_find_tools_activa_y_acumula(sesenta):
    working: dict = {}
    out = await ejecutar_find_tools(None, working, {"query": "ruta más corta", "limit": 3})
    assert out.success and len(out.delta[ACTIVADAS]) == 3
    working.update(out.delta)
    out = await ejecutar_find_tools(None, working, {"query": "pronóstico de lluvia", "limit": 2})
    assert len(out.delta[ACTIVADAS]) == 5 and out.delta[ACTIVADAS][:3] == working[ACTIVADAS]
    assert all(n.endswith("__clima") for n in out.delta[ACTIVADAS][3:])


@pytest.mark.asyncio
async def test_el_bucle_ofrece_lo_activado_en_el_paso_siguiente(sesenta):
    from geo_copilot.core.scripted_llm import ScriptedLLM, tool_call_response
    from geo_copilot.orchestrator.nodes import agent_loop

    llm = ScriptedLLM([
        tool_call_response("find_tools", {"query": "isócrona caminando", "limit": 2}),
        tool_call_response("srv3__isocrona", {}),
        tool_call_response("answer", {"text": "listo"}),
    ])
    with patch.object(agent_loop, "get_settings", return_value=MagicMock(
            react_max_reflections=0, react_max_tool_calls=8, react_token_budget=0, mcp_tools_umbral=25)):
        await agent_loop.run(SimpleNamespace(llm=llm, agent_metrics=None), {"query": "q"})
    nombres = [[t["function"]["name"] for t in c["tools"]] for c in llm.calls]
    assert "find_tools" in nombres[0] and not any(n.startswith("srv") for n in nombres[0])
    assert any(n.endswith("__isocrona") for n in nombres[1])
    # el prompt lista los NOMBRES de las 60 (qué existe), no sus esquemas ni descripciones
    prompt = llm.calls[0]["messages"][0].content
    assert "srv5__clima" in prompt and "Pronóstico de lluvia" not in prompt


@pytest.mark.asyncio
async def test_find_tools_activa_por_nombre_del_catalogo(sesenta):
    working: dict = {}
    out = await ejecutar_find_tools(None, working, {"names": ["srv2__clima", "srv9__nada"]})
    assert working == {} and out.delta[ACTIVADAS] == ["srv2__clima"]
    assert "no existen: srv9__nada" in out.observation
    assert not (await ejecutar_find_tools(None, {}, {})).success


def test_el_catalogo_tiene_tope_y_dice_cuantas_faltan(sesenta, monkeypatch):
    from geo_copilot.platform.mcp import busqueda

    assert busqueda.catalogo(None, [], umbral=100) == ""  # por debajo del umbral se ven todas
    completo = busqueda.catalogo(None, [], umbral=25)
    assert "srv0: srv0__aire" in completo and "más" not in completo
    monkeypatch.setattr(busqueda, "CATALOGO_MAX", 300)
    assert "(+" in busqueda.catalogo(None, [], umbral=25)
