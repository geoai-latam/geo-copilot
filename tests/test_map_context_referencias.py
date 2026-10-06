"""S4.4 (F4) — map_context por referencias: el agente ve también las capas raster.

E4.5: con una capa NDVI cargada, «¿qué valor tiene el NDVI aquí?» — antes el
agente no sabía que la capa existía (el map_context solo describía capas con
features). Ahora cada capa viaja por referencia con su tipo, su procedencia y su
extensión, y el punto que el usuario marcó llega como «aquí». Qué hacer con eso
lo decide el LLM (aquí solo se comprueba que los HECHOS le llegan).
"""
from __future__ import annotations

from geo_copilot.api.models import MapContext
from geo_copilot.core.formatters import format_map_context
from geo_copilot.platform.artefactos import construir_artefactos

NDVI = {
    "id": "l_ndvi", "name": "NDVI 2026-01-17", "kind": "raster-xyz", "visible": True, "is_active": True,
    "url": "/api/v1/proxy/mcp/imagery/tiles/S2B_18NWL_20260117_0_L2A/{z}/{x}/{y}.png",
    "origin": {"capability": "mcp.imagery.imagery_ndvi", "arguments": {"date_from": "2026-01-01"}},
    "legend": {"type": "ramp", "field": "NDVI", "min": 0.05, "max": 0.85},
    "bbox": [-74.2, 4.5, -74.0, 4.7],
}
LOTES_MVT = {"id": "l_lotes", "name": "Lotes", "kind": "vector-mvt", "geometry_type": "Polygon",
             "feature_count": 41033, "fields": ["lotcodigo"], "dataset_id": "ds_0123456789abcdef"}


def test_el_contrato_de_la_api_acepta_capas_por_referencia_y_el_punto():
    ctx = MapContext.model_validate({"layers": [NDVI, LOTES_MVT], "clicked_point": {"lon": -74.1, "lat": 4.6}})
    assert ctx.layers[0].kind == "raster-xyz" and ctx.layers[0].origin["capability"].endswith("imagery_ndvi")
    assert ctx.clicked_point is not None and ctx.clicked_point.lat == 4.6


def test_el_prompt_describe_el_raster_con_su_origen_y_el_punto_marcado():
    bloque = format_map_context({"layers": [NDVI, LOTES_MVT], "clicked_point": {"lon": -74.1, "lat": 4.6}})
    linea = next(x for x in bloque.splitlines() if "NDVI 2026-01-17" in x)
    assert "RASTER (raster-xyz)" in linea
    assert "mcp.imagery.imagery_ndvi" in linea          # de qué tool salió
    assert "S2B_18NWL_20260117_0_L2A" in linea          # la escena, en su plantilla de teselas
    assert "0.05–0.85" in linea and "-74.2000" in linea  # rampa y extensión
    assert "features" not in linea                      # un raster no tiene features
    assert "lon -74.100000, lat 4.600000" in bloque
    # la capa vectorial sigue como antes
    assert "41033 features" in bloque


def test_la_capa_raster_de_un_mcp_lleva_su_procedencia():
    prov = {"capability": "mcp.imagery.imagery_ndvi", "produced_at": "2026-09-25T00:00:00Z",
            "arguments": {"date_from": "2026-01-01"}}
    arts = construir_artefactos(
        {"intent": "connected_service", "external_imagery": {
            "service_url": NDVI["url"], "name": "NDVI", "provenance": prov,
            "extent": {"xmin": -74.2, "ymin": 4.5, "xmax": -74.0, "ymax": 4.7}}},
        layer_ref=None, tiles=None, target_layer_id=None, row_count=0)
    ref = arts[0].layer
    assert ref.provenance.capability == "mcp.imagery.imagery_ndvi"
    assert ref.provenance.arguments == {"date_from": "2026-01-01"}
