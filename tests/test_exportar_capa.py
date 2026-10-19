"""Exportar una capa del workspace a archivo: cada formato abre de vuelta con GDAL y conserva
geometría, atributos y sistema de referencia."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest

gpd = pytest.importorskip("geopandas")
shapely = pytest.importorskip("shapely")

from geo_copilot.platform.workspace.exportar import (
    ExportacionInvalida,
    escribir,
    nombre_de_archivo,
)

CAMPOS = ["nombre", "area_ha", "codigo_catastral_predial"]


def _filas(geoms=None):
    geoms = geoms or [shapely.box(-74.08, 4.60, -74.07, 4.61), shapely.box(-74.06, 4.62, -74.05, 4.63)]
    return [{"_wkb": shapely.to_wkb(g), "nombre": n, "area_ha": Decimal("12.5") * (i + 1),
             "codigo_catastral_predial": f"2517500000{i}"}
            for i, (g, n) in enumerate(zip(geoms, ["Lote Chía", "Predio Ñuñoa"], strict=False))]


def _leer(contenido: bytes, sufijo: str, tmp_path):
    ruta = tmp_path / f"x{sufijo}"
    ruta.write_bytes(contenido)
    return gpd.read_file(ruta)


def test_nombre_de_archivo_sin_tildes():
    assert nombre_de_archivo("Lotes de Chía (2026)") == "lotes_de_chia_2026"
    assert nombre_de_archivo("") == "capa"


@pytest.mark.parametrize("formato,sufijo", [("gpkg", ".gpkg"), ("geojson", ".geojson"), ("kml", ".kml")])
def test_formatos_vectoriales_abren_de_vuelta(formato, sufijo, tmp_path):
    contenido, archivo, _mime = escribir(_filas(), CAMPOS, formato, "Lotes de Chía")
    assert archivo == "lotes_de_chia" + sufijo
    gdf = _leer(contenido, sufijo, tmp_path)
    assert len(gdf) == 2
    assert set(gdf["nombre"]) == {"Lote Chía", "Predio Ñuñoa"}       # tildes intactas


def test_gpkg_en_magna_sirgas_origen_nacional(tmp_path):
    contenido, _a, _m = escribir(_filas(), CAMPOS, "gpkg", "lotes", crs="EPSG:9377")
    gdf = _leer(contenido, ".gpkg", tmp_path)
    assert gdf.crs.to_epsg() == 9377
    assert gdf.total_bounds[0] > 4_000_000          # metros, no grados
    assert gdf["area_ha"].tolist() == [12.5, 25.0]


def test_shapefile_va_en_zip_con_prj_y_tildes(tmp_path):
    contenido, archivo, mime = escribir(_filas(), CAMPOS, "shp", "lotes")
    assert archivo == "lotes.zip" and mime == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(contenido))
    nombres = set(z.namelist())
    assert {"lotes.shp", "lotes.shx", "lotes.dbf", "lotes.prj"} <= nombres
    z.extractall(tmp_path)
    gdf = gpd.read_file(tmp_path / "lotes.shp")
    assert "Predio Ñuñoa" in set(gdf["nombre"])
    assert len(gdf.columns) == 4                     # nombres largos truncados a 10, sin perder columnas


def test_shapefile_de_una_capa_mixta_va_por_tipo(tmp_path):
    """Una cuenca con sus cauces y su salida: un Shapefile por tipo de geometría en el zip."""
    filas = _filas([shapely.box(-74.1, 4.6, -74, 4.7), shapely.LineString([(-74, 4.6), (-74.1, 4.7)])])
    filas.append({**filas[0], "_wkb": shapely.to_wkb(shapely.Point(-74, 4.6))})
    contenido, _a, _m = escribir(filas, CAMPOS, "shp", "cuenca")
    nombres = set(zipfile.ZipFile(io.BytesIO(contenido)).namelist())
    assert {"cuenca_poligonos.shp", "cuenca_lineas.shp", "cuenca_puntos.shp", "cuenca_puntos.prj"} <= nombres


def test_csv_con_wkt_y_bom_para_excel():
    contenido, archivo, _m = escribir(_filas(), CAMPOS, "csv", "lotes")
    assert contenido.startswith(b"\xef\xbb\xbf")
    texto = contenido.decode("utf-8-sig")
    assert texto.splitlines()[0] == "nombre,area_ha,codigo_catastral_predial,wkt"
    assert "POLYGON" in texto and "Lote Chía" in texto


def test_kml_solo_en_wgs84_y_crs_desconocido():
    with pytest.raises(ExportacionInvalida, match="WGS84"):
        escribir(_filas(), CAMPOS, "kml", "x", crs="EPSG:9377")
    with pytest.raises(ExportacionInvalida, match="desconocido"):
        escribir(_filas(), CAMPOS, "gpkg", "x", crs="EPSG:999999")
    with pytest.raises(ExportacionInvalida, match="formato"):
        escribir(_filas(), CAMPOS, "xlsx", "x")


def test_dxf_solo_dibujo(tmp_path):
    contenido, archivo, _m = escribir(_filas(), CAMPOS, "dxf", "lotes")
    assert archivo == "lotes.dxf" and b"SECTION" in contenido
