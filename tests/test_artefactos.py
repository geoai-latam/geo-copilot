"""F4 (S4.1) — el resultado del grafo sale como artefactos del contrato.

Cada caso es una forma real de respuesta de hoy (capa chica, capa teselada,
re-estilo, imagery MCP, ImageServer, tabla, gráfico, estadísticas, servicios)
y lo que el frontend debe recibir para dibujarla.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from geo_copilot.platform.artefactos import construir_artefactos, estilo_de
from geo_copilot.platform.contracts import ArtifactBundle

FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]},
     "properties": {"lotcodigo": "A", "area": 10}}]}
REF = {"id": "ds_0123456789abcdef", "name": "Lotes", "kind": "vector", "provider": "core", "crs": "EPSG:4326",
       "storage": {"kind": "workspace-table", "schema_name": "ws_abc", "table": "d_0123456789abcdef"},
       "provenance": {"capability": "core.query_data", "produced_at": "2026-09-25T00:00:00Z"},
       "feature_count": 1}
SYMB = {"layer_name": "layer_x", "layer_title": "Lotes por área", "geometry_type": "Point",
        "symbology_type": "graduated_colors", "classification_field": "area",
        "classification_method": "quantile", "color_scheme": "Blues", "num_classes": 5,
        "marker": {"type": "circle", "color": "#08519c", "size": 8, "opacity": 1, "stroke_color": "#fff",
                   "stroke_width": 1},
        "class_breaks": [{"min_value": 0, "max_value": 10, "label": "0–10", "color": "#08519c", "count": 1}],
        "statistics": {"mean": 10}, "legend": {"title": "x"}, "reasoning": "numérico"}


def _kinds(arts):
    return [a.kind for a in arts]


def test_capa_chica_con_estilo_y_su_tabla_sin_geometria():
    res = {"intent": "query_data", "geojson": FC, "symbology": SYMB, "sql": "SELECT 1",
           "data": {"results": [{"lotcodigo": "A", "area": 10, "geom_geojson": "{...}"}]},
           "visualization": {"type": "map"}}
    arts = construir_artefactos(res, layer_ref=REF, tiles=None, target_layer_id=None, row_count=1)
    assert _kinds(arts) == ["layer", "table"]
    capa, tabla = arts
    assert capa.inline == FC and capa.tiles is None and capa.replaces is None
    assert capa.layer.style.symbology_type == "graduated_colors" and capa.layer.style.layer_title == "Lotes por área"
    assert capa.layer.style.class_breaks[0].count == 1
    assert tabla.columns == ["lotcodigo", "area"] and tabla.rows_ref == "ds_0123456789abcdef"
    ArtifactBundle(artifacts=arts)  # cumple el contrato completo


def test_capa_grande_va_por_teselas_sin_inline():
    tiles = {"url": "/api/v1/tiles/ws/s/ds_0123456789abcdef/{z}/{x}/{y}.pbf", "source_layer": "dataset",
             "fields": ["lotcodigo"]}
    arts = construir_artefactos({"intent": "query_data", "geojson": FC}, layer_ref=REF, tiles=tiles,
                                target_layer_id=None, row_count=0)
    assert arts[0].inline is None and arts[0].tiles.url_template.endswith(".pbf")


def test_reestilo_con_datos_reemplaza_la_capa_objetivo():
    arts = construir_artefactos({"intent": "apply_symbology", "geojson": FC, "symbology": SYMB},
                                layer_ref=REF, tiles=None, target_layer_id="layer-7", row_count=0)
    assert arts[0].replaces == "layer-7"


def test_reestilo_sin_datos_es_una_orden_al_mapa():
    arts = construir_artefactos({"intent": "apply_symbology", "symbology": SYMB},
                                layer_ref=None, tiles=None, target_layer_id="layer-7", row_count=0)
    assert _kinds(arts) == ["map_command"]
    cmd = arts[0].command
    assert cmd.op == "set_style" and cmd.layer_id == "layer-7"
    assert cmd.args.style.classification_field == "area"


def test_raster_de_un_servidor_mcp_con_leyenda():
    img = {"service_url": "/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png?rescale=0,1", "name": "NDVI",
           "extent": {"xmin": -74.2, "ymin": 4.5, "xmax": -74.0, "ymax": 4.7},
           "legend": {"type": "ramp", "field": "NDVI", "min": 0, "max": 1}}
    arts = construir_artefactos({"intent": "connected_service", "external_imagery": img,
                                 "data": {"results": [{"métrica": "mean", "valor": 0.4}]}},
                                layer_ref=None, tiles=None, target_layer_id=None, row_count=1)
    assert _kinds(arts) == ["layer", "stats"]
    r = arts[0].layer
    assert r.kind == "raster" and r.provider == "mcp:imagery" and r.storage.kind == "raster-tiles"
    assert r.storage.legend["field"] == "NDVI" and r.bbox == (-74.2, 4.5, -74.0, 4.7)
    assert arts[1].items[0].label == "mean"


def test_imageserver_arcgis():
    arts = construir_artefactos({"intent": "select_service", "external_imagery": {
        "service_url": "https://x.gov.co/arcgis/rest/services/Orto/ImageServer", "name": "Orto"}},
        layer_ref=None, tiles=None, target_layer_id=None, row_count=0)
    assert arts[0].layer.storage.kind == "arcgis-image"


def test_grafico_si_el_nodo_lo_pidio_con_ejes():
    res = {"intent": "analyze", "data": {"results": [{"uso": "res", "n": 3}]},
           "visualization": {"type": "chart", "chart_type": "bar", "x_axis": "uso", "y_axis": "n"}}
    arts = construir_artefactos(res, layer_ref=None, tiles=None, target_layer_id=None, row_count=1)
    # el gráfico Y sus cifras: el usuario que pide «tabla y gráfico» ve ambas
    assert _kinds(arts) == ["chart", "table"] and arts[0].spec.x_key == "uso"
    assert arts[1].preview == [{"uso": "res", "n": 3}]
    # sin ejes no se inventa un gráfico: tabla
    res["visualization"] = {"type": "chart", "chart_type": "bar"}
    assert _kinds(construir_artefactos(res, layer_ref=None, tiles=None, target_layer_id=None, row_count=1)) == ["table"]


def test_servicios_solo_si_hubo_busqueda_nueva():
    svc = [{"title": "Parques", "url": "https://x/FeatureServer", "type": "FeatureServer", "layer_count": 2}]
    assert construir_artefactos({"found_services": svc}, layer_ref=None, tiles=None,
                                target_layer_id=None, row_count=0) == []
    arts = construir_artefactos({"found_services": svc, "new_search_executed": True}, layer_ref=None,
                                tiles=None, target_layer_id=None, row_count=0)
    assert arts[0].items[0].name == "Parques" and arts[0].items[0].layer_count == 2


def test_las_tarjetas_de_servicios_dicen_de_quien_son_y_cuanto_se_usan():
    """V5: la tarjeta del chat mostraba «services7» (un trozo del dominio) como organización."""
    svc = [{"name": "Cartografía básica Guaduas", "url": "https://services7.arcgis.com/x/FeatureServer",
            "type": "FeatureServer", "credits": "Corporación Autónoma Regional de Cundinamarca CAR", "views": 13286},
           {"name": "Sin créditos", "url": "https://s/FeatureServer", "type": "FeatureServer", "org": "IGAC",
            "views": None}]
    (arts,) = construir_artefactos({"found_services": svc, "new_search_executed": True}, layer_ref=None,
                                   tiles=None, target_layer_id=None, row_count=0)
    assert (arts.items[0].credits, arts.items[0].views) == ("Corporación Autónoma Regional de Cundinamarca CAR", 13286)
    assert (arts.items[1].credits, arts.items[1].views) == ("IGAC", None)


def test_sin_workspace_la_capa_viaja_inline():
    arts = construir_artefactos({"intent": "query_data", "geojson": FC, "layer_name": "Lotes"},
                                layer_ref=None, tiles=None, target_layer_id=None, row_count=0)
    assert arts[0].layer.id.startswith("tmp_") and arts[0].layer.storage.kind == "geojson-inline"


def test_sin_workspace_ni_nombre_la_capa_se_llama_como_la_consulta():
    # Igual que la capa del workspace (query[:60]): no un «Resultado» genérico.
    arts = construir_artefactos({"intent": "query_data", "geojson": FC, "query": "lotes de Chapinero"},
                                layer_ref=None, tiles=None, target_layer_id=None, row_count=0)
    assert arts[0].layer.name == "lotes de Chapinero"


def test_simbologia_fuera_de_contrato_no_rompe_la_capa():
    assert estilo_de({"symbology_type": "inventada"}) is None
    arts = construir_artefactos({"intent": "query_data", "geojson": FC, "symbology": {"symbology_type": "x"}},
                                layer_ref=REF, tiles=None, target_layer_id=None, row_count=0)
    assert arts[0].layer.style is None


def test_la_respuesta_de_query_cumple_el_contrato(monkeypatch):
    """La respuesta HTTP real valida contra el QueryResponse del contrato (sin los campos legados)."""
    from fastapi.testclient import TestClient

    from geo_copilot.api import dependencies as deps
    from geo_copilot.api.app import create_app
    from geo_copilot.orchestrator.conversation import ConversationManager
    from geo_copilot.platform.contracts import QueryResponse

    monkeypatch.setattr(deps, "get_app_state", lambda: SimpleNamespace(dataset_store=None))
    monkeypatch.setattr("geo_copilot.api.websocket.send_result", AsyncMock())
    monkeypatch.setattr("geo_copilot.api.websocket.send_status", AsyncMock())
    graph = MagicMock()
    graph.process = AsyncMock(return_value={
        "success": True, "message": "listo", "intent": "query_data", "geojson": FC, "symbology": SYMB,
        "sql": "SELECT 1", "data": {"results": [{"lotcodigo": "A"}]}, "visualization": {"type": "map"},
        "reasoning_trace": [{"step": 0, "kind": "decision", "agent": "router", "detail": "query_data",
                             "success": True}],
    })
    app = create_app()
    manager = ConversationManager()
    manager.create_session("sess-c")
    app.dependency_overrides[deps.get_conversation_manager] = lambda: manager
    app.dependency_overrides[deps.get_agent_graph] = lambda: graph
    body = TestClient(app).post("/api/v1/query/", json={"query": "lotes", "session_id": "sess-c"}).json()
    contrato = QueryResponse.model_validate({k: v for k, v in body.items() if k not in ("results", "visualizations")})
    assert [a.kind for a in contrato.artifacts] == ["layer", "table"]
    assert contrato.sql == "SELECT 1" and contrato.status == "completed"


def test_la_capa_del_mapa_inyectada_como_externa_no_se_reemite_cada_turno():
    """V5 F4: el map_context inyecta la capa activa como `external_geojson` (A4a) en
    CADA turno; el constructor la tomaba como resultado y un simple conteo volvía a
    añadir los lotes al mapa (capas duplicadas). Una capa nueva sale de `geojson`."""
    res = {"intent": "query_data", "external_geojson": FC, "geojson": None,
           "data": {"results": [{"n": 15}]}}
    arts = construir_artefactos(res, layer_ref=None, tiles=None, target_layer_id=None, row_count=1)
    assert _kinds(arts) == ["table"]


def test_un_turno_con_grafico_y_luego_tabla_entrega_los_dos():
    """V5 F4: el bucle generó un gráfico del área (paso 2) y luego una tabla de clases
    (paso 6); `data`/`visualization` solo guardan el último y el gráfico se perdía."""
    grafico = {"data": {"results": [{"lote": "001", "area_m2": 226.5}, {"lote": "002", "area_m2": 90.1}]},
               "visualization": {"type": "chart", "chart_type": "bar", "x_axis": "lote", "y_axis": "area_m2"}}
    tabla = {"data": {"results": [{"lote": "001", "clase": 3}, {"lote": "002", "clase": 1}]},
             "visualization": {"type": "table"}}
    res = {"intent": "query_data", **tabla, "analiticos": [grafico, tabla]}
    arts = construir_artefactos(res, layer_ref=None, tiles=None, target_layer_id=None, row_count=2)
    assert _kinds(arts) == ["chart", "table", "table"]
    assert arts[0].data[0]["area_m2"] == 226.5 and arts[2].preview[0]["clase"] == 3


def test_sin_data_final_se_entregan_todos_los_analiticos():
    grafico = {"data": {"results": [{"x": "a", "y": 1}]},
               "visualization": {"type": "chart", "chart_type": "bar", "x_axis": "x", "y_axis": "y"}}
    arts = construir_artefactos({"intent": "query_data", "analiticos": [grafico]}, layer_ref=None, tiles=None,
                                target_layer_id=None, row_count=0)
    assert _kinds(arts) == ["chart", "table"]


def test_las_ordenes_del_agente_al_mapa_salen_tipadas_y_en_orden():
    """FH.1: map_command del bucle → artefactos `map_command`; una fuera de contrato no llega."""
    res = {"intent": "map_control", "map_commands": [
        {"op": "zoom_to", "layer_id": "layer-1", "args": {}},
        {"op": "set_opacity", "layer_id": "raster-2", "args": {"opacity": 7}},   # fuera de contrato
        {"op": "set_visibility", "layer_id": "layer-3", "args": {"visible": False}, "reason": "estorba"},
    ]}
    arts = construir_artefactos(res, layer_ref=None, tiles=None, target_layer_id=None, row_count=0)
    assert [(a.command.op, a.command.layer_id) for a in arts] == [("zoom_to", "layer-1"), ("set_visibility", "layer-3")]
    assert arts[1].command.args.visible is False and arts[1].command.reason == "estorba"
