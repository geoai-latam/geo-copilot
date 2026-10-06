"""Sin BD, el nodo GIS dice qué capas del mapa tiene el turno en memoria (fh5, 2026-10-06).

El bucle respondía «no puedo acceder a la base de datos» aunque la capa filtrada estaba en el turno.
El error de `query_database` lleva ahora ese HECHO; qué hacer con él lo decide el modelo.
"""
from unittest.mock import MagicMock

import pytest

from geo_copilot.orchestrator.nodes import gis_agent as nodo

LOTES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]}, "properties": {"uso": "res"}}
    for _ in range(10)]}


@pytest.mark.asyncio
async def test_sin_bd_el_error_nombra_las_capas_en_memoria():
    graph = MagicMock()
    graph.db_pool = None
    out = await nodo.run(graph, {"query": "¿cuántos lotes hay por uso?", "session_id": "s1",
                                 "map_layers": {"layer-1": {"data": LOTES, "name": "Lotes Manzana 004503004"}}})
    assert out["error"].startswith("No database connection")
    assert "«Lotes Manzana 004503004» (10 elementos)" in out["error"]


@pytest.mark.asyncio
async def test_sin_bd_y_sin_capas_el_error_no_cambia():
    graph = MagicMock()
    graph.db_pool = None
    out = await nodo.run(graph, {"query": "cuenta los lotes", "session_id": "s1"})
    assert out["error"] == "No database connection"


def test_las_capas_vacias_o_sin_datos_no_se_mencionan():
    estado = {"map_layers": {"a": {"data": {"features": []}, "name": "Vacía"}, "b": {"name": "Sin datos"},
                             "c": "basura"}}
    assert nodo._capas_en_memoria(estado) == ""
