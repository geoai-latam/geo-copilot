"""F5 (T5.6) — archivos-mcp: GeoParquet/CSV leídos donde están con DuckDB-spatial, filtrando en
el ORIGEN (where / aoi / columnas); resultados grandes como GeoParquet por referencia que el hub
descarga del propio servidor con su credencial. Sin red: los archivos se crean aquí.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

duckdb = pytest.importorskip("duckdb")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "archivos_mcp"))
from archivos_mcp import server as srv

# grilla de 100 x 100 puntos cada 0,001° desde (-74.10, 4.60): 10 000 «predios»
AOI = {"type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.09, 4.6], [-74.09, 4.61], [-74.1, 4.61], [-74.1, 4.6]]]}


@pytest.fixture(scope="module")
def datos(tmp_path_factory):
    d = tmp_path_factory.mktemp("archivos")
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(f"""COPY (SELECT i AS id, CASE WHEN i % 2 = 0 THEN 'residencial' ELSE 'comercial' END AS uso,
                            (i % 50) * 10.0 AS area, ST_Point(-74.1 + (i % 100) * 0.001, 4.6 + (i // 100) * 0.001) AS geom
                     FROM range(10000) t(i)) TO '{(d / "predios.parquet").as_posix()}' (FORMAT PARQUET)""")
    (d / "sedes.csv").write_text("nombre,lon,lat\nA,-74.095,4.605\nB,-73.5,5.0\n", encoding="utf-8")
    return d


@pytest.fixture
def fuentes(datos, monkeypatch, tmp_path):
    f = srv.cargar_fuentes(json.dumps([
        {"id": "predios", "descripcion": "Predios de prueba", "uri": (datos / "predios.parquet").as_posix(),
         "formato": "geoparquet"},
        {"id": "sedes", "descripcion": "Sedes (CSV)", "uri": (datos / "sedes.csv").as_posix(), "formato": "csv",
         "lon": "lon", "lat": "lat"},
    ]))
    monkeypatch.setattr(srv, "FUENTES", f)
    monkeypatch.setattr(srv, "RESULTADOS", str(tmp_path / "resultados"))
    return f


def test_la_configuracion_se_valida():
    with pytest.raises(ValueError, match="no soportado"):
        srv.cargar_fuentes('[{"id":"x","uri":"a.shp","formato":"shapefile"}]')
    with pytest.raises(ValueError, match="lon.*lat"):
        srv.cargar_fuentes('[{"id":"x","uri":"a.csv","formato":"csv"}]')


def test_listar_dice_que_hay_en_cada_fuente(fuentes):
    hechos = srv._listar()["facts"]["fuentes"]
    p = next(x for x in hechos if x["id"] == "predios")
    assert p["elementos"] == 10000 and p["geometria"] == "geom"
    assert p["extent_4326"] == [-74.1, 4.6, -74.001, 4.699]
    assert {c["columna"] for c in p["columnas"]} == {"id", "uso", "area"}
    s = next(x for x in hechos if x["id"] == "sedes")
    assert s["elementos"] == 2 and s["geometria"] == "geom"


def test_el_filtro_y_el_recorte_se_hacen_en_el_origen(fuentes):
    out = srv._consultar("predios", "uso = 'residencial' AND area < 60", AOI, ["id", "uso"], 50000)
    h = out["facts"]
    # dentro del AOI hay 11 x 11 = 121 puntos; de ellos, residenciales (id par) con area < 60
    esperado = sum(1 for i in range(10000) if (i % 100) <= 10 and (i // 100) <= 10 and i % 2 == 0 and (i % 50) * 10 < 60)
    assert esperado == 33
    assert h["total_que_cumplen"] == esperado and h["completo"] is True and "DuckDB" in h["filtrado_en_origen"]
    (art,) = out["artifacts"]
    assert art["kind"] == "feature_collection" and art["crs"] == "EPSG:4326"
    feats = art["data"]["features"]
    assert len(feats) == esperado and set(feats[0]["properties"]) == {"id", "uso"}
    assert feats[0]["geometry"]["type"] == "Point"


def test_un_csv_con_coordenadas_es_una_capa(fuentes):
    out = srv._consultar("sedes", None, AOI, None, 100)
    assert out["facts"]["total_que_cumplen"] == 1
    assert out["artifacts"][0]["data"]["features"][0]["properties"]["nombre"] == "A"


@pytest.mark.parametrize("where, motivo", [
    ("1=1; DROP TABLE t", "statement"),
    ("id IN (SELECT 1 FROM pg_authid)", "catálogo del sistema"),
    ("read_csv('/etc/passwd') IS NOT NULL", "allowlist"),
])
def test_un_where_peligroso_se_rechaza_antes_de_leer(fuentes, where, motivo):
    with pytest.raises(srv.ErrorArchivos, match=motivo):
        srv._consultar("predios", where, None, None, 10)


def test_errores_que_se_pueden_corregir(fuentes):
    with pytest.raises(srv.ErrorArchivos, match="no hay una fuente «otra»; las que hay: predios, sedes"):
        srv._consultar("otra", None, None, None, 10)
    with pytest.raises(srv.ErrorArchivos, match=r"columnas que no existen: \['barrio'\]"):
        srv._consultar("predios", None, None, ["barrio"], 10)


def test_resultados_grandes_van_como_geoparquet_por_referencia(fuentes):
    out = srv._consultar("predios", None, None, ["id"], 8000)
    (art,) = out["artifacts"]
    assert art["kind"] == "feature_ref" and art["format"] == "geoparquet" and art["feature_count"] == 8000
    assert art["uri"].startswith("/resultados/") and out["facts"]["completo"] is False
    assert "MUESTRA: 8000 de 10000" in out["facts"]["aviso"]
    archivo = Path(srv.RESULTADOS) / art["uri"].rsplit("/", 1)[1]
    con = duckdb.connect()
    con.execute("LOAD spatial")
    assert con.execute(f"SELECT count(*) FROM '{archivo.as_posix()}'").fetchone()[0] == 8000


# ---------------------------------------------------------------------------
# Servidor real en proceso + hub
# ---------------------------------------------------------------------------

CLAVE = "clave-de-test-archivos"


@pytest.fixture(scope="module")
def servidor(datos, tmp_path_factory):
    """UN servidor por módulo: FastMCP solo arranca su gestor de sesiones una vez por instancia."""
    import os

    import uvicorn

    from tests.mcp_helpers import puerto_libre

    os.environ["ARCHIVOS_SOURCES"] = json.dumps([
        {"id": "predios", "descripcion": "Predios de prueba", "uri": (datos / "predios.parquet").as_posix(),
         "formato": "geoparquet"}])
    os.environ["ARCHIVOS_MCP_KEYS"] = json.dumps([{"name": "t", "key": CLAVE, "scopes": ["archivos:read"],
                                                   "rate_limit_per_min": 10_000}])
    srv.RESULTADOS = str(tmp_path_factory.mktemp("resultados_srv"))
    puerto = puerto_libre()
    s = uvicorn.Server(uvicorn.Config(srv.build_app(), host="127.0.0.1", port=puerto, log_level="warning"))
    hilo = threading.Thread(target=s.run, daemon=True)
    hilo.start()
    for _ in range(100):
        if s.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{puerto}"
    s.should_exit = True
    hilo.join(timeout=5)


def test_el_resultado_se_sirve_solo_con_clave(servidor):
    import httpx

    art = srv._consultar("predios", None, None, ["id"], 6000)["artifacts"][0]
    assert art["kind"] == "feature_ref"
    assert httpx.get(servidor + art["uri"]).status_code == 401
    r = httpx.get(servidor + art["uri"], headers={"Authorization": f"Bearer {CLAVE}"})
    assert r.status_code == 200 and r.content[:4] == b"PAR1"
    assert httpx.get(servidor + "/resultados/" + "0" * 32 + ".parquet",
                     headers={"Authorization": f"Bearer {CLAVE}"}).status_code == 404


@pytest.mark.asyncio
async def test_el_hub_descarga_el_geoparquet_del_servidor_y_lo_materializa(servidor, monkeypatch):
    from geo_copilot.platform.capabilities import registry
    from geo_copilot.platform.mcp.config import McpConfig
    from geo_copilot.platform.mcp.hub import McpHub
    from geo_copilot.platform.workspace import context as wsctx

    monkeypatch.setenv("ARCHIVOS_TEST_KEY", CLAVE)
    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_cccccccccccccccc", "name": "Predios de prueba", "feature_count": 6000}
    store = SimpleNamespace(ingest_features=AsyncMock(return_value=ref), to_geojson=AsyncMock())
    monkeypatch.setattr(wsctx, "_store", store)
    hub = McpHub(McpConfig.model_validate({"servers": [{
        "id": "archivos", "url": f"{servidor}/mcp", "conformance": "G1",
        "auth": {"type": "bearer", "secret_ref": "env:ARCHIVOS_TEST_KEY"},
        "tools": {"allow": ["archivos_*"]}, "recursos": {"prefixes": ["/resultados/"]},
    }]}))
    await hub.refrescar()
    try:
        out = await registry().get("archivos__archivos_query").executor(
            None, {"session_id": "s1"}, {"source": "predios", "columns": ["id"], "max_features": 6000})
        assert out.success, out.observation
        args, kw = store.ingest_features.call_args
        assert len(args[2]["features"]) == 6000 and kw["crs"] == "EPSG:4326"
        assert out.delta["result_layer_ref"]["id"] == "ds_cccccccccccccccc"
    finally:
        registry().unregister("archivos__archivos_query")
        registry().unregister("archivos__archivos_list")


def test_columnas_asterisco_son_todas_y_una_geometria_escrita_a_mano_se_dice(fuentes):
    """V5 (archivos): `columns=['*']` se rechazaba y, al reintentar, el LLM soltaba el área; y con
    `where=geom && ST_MakeEnvelope(...)` 3530 lotes de un RECTÁNGULO se narraron como «los del barrio»."""
    todas = srv._consultar("predios", "area > 400", None, ["*"], 50_000)
    capa = todas["artifacts"][0]["data"]["features"][0]["properties"]
    assert {"id", "uso", "area"} <= set(capa)
    rect = srv._consultar("predios", "ST_Intersects(geom, ST_MakeEnvelope(-74.1, 4.6, -74.095, 4.605))",
                          None, None, 50_000)
    assert "NO es su límite" in rect["facts"]["aviso_area"]
    assert "aviso_area" not in todas["facts"]


def test_las_estadisticas_se_calculan_en_el_origen_sobre_todo_lo_que_cumple(fuentes):
    """V5 (archivos): el agente trajo 10 lotes y dio su promedio como el de los 22.387 (212 m² vs 882)."""
    gr = srv._estadisticas("predios", "area > 400", None, ["area", "uso"])
    h = gr["facts"]
    # la grilla: area = (i % 50) * 10 → > 400 son los 9 valores 410..490 en cada ciclo de 50
    assert h["elementos"] == 10_000 * 9 // 50 and "TODAS" in h["calculado_sobre"]
    assert h["campos"]["area"]["media"] == 450.0 and h["campos"]["area"]["min"] == 410.0
    assert sorted(v for v, _ in h["campos"]["uso"]["mas_frecuentes"]) == ["comercial", "residencial"]
    assert gr["artifacts"][0]["kind"] == "stats"
    con_area = srv._estadisticas("predios", None, AOI, None)["facts"]["elementos"]
    assert 0 < con_area < 10_000  # el área filtra en el origen
    with pytest.raises(srv.ErrorArchivos, match="campos que no existen"):
        srv._estadisticas("predios", None, None, ["altura"])


def test_una_columna_inexistente_en_el_where_dice_cuales_hay(fuentes):
    """V5 (archivos): `where=area_m2…` se escribió `area > 1000`; el error de DuckDB no decía las columnas."""
    with pytest.raises(srv.ErrorArchivos, match=r"Columnas de la fuente: area, id, uso"):
        srv._consultar("predios", "superficie > 10", None, None, 10)


def test_el_resultado_dice_que_cubre_la_fuente_y_de_quien_es_el_tope(fuentes):
    """V5: «1217 lotes en Chapinero» de un archivo con Chapinero Y Teusaquillo (sin aoi); y un
    max_features=1000 del propio agente narrado como «límite técnico»."""
    h = srv._consultar("predios", "area > 400", None, None, 5)["facts"]
    assert h["que_cubre_la_fuente"] == "Predios de prueba" and "no se filtró por un área" in h["sin_filtro_de_area"]
    assert "pidió max_features=5" in h["aviso"] and "no es un límite del servidor" in h["aviso"]
    assert "sin_filtro_de_area" not in srv._consultar("predios", None, AOI, None, 5)["facts"]


def test_una_capa_del_workspace_en_el_where_dice_que_va_en_aoi(fuentes):
    """F3 (verdades ×5): el agente metía el límite de Chapinero en el `where` como si fuera una tabla;
    rechazado sin pista, daba los 1.217 lotes del archivo entero como respuesta."""
    with pytest.raises(srv.ErrorArchivos, match="pásala en `aoi`"):
        srv._consultar("predios", "ST_Intersects(geom, (SELECT geom FROM ds_a17eb7a8c4e24b04 LIMIT 1))",
                       None, None, 10)
