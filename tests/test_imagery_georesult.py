"""S3.6 — imagery-mcp habla el contrato GeoResult (G2): cualquier núcleo lo lleva al mapa."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "imagery_mcp"))

from imagery_mcp import georesult as gr

ESCENA = {"id": "S2B_MSIL2A_20260110T152639_R025_T18NWL", "datetime": "2026-01-10T15:26:39Z", "cloud_pct": 3.1}


def test_ndvi_capa_de_teselas_con_rescale_estadisticas_y_hechos():
    r = gr.ndvi({"scene": ESCENA, "stats": {"mean": 0.42, "min": -0.1, "max": 0.9},
                 "tiles": {"bounds": [-74.2, 4.5, -74.0, 4.7], "rescale": [0.05, 0.85]},
                 "reflectance": {"aviso": "DN crudo"}, "alternatives": [{"date": "2026-01-05", "cloud_pct": 8}]})
    assert r["geo_result"] == "1"
    capa = next(a for a in r["artifacts"] if a["kind"] == "raster_tiles")
    assert capa["tiles"] == f"/tiles/{ESCENA['id']}/{{z}}/{{x}}/{{y}}.png?rescale=0.05,0.85"
    assert capa["name"] == "NDVI 2026-01-10" and capa["legend"]["min"] == 0.05
    est = next(a for a in r["artifacts"] if a["kind"] == "stats")
    assert {"label": "mean", "value": 0.42} in est["items"]
    # lo que el analista necesita para fiarse del número viaja como hecho
    assert r["facts"]["reflectance"]["aviso"] == "DN crudo" and r["facts"]["scene"]["cloud_pct"] == 3.1
    assert r["facts"]["alternatives"][0]["date"] == "2026-01-05"


def test_zonal_devuelve_la_capa_enriquecida_con_ndvi_por_feature():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"lote": "A"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {"lote": "B"}},
    ]}
    r = gr.zonal({"scene": ESCENA, "rows": [{"feature_index": 1, "mean": 0.3, "std": 0.1, "min": 0.1, "max": 0.5}]}, fc)
    capa = r["artifacts"][0]
    assert capa["kind"] == "feature_collection" and capa["crs"] == "EPSG:4326"
    b = capa["data"]["features"][1]["properties"]
    assert b == {"lote": "B", "ndvi_mean": 0.3, "ndvi_std": 0.1, "ndvi_min": 0.1, "ndvi_max": 0.5}
    assert "ndvi_mean" not in capa["data"]["features"][0]["properties"]
    assert fc["features"][1]["properties"] == {"lote": "B"}  # no muta la entrada
    assert r["style_hint"]["field"] == "ndvi_mean" and r["facts"]["features_calculadas"] == 1
    assert "features_menores_que_el_pixel" not in r["facts"]


def test_zonal_declara_las_geometrias_menores_que_el_pixel():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}] * 2}
    r = gr.zonal({"scene": ESCENA, "rows": [
        {"feature_index": 0, "mean": 0.3, "std": 0.0, "min": 0.3, "max": 0.3, "px_compartidos": True},
        {"feature_index": 1, "mean": 0.3, "std": 0.0, "min": 0.3, "max": 0.3}]}, fc)
    assert r["facts"]["features_menores_que_el_pixel"]["cuantas"] == 1
    assert r["artifacts"][0]["data"]["features"][0]["properties"]["ndvi_px_compartidos"] is True


def test_cambio_y_composite_usan_sus_rutas_de_teselas():
    c = gr.change({"scene_a": ESCENA, "scene_b": {**ESCENA, "id": "S2B_B", "datetime": "2026-06-01"},
                   "diff_stats": {"mean": -0.05}, "tiles": {"rescale": [-0.5, 0.5]}})
    assert c["artifacts"][0]["tiles"].startswith(f"/tiles-diff/{ESCENA['id']}/S2B_B/")
    k = gr.composite({"scene": ESCENA, "combo": "false_color", "tiles": {}})
    assert k["artifacts"][0]["tiles"] == f"/tiles-rgb/{ESCENA['id']}/false_color/{{z}}/{{x}}/{{y}}.png"


def test_los_errores_del_motor_pasan_tal_cual():
    assert gr.ndvi({"error": "no hay escenas"}) == {"error": "no hay escenas"}
    assert gr.zonal({"error": "x"}, {}) == {"error": "x"}


def test_las_tools_declaran_meta_geo_y_solo_lectura():
    from imagery_mcp import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for nombre, t in tools.items():
        assert t.meta["geo"]["inputs"], nombre
        assert t.annotations.readOnlyHint and t.annotations.openWorldHint, nombre
    assert tools["imagery_zonal_stats"].meta["geo"]["outputs"] == ["feature_collection"]
