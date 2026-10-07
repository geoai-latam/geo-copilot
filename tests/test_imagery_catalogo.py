"""Catálogo GeoParquet de escenas Sentinel-2 (services/imagery_mcp/imagery_mcp/catalogo.py).

Sin red: se arma un GeoParquet de juguete con el mismo esquema que el de Source
Cooperative (las columnas que el catálogo lee) y el lector apunta a ese directorio.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "imagery_mcp"))

duckdb = pytest.importorskip("duckdb")

from imagery_mcp.catalogo import ID_C1, Catalogo, CatalogoError, ConCatalogo

_AWS = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/sentinel-2-c1-l2a"


def _assets(sid: str) -> str:
    raster = {"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
    claves = ("blue", "green", "red", "nir", "swir16", "swir22", "visual", "scl")
    return json.dumps({k: {"href": f"{_AWS}/{sid}/{k}.tif", **raster} for k in claves})


# (id, tesela, fecha, nubes, nodata %, generación, caja [w, s, e, n])
_FILAS = [
    ("S2B_T18NWL_20260810T152745_L2A", "18NWL", "2026-08-10 15:31:43", 1.3, 0.0, "2026-08-10T20:00:00Z", (-74.5, 4.5, -73.5, 5.4)),
    ("S2C_T18NWL_20260904T152650_L2A", "18NWL", "2026-09-04 15:31:34", 35.7, 96.3, "2026-09-04T20:00:00Z", (-74.5, 4.5, -73.5, 5.4)),
    ("S2C_T18NWL_20260914T152651_L2A", "18NWL", "2026-09-14 15:31:43", 42.0, 0.0, "2026-09-14T20:00:00Z", (-74.5, 4.5, -73.5, 5.4)),
    ("S2B_T18NWK_20260810T152745_L2A", "18NWK", "2026-08-10 15:31:50", 5.4, 0.0, "2026-08-10T20:00:00Z", (-74.5, 3.6, -73.5, 4.5)),
    # Lejos de Bogotá (Leticia): no debe salir en un bbox de Bogotá.
    ("S2A_T19MCT_20260812T150000_L2A", "19MCT", "2026-08-12 15:10:00", 3.0, 0.0, "2026-08-12T20:00:00Z", (-70.5, -4.6, -69.5, -3.7)),
]


def _escribir(con, ruta: Path, filas) -> None:
    con.execute("""CREATE OR REPLACE TABLE t (id VARCHAR, _tile VARCHAR, datetime TIMESTAMPTZ,
        "eo:cloud_cover" DOUBLE, "s2:nodata_pixel_percentage" DOUBLE, "s2:generation_time" VARCHAR,
        platform VARCHAR, thumbnail_url VARCHAR, "s2:processing_baseline" VARCHAR, assets VARCHAR,
        bbox DOUBLE[], geometry GEOMETRY)""")
    for sid, tile, fecha, nubes, nodata, gen, (w, s, e, n) in filas:
        con.execute(
            "INSERT INTO t VALUES (?, ?, ?::TIMESTAMPTZ, ?, ?, ?, ?, ?, '05.12', ?, ?, ST_MakeEnvelope(?, ?, ?, ?))",
            [sid, tile, fecha + "+00", nubes, nodata, gen, "sentinel-2b", f"{_AWS}/{sid}/L2A_PVI.jpg",
             _assets(sid), [w, s, e, n], w, s, e, n])
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY t TO '{ruta.as_posix()}' (FORMAT parquet)")


@pytest.fixture(scope="module")
def catalogo(tmp_path_factory) -> Catalogo:
    base = tmp_path_factory.mktemp("s2c1")
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial; SET TimeZone = 'UTC'")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"DuckDB spatial no disponible: {exc}")
    _escribir(con, base / "year=2026" / "items.parquet", _FILAS)
    # La cola del mes trae la escena del 10-ago REPROCESADA (generación más nueva, menos nubes).
    reproc = [("S2B_T18NWL_20260810T152745_L2A", "18NWL", "2026-08-10 15:31:43", 0.9, 0.0,
               "2026-10-01T00:00:00Z", (-74.5, 4.5, -73.5, 5.4))]
    _escribir(con, base / "year=2026" / "live-10.parquet", reproc)
    return Catalogo(base=base.as_posix(), listar=lambda anio: ["items.parquet", "live-10.parquet"] if anio == 2026 else [])


_BOGOTA = (-74.3, 4.4, -73.9, 4.9)


def test_cuadricula_por_tesela_con_la_mas_despejada(catalogo):
    filas = {f["tile"]: f for f in catalogo.cuadricula(_BOGOTA, "2026-07-01", "2026-09-30")}
    assert set(filas) == {"18NWL", "18NWK"}          # Leticia queda fuera
    nwl = filas["18NWL"]
    assert nwl["escenas"] == 3                       # la reprocesada cuenta UNA vez
    assert nwl["nubes_min"] == 0.9                   # gana la generación más nueva
    assert nwl["mejor_escena"] == "S2B_T18NWL_20260810T152745_L2A"
    assert nwl["mejor_fecha"] == "2026-08-10"
    assert nwl["cobertura_max"] == 100.0
    assert nwl["huella"]["type"] == "Polygon"


def test_cuadricula_filtra_nubes_y_cobertura(catalogo):
    filas = {f["tile"]: f for f in catalogo.cuadricula(_BOGOTA, "2026-07-01", "2026-09-30",
                                                       max_nubes=40, min_cobertura=50)}
    assert filas["18NWL"]["escenas"] == 1   # 35,7 % tiene 3,7 % de cobertura; 42 % pasa de nubes


def test_escenas_de_una_tesela_en_orden(catalogo):
    ids = [e["id"] for e in catalogo.escenas("2026-07-01", "2026-09-30", tile="18NWL", orden="reciente")]
    assert ids == ["S2C_T18NWL_20260914T152651_L2A", "S2C_T18NWL_20260904T152650_L2A",
                   "S2B_T18NWL_20260810T152745_L2A"]
    primera = catalogo.escenas("2026-07-01", "2026-09-30", tile="18NWL")[0]
    assert primera["nubes"] == 0.9 and primera["miniatura"].endswith("L2A_PVI.jpg")
    assert primera["fecha"] == "2026-08-10T15:31:43Z"   # UTC, no la hora local


def test_escenas_fechas_inclusivas_y_errores_honestos(catalogo):
    # date_to es inclusivo: la escena de las 15:31 del 14-sep entra con hasta=2026-09-14.
    ids = [e["id"] for e in catalogo.escenas("2026-09-14", "2026-09-14", tile="18NWL")]
    assert ids == ["S2C_T18NWL_20260914T152651_L2A"]
    with pytest.raises(CatalogoError, match="tesela MGRS inválida"):
        catalogo.escenas("2026-07-01", "2026-09-30", tile="18nwl'; DROP")
    with pytest.raises(CatalogoError, match="orden"):
        catalogo.escenas("2026-07-01", "2026-09-30", tile="18NWL", orden="azar")
    with pytest.raises(CatalogoError, match="tesela MGRS o un área"):
        catalogo.escenas("2026-07-01", "2026-09-30")


def test_item_reconstruye_la_escena_y_la_resuelve_el_proveedor(catalogo):
    sid = "S2B_T18NWL_20260810T152745_L2A"
    item = catalogo.item(sid)
    assert item["properties"]["eo:cloud_cover"] == 0.9
    assert item["assets"]["red"]["href"].endswith(f"{sid}/red.tif")
    assert catalogo.item("S2B_T18NWL_20200101T000000_L2A") is None   # año sin archivos
    assert catalogo.item("S2B_T18NWL_20260811T152745_L2A") is None   # id que no está

    class _Inner:
        name = "planetary-computer"

        def __init__(self):
            self.pedidos = []

        def get_scene(self, scene_id, collection=None):
            self.pedidos.append(scene_id)
            return "escena-pc"

        def sign(self, href):
            return href + "?sas"

    inner = _Inner()
    prov = ConCatalogo(inner, catalogo)
    escena = prov.get_scene(sid)
    assert escena.id == sid and escena.collection == "sentinel-2-c1-l2a"
    assert escena.provider == "earth-search" and escena.scl_href.endswith("scl.tif")
    assert escena.scaling_for("red").scale == 0.0001
    assert inner.pedidos == []                                   # no tocó el STAC
    assert prov.get_scene("S2B_MSIL2A_20260810T152649_R025_T18NWL_X") == "escena-pc"
    assert prov.sign(escena.red_href) == escena.red_href         # bucket público: sin firma
    assert prov.sign("https://x.blob.core.windows.net/a/b.tif").endswith("?sas")
    assert prov.name == "planetary-computer"                     # el resto pasa al proveedor


def test_id_c1_reconoce_solo_collection_1():
    assert ID_C1.match("S2B_T18NWL_20260810T152745_L2A")
    assert not ID_C1.match("S2B_MSIL2A_20260810T152649_R025_T18NWL_20260810T204206")
    assert not ID_C1.match("S2B_18NWL_20260810_0_L2A")
