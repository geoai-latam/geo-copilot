"""FH.8 — acciones contextuales: el menú sale SOLO de lo que cada capacidad declara (`geo_inputs`).

DoD: una capacidad nueva de un MCP G1 aparece sola en el menú de los tipos de geometría que
acepta, sin código de frontend.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from geo_copilot.platform.capabilities import Capability, ToolOutcome, registry


def _nombres(acciones: list[dict]) -> set[str]:
    return {a["herramienta"] for a in acciones}


@pytest.fixture
def con_workspace(monkeypatch):
    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.orchestrator.capabilities_core import ensure_core

    monkeypatch.setattr(ce, "store_actual", lambda: object())
    ensure_core()


@pytest.fixture
def tool_mcp_nueva(monkeypatch):
    """Una tool de un MCP G1 que declara recibir un ÁREA (como la estadística zonal)."""
    from geo_copilot.platform.mcp import hub as hubmod

    async def ejecutar(graph, working, args):
        return ToolOutcome("ok", success=True)

    cap = Capability(id="mcp.suelos.textura_zonal", tool_name="suelos__textura_zonal", description="Textura del suelo",
                     parameters={"type": "object", "properties": {"zona": {"type": "string"}}}, executor=ejecutar,
                     blurb="[suelos] Textura del suelo por zona", provider="mcp:suelos", risk="read",
                     geo_inputs={"zona": {"accepts": ["geometry", "layer_ref"], "geometry_types": ["Polygon", "MultiPolygon"]}})
    registry().register(cap, replace=True)
    monkeypatch.setattr(hubmod, "hub_actual", lambda: SimpleNamespace(
        tools={"suelos__textura_zonal": SimpleNamespace(habilitada=True)}))
    yield cap
    registry().unregister(cap.tool_name)


def test_la_tool_nueva_de_un_mcp_aparece_sola_para_los_tipos_que_acepta(con_workspace, tool_mcp_nueva):
    from geo_copilot.platform.acciones import aplicables

    assert "suelos__textura_zonal" in _nombres(aplicables("Polygon"))
    assert "suelos__textura_zonal" in _nombres(aplicables("MultiPolygon"))
    assert "suelos__textura_zonal" not in _nombres(aplicables("Point"))
    accion = next(a for a in aplicables("Polygon") if a["herramienta"] == "suelos__textura_zonal")
    assert accion["server"] == "suelos" and accion["objetivo"] == "zona"
    assert accion["titulo"] == "Textura del suelo por zona"
    assert accion["input_schema"]["properties"]["zona"]["type"] == "string"


def test_una_tool_deshabilitada_por_el_pinning_no_aparece(con_workspace, tool_mcp_nueva, monkeypatch):
    from geo_copilot.platform.acciones import aplicables
    from geo_copilot.platform.mcp import hub as hubmod

    monkeypatch.setattr(hubmod, "hub_actual", lambda: SimpleNamespace(
        tools={"suelos__textura_zonal": SimpleNamespace(habilitada=False)}))
    assert "suelos__textura_zonal" not in _nombres(aplicables("Polygon"))


def test_las_del_nucleo_segun_lo_que_tiene_sentido_para_cada_geometria(con_workspace):
    from geo_copilot.platform.acciones import aplicables

    punto, poligono = _nombres(aplicables("Point")), _nombres(aplicables("Polygon"))
    assert {"ws_buffer", "ws_measure"} <= punto and {"ws_buffer", "ws_measure", "ws_add_measure"} <= poligono
    assert "ws_add_measure" not in punto  # un punto no tiene área ni longitud
    todas = _nombres(aplicables(None))
    assert "query_database" not in todas and "map_command" not in todas  # no reciben algo del mapa


def test_sin_workspace_las_ws_no_se_ofrecen(monkeypatch):
    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.platform.acciones import aplicables

    monkeypatch.setattr(ce, "store_actual", lambda: None)
    assert not {n for n in _nombres(aplicables("Polygon")) if n.startswith("ws_")}


def test_escribir_en_un_sistema_externo_nunca_va_al_menu(con_workspace, monkeypatch):
    from geo_copilot.platform.acciones import aplicables
    from geo_copilot.platform.mcp import hub as hubmod

    async def ejecutar(graph, working, args):
        return ToolOutcome("ok", success=True)

    cap = Capability(id="mcp.x.borrar", tool_name="x__borrar", description="borra", executor=ejecutar,
                     parameters={"type": "object", "properties": {}}, blurb="borra", provider="mcp:x", risk="write",
                     geo_inputs={"aoi": {"accepts": ["geometry"]}})
    registry().register(cap, replace=True)
    monkeypatch.setattr(hubmod, "hub_actual", lambda: SimpleNamespace(tools={"x__borrar": SimpleNamespace(habilitada=True)}))
    try:
        assert "x__borrar" not in _nombres(aplicables("Polygon"))
    finally:
        registry().unregister("x__borrar")


@pytest.mark.asyncio
async def test_run_ejecuta_la_misma_capacidad_y_rechaza_lo_que_el_menu_no_ofrece(con_workspace, tool_mcp_nueva):
    import httpx

    from geo_copilot.api.app import create_app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as http:
        r = await http.get("/api/v1/acciones", params={"geometria": "Polygon"})
        assert r.status_code == 200 and "suelos__textura_zonal" in _nombres(r.json()["acciones"])
        assert (await http.get("/api/v1/acciones", params={"geometria": "Circulo"})).status_code == 400
        ok = await http.post("/api/v1/acciones/suelos__textura_zonal/run",
                             json={"session_id": "s1", "arguments": {"zona": "seleccion"}})
        assert ok.status_code == 200 and ok.json()["success"] is True
        no = await http.post("/api/v1/acciones/query_database/run", json={"session_id": "s1", "arguments": {}})
        assert no.status_code == 404


def test_el_kit_declara_los_tipos_de_geometria_por_argumento():
    from geo_mcp_kit.geo import geo_meta

    geo = geo_meta(inputs={"aoi": ["geometry", "layer_ref"], "fecha": ["date"]},
                   geometry_types={"aoi": ["Polygon"]})["geo"]
    assert geo["inputs"]["aoi"] == {"accepts": ["geometry", "layer_ref"], "geometry_types": ["Polygon"]}
    assert geo["inputs"]["fecha"] == {"accepts": ["date"]}  # sin tipos: cualquiera


def test_el_titulo_es_la_primera_frase_de_la_descripcion():
    from geo_copilot.platform.acciones import _titulo

    assert _titulo("NDVI POR FEATURE (por lote) de una capa. Devuelve la") == "NDVI POR FEATURE (por lote) de una capa"
    largo = _titulo("Busca escenas que cubren el AOI (fechas ISO YYYY-MM-DD; filtra por nubes y por muchas cosas más")
    assert len(largo) <= 80 and largo.endswith("…")
