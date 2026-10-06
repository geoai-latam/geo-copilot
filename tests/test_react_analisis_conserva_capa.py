"""V5 F4 — un análisis sin geometría no borra la capa del turno.

«Trae los lotes de la manzana X y hazme una tabla y un gráfico con el área»:
el bucle ReAct cargaba los lotes (query_database) y luego analyze_layer
devolvía solo el gráfico — con ``geojson: None``. Ese None pisaba la capa en el
estado de trabajo: el usuario veía el gráfico pero ningún lote en el mapa (y si
el último análisis fallaba, ni siquiera la tabla).

Una transformación/análisis CONSERVA la geometría de entrada si no produce otra
(contrato de ``_TRANSFORM_CLEAR``); una herramienta que trae datos nuevos
(``_FRESH``) sí la limpia.
"""
from __future__ import annotations

from geo_copilot.orchestrator.react_tools import _FRESH, _TRANSFORM_CLEAR, _run_node

LOTES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]},
     "properties": {"lotcodigo": "A"}}]}

# Lo que devuelve el nodo python ante un análisis que produjo solo un gráfico.
SOLO_GRAFICO = {
    "geojson": None, "raw_data": [], "external_geojson": None, "has_external_data": True,
    "python_code": "chart = {...}", "active_data_source": "external",
    "data": {"results": [{"lotcodigo": "A", "area_m2": 1805.0}]},
    "visualization": {"type": "chart", "chart_type": "bar", "x_axis": "lotcodigo", "y_axis": "area_m2"},
}


def _nodo(salida):
    async def run(_graph, _state):
        return dict(salida)
    return run


def _estado():
    return {"geojson": LOTES, "external_geojson": LOTES, "raw_data": LOTES["features"],
            "has_external_data": True, "active_data_source": "external"}


async def test_un_analisis_sin_geometria_conserva_la_capa_cargada():
    out = await _run_node(None, _nodo(SOLO_GRAFICO), _estado(), {"intent": "analyze"}, clear=_TRANSFORM_CLEAR)
    estado = {**_estado(), **out.delta}
    assert estado["geojson"] == LOTES and estado["external_geojson"] == LOTES
    # y el resultado analítico sí llega
    assert estado["visualization"]["chart_type"] == "bar"
    assert estado["data"]["results"][0]["area_m2"] == 1805.0


async def test_una_transformacion_con_geometria_nueva_si_la_reemplaza():
    nueva = {"type": "FeatureCollection", "features": []}
    out = await _run_node(None, _nodo({"geojson": nueva, "raw_data": []}), _estado(),
                          {"intent": "spatial_operation"}, clear=_TRANSFORM_CLEAR)
    # un resultado VACÍO (no None) es un resultado: la intersección no dio nada
    assert out.delta["geojson"] == nueva


async def test_una_herramienta_de_datos_nuevos_si_limpia_la_capa_anterior():
    out = await _run_node(None, _nodo({"data": {"results": [{"n": 1}]}}), _estado(),
                          {"intent": "query_data"}, clear=_FRESH)
    assert out.delta["geojson"] is None


async def test_la_capa_conservada_sigue_siendo_el_mismo_dataset():
    """Si la geometría se conserva, su dataset del workspace también: sin él, /query
    volvía a materializar la misma capa (dataset duplicado) tras un gráfico."""
    ref = {"id": "ds_cccccccccccccccc", "name": "Lotes"}
    out = await _run_node(None, _nodo(SOLO_GRAFICO), {**_estado(), "result_layer_ref": ref},
                          {"intent": "analyze"}, clear=_TRANSFORM_CLEAR)
    assert "result_layer_ref" not in out.delta


async def test_una_geometria_nueva_si_invalida_el_dataset_previo():
    nueva = {"type": "FeatureCollection", "features": LOTES["features"]}
    out = await _run_node(None, _nodo({"geojson": nueva}), {**_estado(), "result_layer_ref": {"id": "ds_x"}},
                          {"intent": "spatial_operation"}, clear=_TRANSFORM_CLEAR)
    assert out.delta["result_layer_ref"] is None
