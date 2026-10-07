"""Exportar una capa del workspace a un archivo: GeoPackage, Shapefile (zip), KML, GeoJSON, CSV
(geometría en WKT) o DXF, en el sistema de referencia que se pida (p. ej. MAGNA-SIRGAS Origen
Nacional, EPSG:9377, el de las entregas oficiales en Colombia).

Lo escribe GDAL por pyogrio (viene con geopandas): los mismos drivers que usa cualquier SIG, así
el archivo abre igual en QGIS, ArcGIS o Google Earth.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Formato:
    driver: str | None
    extension: str
    mime: str
    nombre: str


FORMATOS: dict[str, Formato] = {
    "gpkg": Formato("GPKG", ".gpkg", "application/geopackage+sqlite3", "GeoPackage"),
    "shp": Formato("ESRI Shapefile", ".zip", "application/zip", "Shapefile (zip)"),
    "kml": Formato("LIBKML", ".kml", "application/vnd.google-earth.kml+xml", "KML (Google Earth)"),
    "geojson": Formato("GeoJSON", ".geojson", "application/geo+json", "GeoJSON"),
    "csv": Formato(None, ".csv", "text/csv; charset=utf-8", "CSV (geometría en WKT)"),
    "dxf": Formato("DXF", ".dxf", "image/vnd.dxf", "DXF (CAD, sin atributos)"),
}


class ExportacionInvalida(ValueError):
    """Lo pedido no se puede escribir así (formato, CRS o geometrías): el mensaje dice por qué."""


def nombre_de_archivo(nombre: str) -> str:
    """Un nombre de archivo sin tildes ni símbolos: «Lotes de Chía» → «lotes_de_chia»."""
    plano = unicodedata.normalize("NFKD", nombre or "capa").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plano.lower()).strip("_")[:60] or "capa"


def crs_de_salida(crs: str | None, formato: str) -> str:
    if formato == "kml":
        if crs not in (None, "", "EPSG:4326"):
            raise ExportacionInvalida("KML siempre va en WGS84 (EPSG:4326)")
        return "EPSG:4326"
    if not crs:
        return "EPSG:4326"
    from pyproj import CRS
    from pyproj.exceptions import CRSError

    try:
        return CRS.from_user_input(crs).to_string()
    except CRSError as exc:
        raise ExportacionInvalida(f"sistema de referencia desconocido: {crs!r}") from exc


def _valor(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


#: Un Shapefile guarda un solo tipo de geometría: una capa mixta (p. ej. una cuenca con sus
#: cauces y su salida) va en un Shapefile por tipo dentro del zip.
_SUFIJO_TIPO = {"Polygon": "poligonos", "LineString": "lineas", "Point": "puntos"}


def _shapefiles(gdf, carpeta: str, base: str) -> None:
    tipos = gdf.geom_type.fillna("").str.replace("Multi", "")
    presentes = [t for t in _SUFIJO_TIPO if (tipos == t).any()]
    for t in presentes or ["Polygon"]:
        parte = gdf[tipos == t] if presentes else gdf
        nombre = base if len(presentes) <= 1 else f"{base}_{_SUFIJO_TIPO[t]}"
        parte.to_file(os.path.join(carpeta, nombre + ".shp"), engine="pyogrio", driver="ESRI Shapefile",
                      promote_to_multi=True, layer_options={"ENCODING": "UTF-8"})


def escribir(filas: list[dict[str, Any]], campos: list[str], formato: str, nombre: str,
             crs: str | None = None) -> tuple[bytes, str, str]:
    """(contenido, nombre de archivo, mime). `filas` traen `_wkb` (geometría en 4326) y los campos."""
    import geopandas as gpd
    import pandas as pd
    import shapely

    if formato not in FORMATOS:
        raise ExportacionInvalida(f"formato desconocido: {formato!r}; válidos: {', '.join(FORMATOS)}")
    f = FORMATOS[formato]
    destino_crs = crs_de_salida(crs, formato)
    base = nombre_de_archivo(nombre)
    df = pd.DataFrame([{c: _valor(r.get(c)) for c in campos} for r in filas], columns=campos)
    wkb = [bytes(r["_wkb"]) if r.get("_wkb") is not None else None for r in filas]
    gdf = gpd.GeoDataFrame(df, geometry=shapely.from_wkb(wkb), crs="EPSG:4326")
    if destino_crs != "EPSG:4326":
        gdf = gdf.to_crs(destino_crs)

    if formato == "csv":
        salida = pd.DataFrame(gdf.drop(columns="geometry"))
        salida["wkt"] = gdf.geometry.to_wkt()
        # utf-8 con BOM: Excel abre las tildes bien
        return salida.to_csv(index=False).encode("utf-8-sig"), base + f.extension, f.mime

    if formato == "dxf":
        gdf = gdf[["geometry"]]   # el DXF no guarda atributos: solo dibujo

    with tempfile.TemporaryDirectory() as tmp:
        if formato == "shp":
            _shapefiles(gdf, tmp, base)
        else:
            ruta = os.path.join(tmp, base + f.extension)
            opciones: dict[str, Any] = {"engine": "pyogrio", "driver": f.driver}
            if formato == "gpkg":
                opciones.update(promote_to_multi=True, layer=base)
            gdf.to_file(ruta, **opciones)
            with open(ruta, "rb") as fh:
                return fh.read(), base + f.extension, f.mime
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for archivo in sorted(os.listdir(tmp)):
                z.write(os.path.join(tmp, archivo), archivo)
        return buf.getvalue(), base + f.extension, f.mime
