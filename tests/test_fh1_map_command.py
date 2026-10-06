"""FH.1 — el agente opera el mapa con `map_command`; el código solo valida hechos."""
from __future__ import annotations

import pytest

from geo_copilot.orchestrator.capabilities_mapa import _map_command

MAPA = {"map_context": {"layers": [{"id": "layer-1", "name": "Lotes"}, {"id": "raster-2", "name": "NDVI"}]}}


@pytest.mark.asyncio
async def test_una_orden_valida_se_acumula_para_el_mapa():
    out = await _map_command(None, dict(MAPA), {"op": "set_opacity", "layer_id": "raster-2", "args": {"opacity": 0.3}})
    assert out.success and "«NDVI»" in out.observation
    working = {**MAPA, **out.delta}
    out2 = await _map_command(None, working, {"op": "reorder", "layer_id": "raster-2",
                                              "args": {"to": "below", "relative_to": "layer-1"}})
    assert [c["op"] for c in out2.delta["map_commands"]] == ["set_opacity", "reorder"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args,motivo", [
    ({"op": "set_opacity", "layer_id": "raster-2", "args": {"opacity": 3}}, "opacity"),
    ({"op": "set_visibility", "layer_id": "capa-inventada", "args": {"visible": False}}, "no está en el mapa"),
    ({"op": "reorder", "layer_id": "raster-2", "args": {"to": "above", "relative_to": "otra"}}, "no está en el mapa"),
    ({"op": "set_label", "args": {"field": "x"}}, "necesita `layer_id`"),
    ({"op": "zoom_to"}, "necesita `layer_id` o `args.bbox`"),
])
async def test_una_orden_invalida_vuelve_con_el_motivo(args, motivo):
    out = await _map_command(None, dict(MAPA), args)
    assert not out.success and motivo in out.observation
    assert not out.delta


@pytest.mark.asyncio
async def test_activa_y_bbox_son_referencias_validas():
    assert (await _map_command(None, dict(MAPA), {"op": "zoom_to", "layer_id": "activa"})).success
    assert (await _map_command(None, dict(MAPA), {"op": "zoom_to", "args": {"bbox": [-74.2, 4.5, -74.0, 4.7]}})).success


@pytest.mark.asyncio
async def test_el_id_entre_corchetes_del_listado_se_acepta():
    """V5 FH.1: el LLM copió el id como aparece en el prompt, `[layer-1]`, y se rechazaba."""
    out = await _map_command(None, dict(MAPA), {"op": "set_opacity", "layer_id": "[layer-1]", "args": {"opacity": 0.5}})
    assert out.success and out.delta["map_commands"][0]["layer_id"] == "layer-1"


@pytest.mark.asyncio
async def test_activa_se_narra_con_el_nombre_de_la_capa():
    ctx = {"map_context": {"layers": [{"id": "layer-1", "name": "Lotes", "is_active": True}]}}
    out = await _map_command(None, ctx, {"op": "set_opacity", "layer_id": "activa", "args": {"opacity": 0.5}})
    assert "«Lotes»" in out.observation


@pytest.mark.asyncio
async def test_un_dataset_se_acepta_como_la_capa_que_lo_lleva_o_la_del_turno():
    """V5 FH.3: «zoom_to ds_…» sobre el resultado del turno se rechazaba («no está en el mapa»)."""
    working = {"map_context": {"layers": [{"id": "layer-1", "name": "Área 1", "dataset_id": "ds_aaaaaaaaaaaaaaaa"}]},
               "result_layer_ref": {"id": "ds_bbbbbbbbbbbbbbbb"}}
    out = await _map_command(None, dict(working), {"op": "zoom_to", "layer_id": "ds_aaaaaaaaaaaaaaaa"})
    assert out.success and out.delta["map_commands"][0]["layer_id"] == "layer-1"
    out = await _map_command(None, dict(working), {"op": "zoom_to", "layer_id": "[ds_bbbbbbbbbbbbbbbb]"})
    assert out.success and out.delta["map_commands"][0]["layer_id"] == "activa"
    out = await _map_command(None, dict(working), {"op": "zoom_to", "layer_id": "ds_cccccccccccccccc"})
    assert not out.success and "no está en el mapa" in out.observation
