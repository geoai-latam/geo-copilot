"""FH.10 — herramientas SIG estándar como capacidades del agente: vistas, cortina y tiempo;
los rasters de un turno llegan todos, con la fecha que retratan."""
from __future__ import annotations

import pytest

from geo_copilot.core.formatters import format_map_context


def _img(fecha: str) -> dict:
    return {"service_url": f"/api/v1/proxy/mcp/imagery/tiles/{fecha}/{{z}}/{{x}}/{{y}}.png",
            "name": f"NDVI {fecha}", "time": fecha}


def test_los_dos_rasters_del_turno_llegan_en_orden_con_su_fecha():
    from geo_copilot.platform.artefactos import construir_artefactos

    arts = construir_artefactos({"external_imagery": _img("2026-06-12"), "imagery_previas": [_img("2026-03-10")]},
                                layer_ref=None, tiles=None, target_layer_id=None, row_count=0)
    capas = [a.layer for a in arts if a.kind == "layer"]
    assert [c.name for c in capas] == ["NDVI 2026-03-10", "NDVI 2026-06-12"]
    assert [c.time.start.date().isoformat() for c in capas] == ["2026-03-10", "2026-06-12"]


@pytest.mark.asyncio
async def test_comparar_acepta_capas_del_mapa_y_rasters_de_este_turno_y_rechaza_lo_demas():
    from geo_copilot.orchestrator.capabilities_mapa import _map_command

    working = {"map_context": {"layers": [{"id": "layer-1", "name": "Lotes"}]},
               "imagery_previas": [_img("2026-03-10")], "external_imagery": _img("2026-06-12")}
    ok = await _map_command(None, working, {"op": "compare", "args": {"left": "NDVI 2026-03-10", "right": "NDVI 2026-06-12"}})
    assert ok.success and ok.delta["map_commands"][-1]["op"] == "compare"
    mal = await _map_command(None, working, {"op": "compare", "args": {"left": "NDVI 2025-01-01", "right": "layer-1"}})
    assert not mal.success and "«NDVI 2026-03-10» (de este turno)" in mal.observation
    for op, args in (("save_view", {"nombre": "Finca"}), ("end_compare", {}), ("set_time", {"time": "2026-03-10", "play": True})):
        r = await _map_command(None, working, {"op": op, "args": args})
        assert r.success, (op, r.observation)


def test_vistas_cortina_y_tiempo_son_hechos_para_el_agente():
    txt = format_map_context({
        "layers": [{"id": "r1", "name": "NDVI 2026-03-10", "kind": "raster-xyz", "fecha": "2026-03-10"},
                   {"id": "r2", "name": "NDVI 2026-06-12", "kind": "raster-xyz", "fecha": "2026-06-12"}],
        "vistas": [{"nombre": "Finca", "bbox": [-74.1, 4.6, -74.0, 4.7]}],
        "comparacion": {"left": "r1", "right": "r2"},
        "serie_tiempo": {"fechas": ["2026-03-10", "2026-06-12"], "actual": "2026-06-12"}})
    assert "RASTER (raster-xyz); retrata 2026-03-10" in txt
    assert "VISTAS GUARDADAS" in txt and "«Finca» bbox [-74.10000" in txt
    assert "a la izquierda [r1] «NDVI 2026-03-10», a la derecha [r2] «NDVI 2026-06-12»" in txt
    assert "serie con fechas 2026-03-10, 2026-06-12; se muestra 2026-06-12" in txt


def test_la_fecha_de_la_escena_viaja_del_kit_al_contrato():
    from geo_mcp_kit.geo import raster_tiles

    from geo_copilot.platform.contracts.geo_result import RasterTilesArtifact

    art = raster_tiles("NDVI 2026-03-10", "/tiles/x/{z}/{x}/{y}.png", bounds=[0, 0, 1, 1], datetime="2026-03-10")
    assert RasterTilesArtifact.model_validate({**art, "crs": "EPSG:4326"}).datetime == "2026-03-10"
    assert "datetime" not in raster_tiles("x", "/t/{z}/{x}/{y}.png")


@pytest.mark.asyncio
async def test_fh12_punto_y_zona_visible_son_datasets_para_las_ws(monkeypatch):
    """Bench de deixis: el LLM pasa `punto` / `viewport` a las ws_* (como al hub MCP)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator import capabilities_espaciales as ce

    ingeridos: list[dict] = []

    async def ingest(sid, nombre, fc, **kw):
        ingeridos.append({"nombre": nombre, "geom": fc["features"][0]["geometry"], "cap": kw["provenance"].capability})
        return SimpleNamespace(id=f"ds_{len(ingeridos):016d}")

    monkeypatch.setattr(ce, "store_actual", lambda: SimpleNamespace(ingest_features=AsyncMock(side_effect=ingest)))
    working = {"session_id": "s", "map_context": {"clicked_point": {"lon": -74.05, "lat": 4.72},
                                                  "viewport": {"bbox": [-74.1, 4.6, -74.0, 4.7]}}}
    punto = await ce._resolver_crudo("punto", working)
    zona = await ce._resolver_crudo("viewport", working)
    assert punto != zona and await ce._resolver_crudo("punto", working) == punto  # una vez por turno
    assert ingeridos[0]["geom"] == {"type": "Point", "coordinates": [-74.05, 4.72]} and ingeridos[0]["cap"] == "core.punto"
    assert ingeridos[1]["geom"]["type"] == "Polygon" and ingeridos[1]["geom"]["coordinates"][0][2] == [-74.0, 4.7]
    assert await ce._resolver_crudo("punto", {"session_id": "s", "map_context": {}}) is None  # sin punto marcado


@pytest.mark.asyncio
async def test_fh12_colorear_la_seleccion_colorea_su_capa(monkeypatch):
    from geo_copilot.orchestrator import capabilities_core as cc
    from geo_copilot.orchestrator import react_tools

    vistos: list = []

    async def run_node(graph, fn, working, overrides, clear=()):
        vistos.append(overrides["target_layer_id"])
        return None

    monkeypatch.setattr(react_tools, "_run_node", run_node)
    working = {"map_context": {"layers": [{"id": "layer-1", "name": "Lotes",
                                           "seleccion": {"ids": [1, 2], "count": 2, "origin": "lasso"}}]},
               "map_layers": {"layer-1": {"data": {"type": "FeatureCollection", "features": []}}}}
    await cc._apply_symbology(None, working, {"request": "7 clases", "target_layer_id": "seleccion"})
    assert vistos == ["layer-1"]


@pytest.mark.asyncio
async def test_fh15_dibujo_es_lo_ultimo_que_dibujo_el_usuario():
    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.orchestrator.layer_resolution import capa_del_ultimo_dibujo

    working = {"map_context": {"layers": [
        {"id": "layer-1", "dataset_id": "ds_1111111111111111"},
        {"id": "layer-3", "dataset_id": "ds_3333333333333333", "origin": {"capability": "user.sketch"}},
        {"id": "layer-5", "dataset_id": "ds_5555555555555555", "origin": {"capability": "user.sketch"}},
        {"id": "layer-6", "dataset_id": "ds_6666666666666666"}]}}
    assert capa_del_ultimo_dibujo(working) == "layer-5"
    assert await ce._resolver_crudo("dibujo", working) == "ds_5555555555555555"
    assert capa_del_ultimo_dibujo({"map_context": {"layers": [{"id": "layer-1"}]}}) is None
