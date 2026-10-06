"""FH.2 — la selección compartida: referencia `seleccion` en todas las herramientas."""
from __future__ import annotations

import pytest

from geo_copilot.core.formatters import format_map_context
from geo_copilot.orchestrator.capabilities_mapa import _map_command
from geo_copilot.platform.seleccion import (
    PredicadoInvalido,
    capa_virtual,
    cumple,
    filtrar,
    seleccion_actual,
    sql_where,
)

LOTES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, 0]},
     "properties": {"lotcodigo": f"00{i}", "area_m2": a}}
    for i, a in enumerate([50, 120, 300, 90, 250])]}


def _mapa(sel=None):
    capa = {"id": "layer-1", "name": "Lotes", "fields": ["lotcodigo", "area_m2"]}
    if sel:
        capa["seleccion"] = sel
    return {"layers": [capa]}


def test_predicado_numerico_texto_y_lista():
    assert cumple({"field": "area_m2", "op": ">", "value": 100}, {"area_m2": "120"})
    assert not cumple({"field": "area_m2", "op": ">", "value": 100}, {"area_m2": 90})
    assert cumple({"field": "lotcodigo", "op": "in", "value": ["001", "003"]}, {"lotcodigo": "003"})
    assert cumple({"field": "lotcodigo", "op": "contains", "value": "02"}, {"lotcodigo": "002"})
    assert not cumple({"field": "no_existe", "op": "=", "value": 1}, {"area_m2": 1})


def test_la_seleccion_por_indice_o_condicion_filtra_la_capa():
    por_ids = seleccion_actual({"map_context": _mapa({"ids": [0, 2], "origin": "lasso"})})
    assert [f["properties"]["lotcodigo"] for f in filtrar(LOTES, por_ids)["features"]] == ["000", "002"]
    por_where = seleccion_actual({"map_context": _mapa({"where": {"field": "area_m2", "op": ">=", "value": 250}})})
    assert len(filtrar(LOTES, por_where)["features"]) == 2


def test_con_id_de_feature_la_seleccion_usa_esa_identidad_no_la_posicion():
    """Un dataset hecho por SQL numera su fid desde 1: la posición NO es la identidad."""
    con_ids = {"type": "FeatureCollection",
               "features": [{**f, "id": i + 1} for i, f in enumerate(LOTES["features"])]}
    sel = seleccion_actual({"map_context": _mapa({"ids": [1, 3], "origin": "click"})})
    assert [f["properties"]["lotcodigo"] for f in filtrar(con_ids, sel)["features"]] == ["000", "002"]


def test_la_capa_virtual_seleccion_es_una_capa_mas():
    virtual = capa_virtual({"layer-1": {"data": LOTES, "name": "Lotes"}}, _mapa({"ids": [1, 4]}))
    assert virtual["name"] == "Selección de Lotes" and len(virtual["data"]["features"]) == 2
    assert capa_virtual({"layer-1": {"data": LOTES}}, _mapa()) is None


def test_sql_parametrizado_y_campo_validado():
    cond, params = sql_where({"field": "area_m2", "op": ">", "value": 5}, None, ["area_m2"])
    assert cond == '(s."area_m2")::double precision > $1' and params == (5.0,)
    assert sql_where(None, (3, 7), ["x"]) == ("s.fid = ANY($1::int[])", ([3, 7],))
    with pytest.raises(PredicadoInvalido, match="no existe"):
        sql_where({"field": "area; DROP TABLE x", "op": "=", "value": 1}, None, ["area_m2"])


def test_el_llm_ve_la_seleccion_y_como_usarla():
    txt = format_map_context(_mapa({"ids": [0, 2, 3], "origin": "lasso"}))
    assert "SELECCIONADOS 3 elemento(s) (por lasso)" in txt
    assert "[seleccion] \"Selección de Lotes\" (dataset seleccion) — los 3 elemento(s) SELECCIONADOS por el usuario en [layer-1]" in txt
    txt2 = format_map_context(_mapa({"where": {"field": "area_m2", "op": ">", "value": 200}, "count": 2,
                                     "origin": "agent"}))
    assert "SELECCIONADOS 2 elemento(s) — los que cumplen area_m2 > 200 (por agent)" in txt2


@pytest.mark.asyncio
async def test_el_agente_selecciona_por_condicion_y_sabe_cuantos():
    working = {"map_context": _mapa(), "map_layers": {"layer-1": {"data": LOTES, "name": "Lotes"}}}
    out = await _map_command(None, working, {"op": "select", "layer_id": "layer-1",
                                             "args": {"where": {"field": "area_m2", "op": ">", "value": 100}}})
    assert out.success
    cmd = out.delta["map_commands"][0]
    assert cmd["args"]["count"] == 3
    # y en el mismo turno `seleccion` ya es una capa para las demás herramientas
    assert len(out.delta["map_layers"]["seleccion"]["data"]["features"]) == 3
    assert seleccion_actual({**working, **out.delta}).count == 3


@pytest.mark.asyncio
async def test_una_condicion_que_no_cumple_nadie_lo_dice():
    working = {"map_context": _mapa(), "map_layers": {"layer-1": {"data": LOTES}}}
    out = await _map_command(None, working, {"op": "select", "layer_id": "layer-1",
                                             "args": {"where": {"field": "area_m2", "op": ">", "value": 9999}}})
    assert "no se seleccionó nada" in out.observation and not out.delta
    # el hecho para juzgar si el campo era el correcto: qué valores toma
    assert "«area_m2» va de" in out.observation


@pytest.mark.asyncio
async def test_limpiar_la_seleccion_la_quita_del_turno():
    working = {"map_context": _mapa({"ids": [0]}), "map_layers": {"layer-1": {"data": LOTES}, "seleccion": {}}}
    out = await _map_command(None, working, {"op": "clear_selection"})
    assert out.success and seleccion_actual({**working, **out.delta}) is None
    assert "seleccion" not in out.delta["map_layers"]
