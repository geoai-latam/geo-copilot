"""T5.4 — adaptador `tabular_geo`: FILAS de un MCP de terceros (Snowflake, BigQuery…) → capas.

Sin heurísticas: la columna de geometría la DECLARA el LLM (argumento `geometria_resultado`) o
la configuración; el núcleo la valida (existe, se parsea, cae en rango) y si no cuadra lo dice
con las columnas que sí hay. Un servidor G0 REAL (en proceso, a imagen del de Snowflake: texto
JSON sin contrato geo) prueba el camino completo por el hub.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.platform.capabilities import registry
from geo_copilot.platform.mcp.adaptadores import (
    AdaptacionFallida,
    adaptar_tabular,
    filas_de,
    validar,
)
from geo_copilot.platform.mcp.config import McpConfig
from geo_copilot.platform.mcp.hub import McpHub
from geo_copilot.platform.workspace import context as wsctx
from tests.mcp_helpers import puerto_libre

FILAS = [
    {"nombre": "Sede A", "municipio": "SOACHA", "geom_wkt": "POINT(-74.21 4.58)", "lon": -74.21, "lat": 4.58},
    {"nombre": "Sede B", "municipio": "SOACHA", "geom_wkt": "POINT(-74.22 4.59)", "lon": -74.22, "lat": 4.59},
]


# ---------------------------------------------------------------------------
# Unidad
# ---------------------------------------------------------------------------


def test_las_filas_salen_del_texto_json_o_de_una_unica_lista_de_objetos():
    assert filas_de(None, [json.dumps(FILAS)]) == FILAS
    assert filas_de({"rows": FILAS, "total": 2}, []) == FILAS
    assert filas_de(None, ["no es json", json.dumps({"ok": True})]) is None
    assert filas_de(None, ["[]"]) == []
    assert filas_de({"a": FILAS, "b": FILAS}, []) is None  # dos listas: no se elige por adivinación


def test_la_declaracion_se_valida_contra_el_resultado():
    assert validar(FILAS, {"column": "geom_wkt", "encoding": "wkt", "crs": "epsg:4326"}) == {
        "column": "geom_wkt", "encoding": "wkt", "crs": "EPSG:4326"}
    assert validar(FILAS, {"column": "lon", "lat_column": "lat", "encoding": "latlon", "crs": "EPSG:4326"})[
        "lat_column"] == "lat"
    with pytest.raises(AdaptacionFallida, match=r"no tiene.*\['geom'\].*sus columnas: \['nombre'"):
        validar(FILAS, {"column": "geom", "encoding": "wkt", "crs": "EPSG:4326"})
    with pytest.raises(AdaptacionFallida, match="no son wkb_hex válido"):
        validar(FILAS, {"column": "geom_wkt", "encoding": "wkb_hex", "crs": "EPSG:4326"})
    with pytest.raises(AdaptacionFallida, match="lat_column"):
        validar(FILAS, {"column": "lon", "encoding": "latlon", "crs": "EPSG:4326"})
    with pytest.raises(AdaptacionFallida, match="código EPSG"):
        validar(FILAS, {"column": "geom_wkt", "encoding": "wkt", "crs": "WGS84"})


def test_coordenadas_en_metros_declaradas_como_4326_no_se_dibujan():
    """Un UTM/MAGNA declarado como lon/lat mandaba el mapa a otro continente: se rechaza."""
    en_metros = [{"g": "POINT(4880000 2070000)"}]
    with pytest.raises(AdaptacionFallida, match="fuera del rango lon/lat"):
        validar(en_metros, {"column": "g", "encoding": "wkt", "crs": "EPSG:4326"})
    assert validar(en_metros, {"column": "g", "encoding": "wkt", "crs": "EPSG:9377"})["crs"] == "EPSG:9377"


def test_sin_declaracion_queda_como_tabla_y_con_ella_como_tabla_con_geometria():
    solo = adaptar_tabular(None, [json.dumps(FILAS)], None, nombre="t")
    assert "geometry" not in solo["artifacts"][0] and solo["facts"]["filas"] == 2
    geo = adaptar_tabular(None, [json.dumps(FILAS)], {"column": "geom_wkt", "encoding": "wkt", "crs": "EPSG:4326"},
                          nombre="t")
    assert geo["artifacts"][0]["geometry"]["column"] == "geom_wkt" and geo["facts"]["geometria"] == "geom_wkt"
    vacio = adaptar_tabular(None, ["[]"], {"column": "g", "encoding": "wkt", "crs": "EPSG:4326"}, nombre="t")
    assert vacio["artifacts"] == [] and vacio["facts"]["filas"] == 0 and "UPPER" in vacio["facts"]["aviso"]
    assert adaptar_tabular(None, ["hola"], None, nombre="t") is None


# ---------------------------------------------------------------------------
# Por el hub, contra un servidor G0 real
# ---------------------------------------------------------------------------


class ServidorTabular:
    """MCP G0 a imagen del de Snowflake: `run_query` devuelve texto JSON, sin nada geo."""

    def __init__(self) -> None:
        import uvicorn
        from mcp.server.fastmcp import FastMCP
        from mcp.server.transport_security import TransportSecuritySettings

        self.recibidos: list[dict] = []
        mcp = FastMCP("almacen-test", stateless_http=True, json_response=True,
                      transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

        @mcp.tool(description="Ejecuta SQL y devuelve filas JSON.", structured_output=False)
        def run_query(statement: str) -> str:
            self.recibidos.append({"statement": statement})
            if "no_existe" in statement:
                raise ValueError("SQL rechazado: tabla no disponible en el catálogo: public.no_existe")
            return json.dumps(FILAS)

        self.puerto = puerto_libre()
        self._srv = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=self.puerto,
                                                  log_level="warning"))
        self._hilo = threading.Thread(target=self._srv.run, daemon=True)
        self._hilo.start()
        for _ in range(100):
            if self._srv.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.puerto}/mcp"

    def parar(self) -> None:
        self._srv.should_exit = True
        self._hilo.join(timeout=5)


@pytest.fixture
def almacen():
    srv = ServidorTabular()
    yield srv
    srv.parar()
    registry().unregister("almacen__run_query")


@pytest.fixture
def store(monkeypatch):
    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_bbbbbbbbbbbbbbbb", "name": "almacen · run_query", "feature_count": 2}
    st = SimpleNamespace(ingest_table=AsyncMock(return_value=ref),
                         to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": []}))
    monkeypatch.setattr(wsctx, "_store", st)
    return st


async def _hub(url: str) -> McpHub:
    hub = McpHub(McpConfig.model_validate({"servers": [{
        "id": "almacen", "url": url, "auth": {"type": "none"}, "adapter": "tabular_geo",
        "tools": {"allow": ["run_query"]}, "policy": {"default_risk": "read"},
    }]}))
    await hub.refrescar()
    return hub


@pytest.mark.asyncio
async def test_el_llm_declara_la_geometria_y_la_tabla_llega_al_workspace_como_capa(almacen, store):
    await _hub(almacen.url)
    cap = registry().get("almacen__run_query")
    assert "geometria_resultado" in cap.parameters["properties"] and "FILAS" in cap.description
    out = await cap.executor(None, {"session_id": "s1"}, {
        "statement": "SELECT nombre, ST_AsText(geom) AS geom_wkt FROM sedes",
        "geometria_resultado": {"column": "geom_wkt", "encoding": "wkt", "crs": "EPSG:4326",
                                "nombre": "Sedes de Soacha"}})
    assert out.success, out.observation
    assert almacen.recibidos == [{"statement": "SELECT nombre, ST_AsText(geom) AS geom_wkt FROM sedes"}]
    kw = store.ingest_table.call_args.kwargs
    assert store.ingest_table.call_args.args[1] == "Sedes de Soacha"  # el nombre que dio el LLM, no «almacen · run_query»
    g = kw["geometry"]
    assert (g.column, g.encoding, g.crs) == ("geom_wkt", "wkt", "EPSG:4326")
    assert out.delta["result_layer_ref"]["id"] == "ds_bbbbbbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_sin_declaracion_son_filas_y_con_una_mala_se_dice_por_que(almacen, store):
    await _hub(almacen.url)
    cap = registry().get("almacen__run_query")
    tabla = await cap.executor(None, {"session_id": "s1"}, {"statement": "SELECT 1"})
    assert tabla.success and tabla.delta["data"]["results"] == FILAS and not store.ingest_table.called
    # declarada pero ausente (p. ej. un conteo): las filas valen como tabla y se dice por qué no hay mapa
    sin_col = await cap.executor(None, {"session_id": "s1"}, {
        "statement": "SELECT 1", "geometria_resultado": {"column": "geom", "encoding": "wkt", "crs": "EPSG:4326"}})
    assert sin_col.success and sin_col.delta["data"]["results"] == FILAS and not store.ingest_table.called
    assert "TABLA, sin mapa" in sin_col.observation and "geom_wkt" in sin_col.observation
    # declarada y presente pero no es geometría válida: no se dibuja nada inventado
    mala = await cap.executor(None, {"session_id": "s1"}, {
        "statement": "SELECT 1", "geometria_resultado": {"column": "nombre", "encoding": "wkt", "crs": "EPSG:4326"}})
    assert not mala.success and "no son wkt válido" in mala.observation and "No se dibujó nada" in mala.observation


@pytest.mark.asyncio
async def test_un_sql_rechazado_dice_que_el_servicio_sigue_ahi_y_como_seguir(almacen, store):
    """V5: tras suponer el nombre de la tabla y recibir un rechazo, el LLM abandonaba el servicio."""
    await _hub(almacen.url)
    out = await registry().get("almacen__run_query").executor(None, {"session_id": "s1"},
                                                              {"statement": "SELECT * FROM no_existe"})
    assert not out.success and "public.no_existe" in out.observation
    assert "sigue disponible" in out.observation and "listar tablas" in out.observation
