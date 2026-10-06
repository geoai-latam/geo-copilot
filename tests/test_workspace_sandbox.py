"""S2.4 — el sandbox trabaja con N datasets del workspace (`datasets["nombre"]`).

El runner (código de confianza) carga los GeoParquet que app exportó; el código
del LLM recibe solo el dict ya cargado, sin rutas ni un cargador.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import geopandas as gpd
import pytest
from shapely.geometry import Point

from geo_copilot.agents.gis_agent import sandbox_runner as runner
from geo_copilot.agents.python_agent import agent as pa
from geo_copilot.platform.contracts import FieldInfo, LayerRef, Provenance, WorkspaceTable
from geo_copilot.platform.workspace import context as wsctx

WS = "ws_0123456789abcdef"
DS_A, DS_B = "ds_aaaaaaaaaaaaaaaa", "ds_bbbbbbbbbbbbbbbb"


def _ref(ds: str, nombre: str) -> LayerRef:
    return LayerRef(
        id=ds, name=nombre, kind="vector", provider="core", crs="EPSG:4326",
        geometry_type="Point", feature_count=3,
        fields=[FieldInfo(name="valor", type="number")],
        storage=WorkspaceTable(schema_name=WS, table=ds.replace("ds_", "d_"), geometry_column="geom"),
        provenance=Provenance(capability="core.query_database", produced_at=datetime.now(UTC)),
    )


@pytest.fixture
def raiz(tmp_path):
    os.makedirs(tmp_path / WS)
    for ds, base in ((DS_A, 1.0), (DS_B, 10.0)):
        gpd.GeoDataFrame(
            {"valor": [base, base * 2, base * 3]},
            geometry=[Point(-74, 4.6), Point(-74.1, 4.6), Point(-74.2, 4.6)], crs="EPSG:4326",
        ).to_parquet(tmp_path / WS / f"{ds}.parquet")
    return str(tmp_path)


def _correr(payload: dict) -> dict:
    """El runner EN PROCESO, sin sus rlimits.

    `_apply_limits` pone RLIMIT_AS/CPU/NPROC al proceso actual: en Linux (CI)
    eso es pytest mismo, y lo mataba (exit 137). Aquí se prueba la carga de
    datasets; los límites tienen sus propios tests en subproceso.
    """
    cwd = os.getcwd()
    original = runner._apply_limits
    runner._apply_limits = lambda *a, **k: None
    try:
        return runner._run(payload)
    finally:
        runner._apply_limits = original
        os.chdir(cwd)  # el runner hace chdir a un tempdir


# --- runner ---------------------------------------------------------------


def test_el_runner_entrega_los_datasets_cargados(raiz):
    out = _correr({
        "code": "total = float(datasets['A'].valor.sum() + datasets['B'].valor.sum())",
        "datasets": {"A": f"{WS}/{DS_A}.parquet", "B": f"{WS}/{DS_B}.parquet"},
        "datasets_root": raiz,
    })
    assert out["success"], out
    assert out["results"]["total"] == 66.0
    assert "datasets" not in out["results"]  # no se devuelve la entrada


@pytest.mark.parametrize("ruta", [
    "../etc/passwd", "/etc/passwd", f"{WS}/../{WS}/{DS_A}.parquet", f"{WS}/{DS_A}.csv", "x.parquet",
])
def test_el_runner_solo_lee_rutas_del_store(raiz, ruta):
    out = _correr({"code": "x = 1", "datasets": {"A": ruta}, "datasets_root": raiz})
    assert not out["success"] and "ruta de dataset no válida" in out["error"]


def test_sin_datasets_el_runner_no_cambia(raiz):
    out = _correr({"code": "x = 2"})
    assert out["success"] and out["results"] == {"x": 2}


# --- python agent -----------------------------------------------------------


@pytest.mark.asyncio
async def test_solo_se_exportan_los_datasets_que_el_codigo_nombra(monkeypatch):
    store = SimpleNamespace(export_geoparquet=AsyncMock(side_effect=lambda ws, ds, r: f"{WS}/{ds}.parquet"))
    monkeypatch.setattr(wsctx, "_store", store)
    workspace = {"Lotes": _ref(DS_A, "Lotes"), "Vías": _ref(DS_B, "Vías")}
    code = 'r = datasets["Lotes"].copy()\nx = datasets["Otra"]  # no existe: se ignora'
    manifiesto = await pa._exportar_usados("sess-a", code, workspace)
    assert manifiesto == {"Lotes": f"{WS}/{DS_A}.parquet"}
    store.export_geoparquet.assert_awaited_once()
    assert store.export_geoparquet.call_args.args[:2] == ("sess-a", DS_A)


@pytest.mark.asyncio
async def test_claves_duplicadas_se_numeran(monkeypatch):
    store = SimpleNamespace(list_datasets=AsyncMock(return_value=[
        _ref(DS_A, "Lotes"), _ref(DS_B, "Lotes"),
    ]))
    monkeypatch.setattr(wsctx, "_store", store)
    ws = await pa._datasets_de_la_sesion("sess-a")
    assert list(ws) == ["Lotes", "Lotes #2"] and ws["Lotes #2"].id == DS_B
    assert await pa._datasets_de_la_sesion(None) == {}


def test_el_bloque_del_prompt_da_claves_literales_y_columnas():
    txt = pa._bloque_datasets({"Lotes": _ref(DS_A, "Lotes")})
    assert "datasets['Lotes']" in txt and "valor" in txt and "gdf` puede ser None" in txt


@pytest.mark.asyncio
async def test_sin_capa_activa_pero_con_datasets_el_agente_trabaja(monkeypatch):
    """Una capa grande que se quedó en el workspace se analiza vía `datasets`."""
    monkeypatch.setattr(pa.settings, "hitl_enabled", False)
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(
        list_datasets=AsyncMock(return_value=[_ref(DS_A, "Lotes")]),
        export_geoparquet=AsyncMock(return_value=f"{WS}/{DS_A}.parquet"),
    ))
    agente = pa.PythonAgent.__new__(pa.PythonAgent)
    agente.hitl_manager = None
    agente._generate_code = AsyncMock(return_value="stats = {'n': len(datasets['Lotes'])}")
    agente._execute_in_sandbox = AsyncMock(return_value={"success": True, "stats": {"n": 3}})
    agente._judge_output_responds = AsyncMock(return_value={"responds": True})

    resp = await agente.process("¿cuántos lotes hay?", {"session_id": "sess-a"})

    kwargs = agente._generate_code.call_args.kwargs
    assert list(kwargs["workspace"]) == ["Lotes"]
    assert agente._execute_in_sandbox.call_args.kwargs["datasets"] == {"Lotes": f"{WS}/{DS_A}.parquet"}
    assert resp.success, resp.message


@pytest.mark.asyncio
async def test_sin_capa_ni_datasets_sigue_fallando_honesto(monkeypatch):
    monkeypatch.setattr(wsctx, "_store", None)
    agente = pa.PythonAgent.__new__(pa.PythonAgent)
    resp = await agente.process("haz un buffer", {"session_id": "sess-a"})
    assert not resp.success and "fuente de datos activa" in resp.message


# --- export real -------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_export_geoparquet_real_y_purga(workspace_pool, tmp_path):
    from geo_copilot.platform.workspace import DatasetStore, WorkspaceError, WorkspaceLimits

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74 - i * 1e-3, 4.6]},
         "properties": {"n": i, "nombre": f"p{i}", "meta": {"k": i}}}
        for i in range(5)
    ]}
    prov = Provenance(capability="core.query_database", produced_at=datetime.now(UTC))
    ref = await store.ingest_features(ws, "Puntos", fc, crs="EPSG:4326", provenance=prov)

    relativa = await store.export_geoparquet(ws, ref.id, str(tmp_path))
    assert runner._DATASET_PATH_RE.match(relativa), relativa  # el runner la aceptará
    gdf = gpd.read_parquet(tmp_path / relativa)
    assert len(gdf) == 5 and gdf.crs.to_epsg() == 4326
    assert list(gdf.n) == [0, 1, 2, 3, 4] and gdf.geometry.iloc[1].x == pytest.approx(-74.001)
    # inmutable: la segunda vez reutiliza el archivo
    mtime = os.path.getmtime(tmp_path / relativa)
    assert await store.export_geoparquet(ws, ref.id, str(tmp_path)) == relativa
    assert os.path.getmtime(tmp_path / relativa) == mtime

    with pytest.raises(WorkspaceError):
        await store.export_geoparquet(f"otra-{uuid.uuid4()}", ref.id, str(tmp_path))

    vencido = await DatasetStore(workspace_pool, WorkspaceLimits(ttl_hours=-1)).ingest_features(
        ws, "Efímero", fc, crs="EPSG:4326", provenance=prov,
    )
    # un vencido ya no se puede exportar, y la purga borra su archivo si existía
    with pytest.raises(WorkspaceError):
        await store.export_geoparquet(ws, vencido.id, str(tmp_path))
    (tmp_path / ref.storage.schema_name / f"{vencido.id}.parquet").write_bytes(b"x")
    await store.purge_expired(str(tmp_path))
    assert not (tmp_path / ref.storage.schema_name / f"{vencido.id}.parquet").exists()
    assert (tmp_path / relativa).exists()  # el vigente se queda
