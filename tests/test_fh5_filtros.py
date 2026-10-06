"""FH.5 — filtros de capa: la capa filtrada ES su subconjunto (mapa, agente y herramientas)."""
from __future__ import annotations

import pytest

from geo_copilot.core.formatters import format_map_context
from geo_copilot.orchestrator.capabilities_mapa import _map_command
from geo_copilot.platform.seleccion import (
    PredicadoInvalido,
    filtrar_capa,
    filtro_de_dataset,
    sql_filtro,
)

LOTES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, 0]},
     "properties": {"lotcodigo": f"00{i}", "estrato": e, "uso": u}}
    for i, (e, u) in enumerate([(1, "res"), (3, "com"), (3, "res"), (2, "res"), (3, "ind")])]}


def _working(filtro=None):
    capa = {"id": "layer-1", "name": "Lotes", "fields": ["lotcodigo", "estrato", "uso"], "feature_count": 5}
    if filtro:
        capa["filtro"] = filtro
    return {"map_context": {"layers": [capa]}, "map_layers": {"layer-1": {"data": LOTES, "name": "Lotes"}}}


def test_filtro_en_memoria_y_en_sql():
    f = [{"field": "estrato", "op": "=", "value": 3}, {"field": "uso", "op": "in", "value": ["res", "com"]}]
    assert [x["properties"]["lotcodigo"] for x in filtrar_capa(LOTES, f)["features"]] == ["001", "002"]
    cond, params = sql_filtro(f, ["estrato", "uso"])
    assert cond == '((s."estrato")::double precision = $1) AND (s."uso"::text = ANY($2::text[]))'
    assert params == (3.0, ["res", "com"])
    with pytest.raises(PredicadoInvalido):
        sql_filtro([{"field": "x; drop", "op": "=", "value": 1}], ["estrato"])


@pytest.mark.asyncio
async def test_el_agente_filtra_y_sabe_cuantos_quedan_y_la_capa_ya_es_el_subconjunto():
    out = await _map_command(None, _working(), {"op": "set_filter", "layer_id": "layer-1",
                                                "args": {"where": [{"field": "estrato", "op": "=", "value": 3}]}})
    assert out.success
    orden = out.delta["map_commands"][0]
    assert orden["op"] == "set_filter" and orden["args"]["count"] == 3
    # en este mismo turno: el contexto y los datos en memoria ya son el subconjunto
    assert out.delta["map_context"]["layers"][0]["filtro"] == [{"field": "estrato", "op": "=", "value": 3}]
    assert len(out.delta["map_layers"]["layer-1"]["data"]["features"]) == 3
    assert len(out.delta["map_layers"]["layer-1"]["completa"]["features"]) == 5


@pytest.mark.asyncio
async def test_retocar_el_filtro_cuenta_sobre_la_capa_entera_y_quitarlo_la_devuelve():
    w = _working([{"field": "estrato", "op": "=", "value": 3}])
    w["map_layers"]["layer-1"] = {"data": filtrar_capa(LOTES, w["map_context"]["layers"][0]["filtro"]),
                                  "completa": LOTES, "name": "Lotes"}
    out = await _map_command(None, w, {"op": "set_filter", "layer_id": "layer-1",
                                       "args": {"where": [{"field": "estrato", "op": ">=", "value": 2}]}})
    assert out.delta["map_commands"][0]["args"]["count"] == 4  # sobre las 5, no sobre las 3 filtradas
    sin = await _map_command(None, w, {"op": "set_filter", "layer_id": "layer-1", "args": {"where": []}})
    assert sin.success and len(sin.delta["map_layers"]["layer-1"]["data"]["features"]) == 5


@pytest.mark.asyncio
async def test_un_campo_que_no_existe_o_que_no_deja_nada_lo_dice():
    mal = await _map_command(None, _working(), {"op": "set_filter", "layer_id": "layer-1",
                                                "args": {"where": [{"field": "area", "op": ">", "value": 1}]}})
    assert not mal.success and "«area» no existe" in mal.observation
    nada = await _map_command(None, _working(), {"op": "set_filter", "layer_id": "layer-1",
                                                 "args": {"where": [{"field": "estrato", "op": "=", "value": 9}]}})
    assert "ningún elemento" in nada.observation and not nada.delta
    assert "«estrato» va de 1 a 3" in nada.observation  # el hecho para corregir el valor
    texto = await _map_command(None, _working(), {"op": "set_filter", "layer_id": "layer-1",
                                                  "args": {"where": [{"field": "uso", "op": "=", "value": "residencial"}]}})
    assert "«uso» toma valores como: res, com, ind" in texto.observation


def test_el_llm_ve_la_capa_filtrada_y_su_conteo():
    txt = format_map_context({"layers": [{"id": "layer-1", "name": "Lotes", "feature_count": 5, "fields": ["estrato"],
                                          "filtro": [{"field": "estrato", "op": "=", "value": 3}], "filtro_count": 3}]})
    assert ("FILTRADA: solo muestra los que cumplen estrato = 3 (3 de 5); para las herramientas la capa es "
            "ese subconjunto") in txt


def test_el_filtro_de_un_dataset_se_encuentra_por_su_capa():
    w = {"map_context": {"layers": [{"id": "l1", "dataset_id": "ds_a", "filtro": [{"field": "x", "op": "=", "value": 1}]},
                                    {"id": "l2", "dataset_id": "ds_b"}]}}
    assert filtro_de_dataset("ds_a", w) == [{"field": "x", "op": "=", "value": 1}]
    assert filtro_de_dataset("ds_b", w) is None


@pytest.mark.asyncio
async def test_agregar_medida_usa_el_dataset_entero_aunque_la_capa_este_filtrada_y_actualiza_esa_capa(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.platform.workspace import context as wsctx
    from geo_copilot.platform.workspace import ops

    ref = SimpleNamespace(id="ds_aaaaaaaaaaaaaaaa", name="Lotes", feature_count=5, fields=[SimpleNamespace(name="area_m2")],
                          storage=SimpleNamespace(kind="workspace-table"), model_dump=lambda mode=None: {"id": "ds_aaaaaaaaaaaaaaaa"})
    medida = AsyncMock(return_value=ops.Resultado(ref, {"campo": "area_m2", "misma_capa": True}))
    monkeypatch.setattr(ops, "agregar_medida", medida)
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(to_geojson=AsyncMock(return_value=LOTES)))
    working = {"session_id": "s", "map_context": {"layers": [
        {"id": "layer-1", "dataset_id": "ds_aaaaaaaaaaaaaaaa", "filtro": [{"field": "estrato", "op": "=", "value": 3}]}]}}
    out = await ce._agregar_medida(None, working, {"dataset": "layer-1", "measure": "area"})
    assert medida.await_args.args[2] == "ds_aaaaaaaaaaaaaaaa"  # el de la capa, no el subconjunto filtrado
    assert '"dataset_actualizado"' in out.observation and '"nuevo_dataset"' not in out.observation
    assert out.delta["target_layer_id"] == "layer-1"


def test_la_capa_del_turno_que_ya_esta_en_el_mapa_se_actualiza_en_su_sitio_y_las_filas_feature_se_aplanan():
    from geo_copilot.platform.artefactos import _capa_con_dataset, _filas

    r = {"map_context": {"layers": [{"id": "layer-1", "dataset_id": "ds_a"}]}}
    assert _capa_con_dataset(r, "ds_a") == "layer-1" and _capa_con_dataset(r, "ds_b") is None
    filas = _filas({"data": {"results": [{"type": "Feature", "id": 0, "properties": {"lotcodigo": "L0", "area_m2": 10},
                                          "geometry": {"type": "Point", "coordinates": [0, 0]}}]}})
    assert filas == [{"lotcodigo": "L0", "area_m2": 10}]


def test_la_capa_de_un_script_se_nombra_por_su_entrada_y_operacion():
    from geo_copilot.orchestrator.nodes.python_agent import _nombre_resultado

    assert _nombre_resultado("Lotes Manzana 008510017", "area") == "Lotes Manzana 008510017 · área"
    assert _nombre_resultado("Vías", "other") == "Vías (resultado)"
    assert _nombre_resultado(None, None) == "Resultado (resultado)"


def test_filtrar_conserva_la_identidad_del_mapa_de_cada_elemento():
    """FH.7: sin `id`, un elemento es su índice en la capa ENTERA; filtrando no debe cambiar."""
    from geo_copilot.platform.seleccion import filtrar_capa

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"n": 1}, "geometry": None},
        {"type": "Feature", "properties": {"n": 5}, "geometry": None},
        {"type": "Feature", "id": 70, "properties": {"n": 9}, "geometry": None}]}
    out = filtrar_capa(fc, [{"field": "n", "op": ">", "value": 2}])
    assert [f["id"] for f in out["features"]] == [1, 70]
    assert "id" not in fc["features"][1]  # no muta la capa original


@pytest.mark.asyncio
@pytest.mark.parametrize("filtro", [None, [{"field": "estrato", "op": "=", "value": 3}]])
async def test_la_copia_del_mapa_de_la_capa_medida_lleva_el_campo_nuevo(monkeypatch, filtro):
    """V5 (auditoría F4): «colorea los lotes por área» en el NAVEGADOR — ws_add_measure añadía area_m2 al
    dataset pero la simbología resolvía la copia del mapa (sin el campo) y coloreaba de un solo color.
    La copia del mapa se actualiza (con el filtro de la capa, si lo tiene) y la resolución de capa la ve."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.orchestrator.layer_resolution import resolver_capa
    from geo_copilot.platform.workspace import context as wsctx
    from geo_copilot.platform.workspace import ops

    medidos = {"type": "FeatureCollection", "features": [
        {**f, "properties": {**f["properties"], "area_m2": 100.0 + i}} for i, f in enumerate(LOTES["features"])]}
    ref = SimpleNamespace(id="ds_aaaaaaaaaaaaaaaa", name="Lotes", feature_count=5, fields=[SimpleNamespace(name="area_m2")],
                          storage=SimpleNamespace(kind="workspace-table"), model_dump=lambda mode=None: {"id": "ds_aaaaaaaaaaaaaaaa"})
    monkeypatch.setattr(ops, "agregar_medida", AsyncMock(return_value=ops.Resultado(ref, {"campo": "area_m2", "misma_capa": True})))
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(to_geojson=AsyncMock(return_value=medidos)))
    capa_mc = {"id": "layer-1", "dataset_id": "ds_aaaaaaaaaaaaaaaa", **({"filtro": filtro} if filtro else {})}
    working = {"session_id": "s", "map_context": {"layers": [capa_mc]},
               "map_layers": {"layer-1": {"name": "Lotes", "data": LOTES}, "otra": {"name": "Otra", "data": LOTES}}}

    out = await ce._agregar_medida(None, working, {"dataset": "layer-1", "measure": "area"})
    working.update(out.delta)  # lo que hace el bucle con el delta

    capa = resolver_capa(working)
    assert capa is not None and capa.origen == "target" and capa.layer_id == "layer-1"
    assert all("area_m2" in f["properties"] for f in capa.geojson["features"])
    esperados = 3 if filtro else 5  # la vista del mapa: con el filtro de la capa
    assert len(capa.geojson["features"]) == esperados
    assert working["map_layers"]["otra"]["data"] is LOTES  # las demás capas, intactas
