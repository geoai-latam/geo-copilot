"""Fase A: map_context — el agente recibe el estado real del mapa."""

from geo_copilot.api.models import MapContext, QueryRequest
from geo_copilot.core.formatters import format_map_context


def test_query_request_accepts_map_context():
    req = QueryRequest(
        query="hazle un buffer a esta capa",
        session_id="s1",
        map_context={
            "layers": [
                {
                    "id": "lyr-1",
                    "name": "Manzanas IGAC",
                    "geometry_type": "Polygon",
                    "feature_count": 320,
                    "fields": ["codigo", "area_m2"],
                    "visible": True,
                    "is_active": True,
                }
            ],
            "viewport": {"bbox": None, "zoom": 14, "crs": "EPSG:4326"},
        },
    )
    assert req.map_context is not None
    assert req.map_context.layers[0].is_active is True


def test_format_map_context_lists_layers_and_selection():
    mc = {
        "layers": [
            {"id": "a", "name": "Parcelas", "geometry_type": "Polygon",
             "feature_count": 10, "fields": ["area"], "visible": True, "is_active": True},
            {"id": "b", "name": "Vías", "geometry_type": "LineString",
             "feature_count": 5, "fields": ["tipo"], "visible": False, "is_active": False},
        ],
        "selected_feature": {"layer_id": "Parcelas", "properties": {"codigo": "001", "area": 123}},
        "viewport": {"bbox": [-74.1, 4.6, -74.0, 4.7], "zoom": 14, "crs": "EPSG:4326"},
    }
    out = format_map_context(mc)
    assert "Parcelas" in out
    assert ">> ACTIVA" in out
    assert "FEATURE SELECCIONADA" in out
    assert "ZONA VISIBLE" in out
    assert "codigo=001" in out


def test_format_map_context_empty():
    assert format_map_context(None) == ""
    assert format_map_context({}) == ""


def test_model_validates_active_layer_geojson_field():
    # La capa activa puede traer geojson (A4a) — el modelo lo acepta como dict.
    mc = MapContext(layers=[{
        "id": "x", "name": "X", "feature_count": 1, "is_active": True,
        "data": {"type": "FeatureCollection", "features": []},
    }])
    assert mc.layers[0].data["type"] == "FeatureCollection"
