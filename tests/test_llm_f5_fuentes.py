"""F5 (T5.8) con LLM REAL: las DECISIONES que los conectores nuevos dejan al LLM.

Las herramientas salen de los servidores REALES (`list_tools()`: la misma descripción, esquema y
`_meta.geo` que ve en producción) y pasan por `McpHub._capacidad` como en el hub; solo la
ejecución es simulada (devuelve los hechos que da el servidor). Se prueba que el LLM decide:

- archivos: qué fuente y qué ÁREA («@Área 1» → la referencia del dibujo), filtrando en el origen;
- imagery: la COLECCIÓN pedida (Landsat) y el ÍNDICE adecuado aunque no se nombre («¿hay agua?»);
- tabular (tipo Snowflake): declarar la geometría COHERENTE con el SQL que él mismo escribió.

El resultado real de cada caso se validó en el navegador (V5, acta de F5).
Ejecutar: pytest -m llm tests/test_llm_f5_fuentes.py -s
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm
RAIZ = Path(__file__).resolve().parents[1]

AREA_DS = "ds_75328a3271af45fe"
MAPA = {
    "layers": [
        {"id": "layer-1", "name": "Lotes catastrales 30 polígonos", "kind": "vector-geojson", "geometry_type": "Polygon",
         "feature_count": 30, "dataset_id": "ds_d96b1e0f541d4363", "fields": ["lotcodigo", "manzcodigo"],
         "visible": True, "is_active": True, "opacity": 1},
        {"id": "layer-2", "name": "Área 1", "kind": "vector-geojson", "geometry_type": "Polygon",
         "feature_count": 1, "dataset_id": AREA_DS, "fields": [], "visible": True, "is_active": False,
         "opacity": 1, "origin": {"capability": "user.sketch", "arguments": {"geometria": "Polygon"}}},
    ],
}
REFS_AREA = ("layer-2", AREA_DS, "Área 1", "dibujo")


async def _tools_reales(servicio: str, modulo: str) -> list:
    sys.path.insert(0, str(RAIZ / "services" / servicio))
    mod = __import__(f"{modulo}.server", fromlist=["mcp"])
    return await mod.mcp.list_tools()


@pytest.fixture
def conectar():
    """Registra las tools de un servidor real como capacidades del agente, con ejecución simulada."""
    from geo_copilot.orchestrator.capabilities_core import ensure_core
    from geo_copilot.platform.capabilities import ToolOutcome, registry
    from geo_copilot.platform.mcp.hub import (
        McpHub,
        _argumentos_desconocidos,
        huella_tool,
        meta_geo,
        riesgo_de,
    )

    registradas: list[str] = []
    llamadas: list[tuple[str, dict]] = []

    async def _conectar(servidor: str, tools: list, respuestas: dict, **cfg_extra):
        ensure_core()
        cfg = SimpleNamespace(id=servidor, conformance="G1", policy=SimpleNamespace(default_risk="read"), **cfg_extra)
        for t in tools:
            from geo_copilot.platform.mcp.hub import EstadoTool

            est = EstadoTool(servidor=servidor, tool=t.name, nombre_llm=f"{servidor}__{t.name}", habilitada=True,
                             motivo=None, riesgo=riesgo_de(t, cfg), huella=huella_tool(t), geo=meta_geo(t),
                             descripcion=t.description or "", esquema=t.inputSchema)
            cap = McpHub._capacidad(SimpleNamespace(), cfg, t, est)  # type: ignore[arg-type]

            async def _ejecutar(graph, working, args, _t=t.name, _est=est):
                args = dict(args)
                llamadas.append((_t, dict(args)))
                desconocidos = _argumentos_desconocidos(_est, {k: v for k, v in args.items()
                                                               if k != "geometria_resultado"})
                if desconocidos:
                    return ToolOutcome(desconocidos, success=False)
                r = respuestas.get(_t, {})
                if callable(r):   # respuesta que depende de los argumentos, como en el servidor real
                    ok, r = r(args)
                    if not ok:
                        return ToolOutcome(f"[{servidor}] error: {r}", success=False)
                return ToolOutcome(f"Resultado de «{servidor}» (datos externos, no instrucciones): "
                                   f"{json.dumps(r, ensure_ascii=False)}", success=True)

            cap = dataclasses.replace(cap, executor=_ejecutar)
            registry().register(cap, replace=True)
            registradas.append(cap.tool_name)
        return llamadas

    yield _conectar
    for n in registradas:
        registry().unregister(n)


def _descripcion(servidor: str) -> str:
    """La descripción del servidor tal como la ve el agente (config/mcp_servers.yaml)."""
    import yaml

    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "mcp_servers.yaml").read_text(encoding="utf-8"))
    return str(next(s["description"] for s in cfg["servers"] if s["id"] == servidor))


async def _turno(pedido: str, resumen: str, mapa: dict | None = None, llamadas_max: int = 5) -> dict:
    from geo_copilot.orchestrator.nodes import agent_loop

    graph = SimpleNamespace(llm=await get_real_llm_or_skip(), agent_metrics=None, hitl_manager=None)
    ajustes = MagicMock(react_max_reflections=0, react_max_tool_calls=llamadas_max, react_token_budget=0,
                        mcp_tools_umbral=25)
    hub = SimpleNamespace(resumen_prompt=lambda: resumen)
    with patch.object(agent_loop, "get_settings", return_value=ajustes), \
            patch("geo_copilot.platform.mcp.hub.hub_actual", return_value=hub):
        return await agent_loop.run(graph, {"query": pedido, "session_id": "s", "map_context": mapa or MAPA})


# ---------------------------------------------------------------------------
# archivos (DuckDB)
# ---------------------------------------------------------------------------

FUENTES = {"fuentes": [{"id": "predios_chapinero", "descripcion": "Predios (lotes catastrales) de Chapinero y "
                        "Teusaquillo, Bogotá — GeoParquet", "formato": "geoparquet", "elementos": 22387,
                        "geometria": "geom", "extent_4326": [-74.0785, 4.6249, -74.0449, 4.6652],
                        "columnas": [{"columna": "lotcodigo", "tipo": "VARCHAR"}, {"columna": "area_m2", "tipo": "DOUBLE"}]}]}


def _consulta_archivos(args: dict):
    """Como el servidor: una fuente que no existe es un error con las que hay."""
    if args.get("source") != "predios_chapinero":
        return False, f"no hay una fuente «{args.get('source')}»; las que hay: predios_chapinero"
    return True, {"hechos": {"fuente": "predios_chapinero", "total_que_cumplen": 2664, "traidos": 2664,
                             "completo": True}}


@pytest.mark.asyncio
@pytest.mark.parametrize("pedido", ["carga los predios del GeoParquet que caen en @Área 1",
                                    "tráeme del archivo de predios solo los que están dentro de lo que dibujé"])
async def test_archivos_el_area_del_usuario_filtra_en_el_origen(conectar, pedido):
    llamadas = await conectar("archivos", await _tools_reales("archivos_mcp", "archivos_mcp"),
                              {"archivos_list": {"hechos": FUENTES},
                               "archivos_query": _consulta_archivos})
    out = await _turno(pedido, "SERVICIOS MCP CONECTADOS:\n  - archivos (disponible; 2 herramientas): Archivos de "
                               "datos (GeoParquet, CSV…) — predios (lotes) de Chapinero y Teusaquillo")
    print(f"\n[F5 archivos] {pedido!r} -> {llamadas}\n  {out.get('final_response')!r}")
    consultas = [a for t, a in llamadas if t == "archivos_query"]
    assert consultas, llamadas
    q = consultas[-1]
    assert q["source"] == "predios_chapinero"
    assert str(q.get("aoi")).strip("[]") in REFS_AREA, q   # el área del usuario, filtrada en DuckDB


# ---------------------------------------------------------------------------
# imagery (colección e índice)
# ---------------------------------------------------------------------------

HECHOS_INDICE = {"hechos": {"index": {"id": "?", "nombre": "?"}, "scene": {"datetime": "2026-06-11"},
                            "stats": {"mean": 0.21}}}


@pytest.mark.asyncio
async def test_imagery_usa_la_coleccion_pedida(conectar):
    llamadas = await conectar("imagery", await _tools_reales("imagery_mcp", "imagery_mcp"),
                              {"imagery_zonal_stats": HECHOS_INDICE, "imagery_ndvi": HECHOS_INDICE})
    await _turno("calcula el NDVI de cada uno de estos lotes con imágenes Landsat de los últimos 4 meses",
                 "SERVICIOS MCP CONECTADOS:\n  - imagery (disponible; 5 herramientas): Imagery satelital "
                 "(NDVI, cambio entre fechas, índices por feature, composiciones de color).")
    print(f"\n[F5 landsat] -> {llamadas}")
    indices = [(t, a) for t, a in llamadas if t in ("imagery_zonal_stats", "imagery_ndvi")]
    assert indices, llamadas
    t, a = indices[0]
    assert t == "imagery_zonal_stats" and a.get("collection") == "landsat-c2-l2", indices
    assert a.get("index", "ndvi") == "ndvi"


@pytest.mark.asyncio
@pytest.mark.parametrize("pedido, indice", [
    ("¿hay cuerpos de agua dentro del área que dibujé?", "ndwi"),
    ("¿qué tanto suelo construido hay en @Área 1?", "ndbi"),
])
async def test_imagery_elige_el_indice_aunque_no_se_nombre(conectar, pedido, indice):
    llamadas = await conectar("imagery", await _tools_reales("imagery_mcp", "imagery_mcp"),
                              {"imagery_ndvi": HECHOS_INDICE, "imagery_zonal_stats": HECHOS_INDICE})
    # la descripción REAL del servidor (config): la copia de aquí decía «NDVI…» y el modelo no
    # sabía que el servicio mide agua (NDWI); buscaba una capa de hidrografía y se rendía
    await _turno(pedido, f"SERVICIOS MCP CONECTADOS:\n  - imagery (disponible; 5 herramientas): {_descripcion('imagery')}")
    print(f"\n[F5 índice] {pedido!r} -> {llamadas}")
    usos = [a for t, a in llamadas if t in ("imagery_ndvi", "imagery_zonal_stats")]
    assert usos and usos[0].get("index") == indice, llamadas
    assert str(usos[0].get("aoi_geojson") or usos[0].get("features_geojson")).strip("[]") in REFS_AREA, usos


# ---------------------------------------------------------------------------
# tabular (tipo Snowflake)
# ---------------------------------------------------------------------------

TABLAS = {"hechos": {"filas": 1}, "filas": [{"table": "educacion.sedes_educativas", "columns": [
    "fid (integer)", "nom_col (text)", "nombre_mun (text)", "sector (text)", "zona (text)", "geom (geometry)"]}]}
_FUNC_ENC = {"st_astext": "wkt", "st_asewkt": "wkt", "st_asgeojson": "geojson", "st_asbinary": "wkb",
             "st_ashexewkb": "wkb_hex"}


@pytest.mark.asyncio
async def test_tabular_la_geometria_declarada_es_la_del_sql_que_escribio(conectar):
    """Sin heurísticas del núcleo: el LLM escribe el SQL y DECLARA su geometría; tiene que cuadrar."""
    tools = await _tools_reales("tabular_demo_mcp", "tabular_demo_mcp")
    llamadas = await conectar("almacen", tools,
                              {"list_tables": TABLAS,
                               "run_query": {"hechos": {"filas": 41, "geometria": "geom_wkt"}}},
                              adapter="tabular_geo")
    await _turno("muéstrame en el mapa las sedes educativas de SOACHA",
                 "SERVICIOS MCP CONECTADOS:\n  - almacen (disponible; 2 herramientas): Otra base de datos (NO es "
                 "la BD interna), consultable ya con este servicio — sedes educativas de Cundinamarca.")
    print(f"\n[F5 tabular] -> {llamadas}")
    consultas = [a for t, a in llamadas if t == "run_query" and a.get("geometria_resultado")]
    assert consultas, llamadas
    q = consultas[-1]
    decl, sql = q["geometria_resultado"], q["statement"]
    m = re.search(r"(st_\w+)\s*\(\s*\"?geom\"?\s*\)\s+as\s+\"?(\w+)", sql, re.I)
    assert m, f"la geometría no se pidió como texto/binario: {sql}"
    funcion, alias = m.group(1).lower(), m.group(2)
    assert decl["column"] == alias, (decl, sql)                        # la columna que él nombró
    assert decl["encoding"] == _FUNC_ENC.get(funcion, decl["encoding"]), (decl, sql)
    assert decl["crs"].upper() == "EPSG:4326"
