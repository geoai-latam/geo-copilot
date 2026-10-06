"""S2.5 — las capacidades ws_* en el bucle ReAct (despacho, estado, disponibilidad).

La corrección geométrica está en test_workspace_ops.py (PostGIS real); aquí,
el contrato con el bucle: qué ve el LLM, qué entra al estado y qué sale del turno.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from geo_copilot.orchestrator import capabilities_espaciales as ce
from geo_copilot.orchestrator.capabilities_core import ensure_core
from geo_copilot.platform.capabilities import registry
from geo_copilot.platform.contracts import FieldInfo, LayerRef, Provenance, WorkspaceTable
from geo_copilot.platform.workspace import WorkspaceError, ops
from geo_copilot.platform.workspace import context as wsctx

WS_TOOLS = {
    "ws_measure", "ws_buffer", "ws_overlay", "ws_spatial_join",
    "ws_aggregate_by_zone", "ws_spatial_autocorrelation",
}


def _ref(ds="ds_0123456789abcdef", n=3, nombre="Buffer 500 m de Vías") -> LayerRef:
    return LayerRef(
        id=ds, name=nombre, kind="vector", provider="core", crs="EPSG:4326",
        geometry_type="Polygon", feature_count=n, fields=[FieldInfo(name="nombre", type="string")],
        storage=WorkspaceTable(schema_name="ws_0123456789abcdef", table="d_0123456789abcdef"),
        provenance=Provenance(capability="core.buffer", produced_at=datetime.now(UTC)),
    )


FC = {"type": "FeatureCollection", "features": []}


@pytest.fixture
def store(monkeypatch):
    s = SimpleNamespace(
        to_geojson=AsyncMock(return_value=FC),
        list_datasets=AsyncMock(return_value=[_ref("ds_aaaaaaaaaaaaaaaa", nombre="Vías")]),
    )
    monkeypatch.setattr(wsctx, "_store", s)
    return s


def test_se_registran_y_solo_con_workspace(monkeypatch):
    ensure_core()
    assert WS_TOOLS <= {c.tool_name for c in registry().all()}
    monkeypatch.setattr(wsctx, "_store", None)
    assert not WS_TOOLS & {c.tool_name for c in registry().available(None)}
    monkeypatch.setattr(wsctx, "_store", object())
    assert WS_TOOLS <= {c.tool_name for c in registry().available(None)}


@pytest.mark.asyncio
async def test_resolver_acepta_ds_o_la_capa_del_mapa():
    working = {"map_context": {"layers": [
        {"id": "layer-1", "dataset_id": "ds_aaaaaaaaaaaaaaaa"}, {"id": "layer-2"},
    ]}}
    assert await ce._resolver("ds_bbbbbbbbbbbbbbbb", working) == "ds_bbbbbbbbbbbbbbbb"
    assert await ce._resolver("layer-1", working) == "ds_aaaaaaaaaaaaaaaa"
    assert await ce._resolver("layer-2", working) is None  # capa sin dataset
    assert await ce._resolver("inventado", working) is None


# V5 F4: `query_database` le dice al LLM «refiérete a ella como `activa`» y las ws_*
# lo rechazaban; la capa recién traída no tenía ds_ y el agente operó sobre las viejas.
_LOTES_NUEVOS = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"i": i}}
    for i in range(30)]}


@pytest.mark.asyncio
async def test_activa_es_la_capa_traida_en_el_turno_y_se_guarda_una_vez(store):
    store.ingest_features = AsyncMock(return_value=_ref("ds_cccccccccccccccc", n=30, nombre="Lotes 004503004"))
    working = {"session_id": "s1", "geojson": _LOTES_NUEVOS, "layer_name": "Lotes 004503004",
               "map_context": {"layers": [{"id": "layer-1", "dataset_id": "ds_aaaaaaaaaaaaaaaa"}]}}
    assert await ce._resolver("activa", working) == "ds_cccccccccccccccc"
    args = store.ingest_features.await_args
    assert args.args[:3] == ("s1", "Lotes 004503004", _LOTES_NUEVOS)
    # queda como el dataset del turno: la segunda referencia no vuelve a guardar
    assert working["result_layer_ref"]["id"] == "ds_cccccccccccccccc"
    assert await ce._resolver("activa", working) == "ds_cccccccccccccccc"
    assert store.ingest_features.await_count == 1


@pytest.mark.asyncio
async def test_activa_con_capa_objetivo_usa_su_dataset(store):
    store.ingest_features = AsyncMock()
    working = {"session_id": "s1", "target_layer_id": "layer-1",
               "map_layers": {"layer-1": {"data": _LOTES_NUEVOS, "name": "Lotes"}},
               "map_context": {"layers": [{"id": "layer-1", "dataset_id": "ds_aaaaaaaaaaaaaaaa"}]}}
    assert await ce._resolver("activa", working) == "ds_aaaaaaaaaaaaaaaa"
    store.ingest_features.assert_not_awaited()


@pytest.mark.asyncio
async def test_activa_sin_capa_es_none(store):
    assert await ce._resolver("activa", {"session_id": "s1"}) is None


@pytest.mark.asyncio
async def test_buffer_deja_la_capa_nueva_en_el_estado(store, monkeypatch):
    nuevo = _ref()
    monkeypatch.setattr(ops, "buffer", AsyncMock(return_value=ops.Resultado(
        nuevo, {"area_total_m2": 2785398.16, "area_total_ha": 278.5398},
    )))
    out = await ce._buffer(None, {"session_id": "sess-a", "sql": "SELECT viejo"},
                           {"dataset": "ds_aaaaaaaaaaaaaaaa", "meters": 500, "dissolve": True})
    assert out.success
    obs = json.loads(out.observation)
    assert obs["hechos"]["area_total_ha"] == 278.5398 and obs["nuevo_dataset"]["id"] == nuevo.id
    assert out.delta["result_layer_ref"]["id"] == nuevo.id
    assert out.delta["geojson"] == FC and out.delta["sql"] is None  # pizarra limpia
    args = ops.buffer.call_args
    assert args.args[1:] == ("sess-a", "ds_aaaaaaaaaaaaaaaa", 500.0) and args.kwargs["disolver"] is True


@pytest.mark.asyncio
async def test_resultado_grande_no_se_hidrata(store, monkeypatch):
    monkeypatch.setattr(ops, "buffer", AsyncMock(return_value=ops.Resultado(_ref(n=60_000), {})))
    out = await ce._buffer(None, {"session_id": "s"}, {"dataset": "ds_aaaaaaaaaaaaaaaa", "meters": 10, "dissolve": False})
    assert out.delta["geojson"] is None and out.delta["result_layer_ref"]["feature_count"] == 60_000
    store.to_geojson.assert_not_called()


@pytest.mark.asyncio
async def test_medir_no_crea_capa_ni_toca_el_estado(store, monkeypatch):
    monkeypatch.setattr(ops, "medir", AsyncMock(return_value=ops.Resultado(None, {"area_total_m2": 1e6})))
    out = await ce._medir(None, {"session_id": "s"}, {"dataset": "ds_aaaaaaaaaaaaaaaa"})
    assert out.success and out.delta == {}
    assert json.loads(out.observation)["hechos"]["area_total_m2"] == 1e6
    assert "seleccion_en_el_mapa" not in json.loads(out.observation)


@pytest.mark.asyncio
async def test_medir_la_capa_entera_con_seleccion_lo_dice(store, monkeypatch):
    """FH.2 (V5): el agente midió los 30 lotes con 8 seleccionados; la observación lo dice."""
    monkeypatch.setattr(ops, "medir", AsyncMock(return_value=ops.Resultado(None, {"area_total_m2": 1e6})))
    working = {"session_id": "s", "map_context": {"layers": [
        {"id": "layer-1", "name": "Lotes", "dataset_id": "ds_aaaaaaaaaaaaaaaa", "is_active": True,
         "seleccion": {"ids": [1, 2], "count": 2, "origin": "box"}}]}}
    for ref in ("ds_aaaaaaaaaaaaaaaa", "[layer-1]"):
        nota = json.loads((await ce._medir(None, working, {"dataset": ref})).observation)["seleccion_en_el_mapa"]
        assert "capa ENTERA «Lotes»" in nota and "2 elemento(s) SELECCIONADOS" in nota


@pytest.mark.asyncio
async def test_un_error_del_workspace_le_da_al_llm_los_ids_validos(store, monkeypatch):
    monkeypatch.setattr(ops, "medir", AsyncMock(side_effect=WorkspaceError("el dataset ds_x no existe en esta sesión")))
    out = await ce._medir(None, {"session_id": "s"}, {"dataset": "ds_xxxxxxxxxxxxxxxx"})
    assert not out.success
    assert "no existe en esta sesión" in out.observation and "ds_aaaaaaaaaaaaaaaa «Vías»" in out.observation


@pytest.mark.asyncio
async def test_un_id_que_no_es_dataset_se_rechaza_sin_llamar_al_workspace(store, monkeypatch):
    monkeypatch.setattr(ops, "overlay", AsyncMock())
    out = await ce._overlay(None, {"session_id": "s"}, {"dataset_a": "lotes", "dataset_b": "ds_aaaaaaaaaaaaaaaa", "mode": "intersection"})
    assert not out.success and "`lotes` no es un dataset" in out.observation
    ops.overlay.assert_not_called()


@pytest.mark.asyncio
async def test_bloque_del_prompt_lista_ids_y_pide_preferir_ws(store):
    txt = await ce.bloque_workspace("sess-a")
    assert "ds_aaaaaaaaaaaaaaaa «Vías»" in txt and "PREFIERE las herramientas ws_*" in txt
    assert await ce.bloque_workspace(None) == ""


@pytest.mark.asyncio
async def test_con_seleccion_el_bloque_la_lista_como_un_dataset_mas(store):
    """FH.2 (V5): la selección va donde el agente busca los datasets de las ws_*."""
    mc = {"layers": [{"id": "layer-1", "name": "Lotes", "dataset_id": "ds_aaaaaaaaaaaaaaaa",
                      "seleccion": {"ids": [1, 2, 3], "count": 3, "origin": "box"}}]}
    txt = await ce.bloque_workspace("sess-a", mc)
    assert ("  - seleccion «Selección de Lotes» — los 3 elemento(s) SELECCIONADOS por el usuario "
            "en ds_aaaaaaaaaaaaaaaa [layer-1] (por box)") in txt
    assert "seleccion «" not in await ce.bloque_workspace("sess-a", {"layers": [{"id": "layer-1"}]})


def test_una_herramienta_de_transformacion_limpia_el_dataset_previo():
    from geo_copilot.orchestrator.react_tools import _FRESH, _TRANSFORM_CLEAR

    assert "result_layer_ref" in _FRESH and "result_layer_ref" in _TRANSFORM_CLEAR


def test_el_bucle_saca_el_dataset_del_turno():
    from geo_copilot.orchestrator.nodes.agent_loop import _OUTPUT_KEYS

    assert "result_layer_ref" in _OUTPUT_KEYS


def test_map_context_muestra_el_dataset_de_cada_capa():
    from geo_copilot.core.formatters import format_map_context

    txt = format_map_context({"layers": [
        {"id": "layer-1", "name": "Vías", "dataset_id": "ds_aaaaaaaaaaaaaaaa", "is_active": True},
    ]})
    assert '[layer-1] "Vías" (dataset ds_aaaaaaaaaaaaaaaa)' in txt


@pytest.mark.asyncio
async def test_el_punto_y_la_zona_de_turnos_anteriores_no_se_listan(store):
    """V5 (otra temática): el agente usó «Punto marcado (-74.03767, …)» de un turno viejo como su «aquí»."""
    def figura(ds, nombre, cap):
        r = _ref(ds, n=1, nombre=nombre)
        return r.model_copy(update={"provenance": r.provenance.model_copy(update={"capability": cap})})

    viejo = figura("ds_bbbbbbbbbbbbbbbb", "Punto marcado (-74.03767, 4.72644)", "core.punto")
    zona = figura("ds_cccccccccccccccc", "Zona visible del mapa", "core.viewport")
    store.list_datasets.return_value = [_ref("ds_aaaaaaaaaaaaaaaa", nombre="Vías"), viejo, zona]
    txt = await ce.bloque_workspace("sess-a")
    assert "ds_aaaaaaaaaaaaaaaa «Vías»" in txt
    assert "Punto marcado" not in txt and "Zona visible" not in txt


@pytest.mark.asyncio
async def test_con_punto_marcado_el_bloque_lo_lista_como_un_dataset_mas(store):
    """V5 (otra temática): sin esta línea el agente hacía un buffer de la capa entera antes de dar con `punto`."""
    txt = await ce.bloque_workspace("sess-a", {"layers": [], "clicked_point": {"lon": -74.426531, "lat": 4.992324}})
    assert "  - punto «Punto marcado (-74.42653, 4.99232)» — el «aquí» del usuario AHORA; Point" in txt
    assert "  - punto «" not in await ce.bloque_workspace("sess-a", {"layers": []})


@pytest.mark.asyncio
async def test_una_capa_del_catalogo_incompleta_se_marca_como_tal(store):
    """Pendiente del acta FH: 2000 de 5311 elementos; conteos sobre ella no son del servicio."""
    r = _ref("ds_dddddddddddddddd", n=2000, nombre="Sedes educativas")
    r = r.model_copy(update={"provenance": r.provenance.model_copy(update={
        "capability": "discovery.load", "arguments": {"elementos_cargados": 2000, "total_en_servicio": 5311}})})
    store.list_datasets.return_value = [r]
    txt = await ce.bloque_workspace("sess-a")
    assert "(INCOMPLETA: 2000 de 5311 elementos del servicio;" in txt


@pytest.mark.asyncio
async def test_una_tabla_de_la_bd_como_dataset_dice_como_cruzarla(store):
    """V5 F5: pasó `catastro.lotes` a ws_spatial_join y se desvió; el error dice usar query_database."""
    out = await ce._join(None, {"session_id": "s"}, {"dataset_a": "catastro.lotes", "dataset_b": "ds_aaaaaaaaaaaaaaaa",
                                                      "predicate": "dwithin", "meters": 200})
    assert not out.success and "parece una TABLA de la base de datos" in out.observation
    assert "query_database" in out.observation
    otro = await ce._join(None, {"session_id": "s"}, {"dataset_a": "lotes", "dataset_b": "ds_aaaaaaaaaaaaaaaa",
                                                       "predicate": "intersects", "meters": None})
    assert "parece una TABLA" not in otro.observation


@pytest.mark.asyncio
async def test_una_capa_de_una_consulta_filtrada_se_dice_subconjunto_de_su_tabla(store):
    """V5 acumulada: con «Construcciones Manzana 004503009» (9) cargada, a «¿cuántas construcciones
    del catastro caen en el buffer?» el agente cruzó esa capa y narró «9 construcciones del
    catastro». Su procedencia (el SQL con WHERE) dice que es un subconjunto: es un hecho."""
    def con_sql(ref, sql):
        return ref.model_copy(update={"provenance": ref.provenance.model_copy(update={"sql": sql})})

    filtrada = con_sql(_ref("ds_bbbbbbbbbbbbbbbb", n=9, nombre="Construcciones Manzana 004503009"),
                       "SELECT c.objectid, c.shape FROM catastro.construcciones c "
                       "JOIN catastro.lotes l ON l.lotcodigo = c.lotecodigo WHERE l.manzcodigo = '004503009'")
    completa = con_sql(_ref("ds_cccccccccccccccc", n=5, nombre="Localidades"),
                       "SELECT nombre, shape FROM catastro.localidades")
    store.list_datasets = AsyncMock(return_value=[filtrada, completa])
    txt = await ce.bloque_workspace("sess-a")
    linea = next(x for x in txt.splitlines() if "ds_bbbbbbbbbbbbbbbb" in x)
    assert "SUBCONJUNTO de la BD: solo las filas de catastro.construcciones, catastro.lotes" in linea
    assert "NO es el de la tabla completa" in linea
    assert "SUBCONJUNTO" not in next(x for x in txt.splitlines() if "ds_cccccccccccccccc" in x)
