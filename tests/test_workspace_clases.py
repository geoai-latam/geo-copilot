"""La leyenda de una capa grande cuenta sobre la capa ENTERA, no sobre la muestra del estilo.

V5: la Malla Vial de Bogotá (136.956 líneas, por teselas) mostraba «M (1972) · B (1236) · R (1102)
· SD (690)»: los conteos de la muestra de 5.000 con que se diseñó el estilo. Contra PostGIS REAL,
con la misma regla de pintado del mapa (`_clase_de`).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from geo_copilot.core.config import get_settings
from geo_copilot.platform.contracts import Provenance
from geo_copilot.platform.workspace import DatasetStore
from geo_copilot.platform.workspace.clases import recontar_clases

pytestmark = [pytest.mark.integration, pytest.mark.postgis, pytest.mark.asyncio(loop_scope="session")]

PROC = Provenance(capability="test", produced_at=datetime.now(UTC))


def _fc(props: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.08 + i * 1e-4, 4.6]}, "properties": p}
        for i, p in enumerate(props)]}


@pytest.fixture
def capa_grande(monkeypatch):
    monkeypatch.setattr(get_settings(), "workspace_inline_max_features", 5)  # 12 elementos ya es «grande»


async def test_valores_unicos_cuentan_toda_la_capa(workspace_pool, capa_grande):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "vias", _fc([{"estado": "B"}] * 7 + [{"estado": "M"}] * 4 + [{"estado": None}]),
                                      crs="EPSG:4326", provenance=PROC)
    muestra = {"classification_field": "estado", "class_breaks": [
        {"label": "B", "color": "#1", "count": 3}, {"label": "M", "color": "#2", "count": 2}]}
    out = await recontar_clases(store, ws, ref.id, muestra)
    assert [c["count"] for c in out["class_breaks"]] == [7, 4]
    assert out["class_breaks"][0]["color"] == "#1"  # el estilo no cambia, solo los conteos


async def test_rangos_con_la_regla_del_mapa(workspace_pool, capa_grande):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    valores = [0, 0, 5, 9.99, 10, 15, 20, 20, 25, 30, 31, 7]
    ref = await store.ingest_features(ws, "num", _fc([{"v": x} for x in valores]), crs="EPSG:4326", provenance=PROC)
    estilo = {"classification_field": "v", "class_breaks": [
        {"label": "cero", "min_value": 0, "max_value": 0},       # degenerado: igualdad
        {"label": "0–10", "min_value": 0, "max_value": 10},      # [0, 10): los 0 ya los tomó «cero»
        {"label": "10–20", "min_value": 10, "max_value": 20},    # [10, 20)
        {"label": "20–30", "min_value": 20, "max_value": 30},    # la última: [20, 30]
    ]}
    out = await recontar_clases(store, ws, ref.id, estilo)
    # cero: 0,0 · [0,10): 5, 9.99, 7 · [10,20): 10, 15 · [20,30]: 20, 20, 25, 30 · fuera: 31
    assert [c["count"] for c in out["class_breaks"]] == [2, 3, 2, 4]


async def test_una_capa_chica_ya_tiene_conteos_exactos_y_no_se_toca(workspace_pool):
    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "chica", _fc([{"estado": "B"}] * 3), crs="EPSG:4326", provenance=PROC)
    estilo = {"classification_field": "estado", "class_breaks": [{"label": "B", "color": "#1", "count": 3}]}
    assert await recontar_clases(store, ws, ref.id, estilo) is estilo


async def test_el_contexto_del_sql_trae_los_valores_de_un_dataset_pequeno(workspace_pool):
    """V5 (sql/lugares): con el límite «UPZ Teusaquillo» el SQL filtró `w.nombre = 'Teusaquillo'`
    (adivinado) y dio 0 lotes donde hay 5197. Los valores reales van al contexto."""
    from geo_copilot.orchestrator.nodes.gis_agent import _valores_de_datasets_pequenos
    from geo_copilot.platform.workspace.context import bloque_para_llm

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    ref = await store.ingest_features(ws, "Límite", _fc([{"nombre": "UPZ Teusaquillo", "nivel": "quarter"}]),
                                      crs="EPSG:4326", provenance=PROC)
    valores = await _valores_de_datasets_pequenos(store, ws, [ref])
    assert "'UPZ Teusaquillo'" in valores[ref.id] and "'quarter'" in valores[ref.id]
    bloque = bloque_para_llm([ref], None, valores)
    assert "valores: " in bloque and "UN solo elemento: crúzalo entero" in bloque
