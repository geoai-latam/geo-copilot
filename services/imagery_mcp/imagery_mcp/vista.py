"""VER una escena entera y mirarla de cerca: el producto que se pinta (color, índice, banda suelta,
clasificación SCL, probabilidad de nubes), el histograma de una banda para ajustar el contraste,
el valor de cada banda en un píxel y los enlaces para descargar sus COG.

Ver no es medir: estas operaciones no tienen el tope de área de las de análisis (no leen una
ventana para sacar estadísticas; las teselas se componen al vuelo). La escena se pide por su id
(el del catálogo o el de una búsqueda): nada aquí elige ni interpreta.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from imagery_mcp.catalogo import ID_C1
from imagery_mcp.engine import _GDAL_ENV, ImageryError, apply_scaling, scene_scaling
from imagery_mcp.radiometria import INDICES
from imagery_mcp.tiles_bandas import CLASES_SCL, PROBABILIDADES, rango_de

#: Bandas de Sentinel-2 L2A: nombre canónico → (código ESA, qué es, resolución en m).
BANDAS: dict[str, tuple[str, str, int]] = {
    "coastal": ("B01", "Aerosol costero 443 nm", 60),
    "blue": ("B02", "Azul 490 nm", 10),
    "green": ("B03", "Verde 560 nm", 10),
    "red": ("B04", "Rojo 665 nm", 10),
    "rededge1": ("B05", "Borde rojo 1, 705 nm", 20),
    "rededge2": ("B06", "Borde rojo 2, 740 nm", 20),
    "rededge3": ("B07", "Borde rojo 3, 783 nm", 20),
    "nir": ("B08", "Infrarrojo cercano 842 nm", 10),
    "nir08": ("B8A", "Infrarrojo cercano estrecho 865 nm", 20),
    "nir09": ("B09", "Vapor de agua 945 nm", 60),
    "swir16": ("B11", "Infrarrojo de onda corta 1610 nm", 20),
    "swir22": ("B12", "Infrarrojo de onda corta 2190 nm", 20),
    "aot": ("AOT", "Espesor óptico de aerosoles", 20),
    "wvp": ("WVP", "Columna de vapor de agua (g/cm²)", 20),
    "scl": ("SCL", "Clasificación de la escena", 20),
    "cloud": ("CLD", "Probabilidad de nubes (%)", 20),
    "snow": ("SNW", "Probabilidad de nieve (%)", 20),
    "visual": ("TCI", "Color real ya renderizado (8 bit)", 10),
}

COMBOS = {
    "true_color": "Color real (B04, B03, B02)",
    "false_color": "Falso color infrarrojo (B08, B04, B03): vegetación en rojo",
    "agriculture": "Agricultura (B11, B08, B02): cultivos y suelo",
    "swir": "Infrarrojo de onda corta (B12, B11, B04): humedad, quemas, urbano",
}

#: Todo lo que se puede ver: combinaciones de color, índices y cada banda suelta.
PRODUCTOS = (*COMBOS, *INDICES, *(b for b in BANDAS if b != "visual"))

_PIXEL_HILOS = 8


def _escena(provider, scene_id: str):
    escena = provider.get_scene(scene_id) if scene_id else None
    if escena is None:
        raise ImageryError(f"escena no encontrada: {scene_id!r} (usa un id de imagery_catalog_scenes "
                           "o de imagery_search_scenes)")
    return escena


def _colores(nombre: str, n: int = 5) -> list[str]:
    """Paradas de una rampa de rio-tiler, para la leyenda."""
    from rio_tiler.colormap import cmap

    c = cmap.get(nombre)
    return ["#{:02x}{:02x}{:02x}".format(*c[int(i * 255 / (n - 1))][:3]) for i in range(n)]


def _fecha(escena) -> str:
    return str(escena.datetime)[:10]


def descargas(provider, escena) -> list[dict[str, Any]]:
    """Cada banda de la escena como COG descargable (firmado si el proveedor lo exige)."""
    out = []
    for canon, href in (getattr(escena, "bands", None) or {}).items():
        codigo, nombre, res = BANDAS.get(canon, (canon, canon, None))
        out.append({"banda": canon, "codigo": codigo, "nombre": nombre, "resolucion_m": res,
                    "formato": "COG (GeoTIFF)", "url": provider.sign(href)})
    return out


def _capa(escena, producto: str, estiramiento: list[list[float]] | None, rescale: list[float] | None) -> dict:
    """Ruta de teselas (de este servicio) y leyenda del producto."""
    if producto in COMBOS:
        url = f"/tiles-rgb/{escena.id}/{producto}/{{z}}/{{x}}/{{y}}.png"
        if estiramiento:
            if len(estiramiento) != 3 or any(len(t) != 2 or t[0] >= t[1] for t in estiramiento):
                raise ImageryError("estiramiento: tres pares [mín, máx] de reflectancia (R, G, B)")
            url += "?" + "&".join(f"{c}={t[0]:g},{t[1]:g}" for c, t in zip("rgb", estiramiento, strict=True))
        return {"url": url, "nombre": COMBOS[producto], "leyenda": None}
    if producto in INDICES:
        ind = INDICES[producto]
        lo, hi = rescale or (-1.0, 1.0)
        url = f"/tiles/{escena.id}/{{z}}/{{x}}/{{y}}.png?rescale={lo:g},{hi:g}&index={producto}"
        if ID_C1.match(escena.id):   # escena del catálogo: sus bandas son las de Collection 1
            url += "&collection=sentinel-2-c1-l2a"
        return {"url": url, "nombre": f"{ind['nombre']}: {ind['lectura']}",
                "leyenda": {"type": "ramp", "field": ind["nombre"], "min": lo, "max": hi,
                            "colores": _colores(ind["colormap"])}}
    codigo, nombre, _res = BANDAS[producto]
    if producto == "scl":
        return {"url": f"/tiles-band/{escena.id}/scl/{{z}}/{{x}}/{{y}}.png", "nombre": nombre,
                "leyenda": {"type": "clases", "field": "SCL",
                            "clases": [{"valor": v, "etiqueta": e, "color": c} for v, (e, c) in CLASES_SCL.items()]}}
    lo, hi = rescale or rango_de(producto)
    unidad = "%" if producto in PROBABILIDADES else ("" if producto in ("aot", "wvp") else "reflectancia")
    return {"url": f"/tiles-band/{escena.id}/{producto}/{{z}}/{{x}}/{{y}}.png?rescale={lo:g},{hi:g}",
            "nombre": f"{codigo} · {nombre}",
            "leyenda": {"type": "ramp", "field": f"{codigo} {unidad}".strip(), "min": lo, "max": hi,
                        "colores": ["#000000", "#ffffff"]}}


def ver_escena(provider, scene_id: str, producto: str, *, estiramiento=None, rescale=None,
               on_scene=None) -> dict:
    """La escena ENTERA en el producto pedido, con sus descargas."""
    if producto not in PRODUCTOS:
        raise ImageryError(f"producto desconocido: {producto!r}; válidos: {', '.join(PRODUCTOS)}")
    escena = _escena(provider, scene_id)
    if producto not in COMBOS and producto not in INDICES and producto not in (escena.bands or {}) \
            and not (producto == "scl" and getattr(escena, "scl_href", None)):
        raise ImageryError(f"la escena {escena.id} no trae la banda {producto!r}")
    if on_scene is not None:
        on_scene(escena)
    capa = _capa(escena, producto, estiramiento, rescale)
    return {
        "scene": {"id": escena.id, "datetime": escena.datetime, "cloud_pct": escena.cloud_pct,
                  "provider": escena.provider, "bbox": list(escena.bbox)},
        "producto": {"id": producto, "nombre": capa["nombre"]},
        "tiles": {"url_template": capa["url"], "bounds": list(escena.bbox), "legend": capa["leyenda"]},
        "descargas": descargas(provider, escena),
        "nota": "la escena completa (~110 km); las teselas se componen al vuelo",
    }


def _leer_resumen(provider, href: str, lado: int):
    """La banda entera a `lado`×`lado` píxeles (de las vistas reducidas del COG: lee poco)."""
    import rasterio

    with rasterio.Env(**_GDAL_ENV), rasterio.open(provider.sign(href)) as src:
        arr = src.read(1, out_shape=(lado, lado)).astype("float32")
        nodata = src.nodata if src.nodata is not None else 0
    return arr, arr != nodata


def histograma(provider, scene_id: str, bandas: list[str], *, cubos: int = 64, lado: int = 512) -> dict:
    """Histograma de cada banda sobre la escena entera (de una vista reducida del COG) y sus
    percentiles 2 y 98: con eso se elige el rango de contraste de cada canal."""
    escena = _escena(provider, scene_id)
    if not bandas:
        raise ImageryError("pide al menos una banda")
    out = []
    for b in bandas[:6]:
        href = (escena.bands or {}).get(b) or (getattr(escena, "scl_href", None) if b == "scl" else None)
        if not href:
            raise ImageryError(f"la escena {escena.id} no trae la banda {b!r}")
        arr, valid = _leer_resumen(provider, href, lado)
        if b == "scl":
            v = arr[valid].astype(int)
            total = max(1, v.size)
            out.append({"banda": b, "clases": [
                {"valor": k, "etiqueta": e, "pct": round(100 * float((v == k).sum()) / total, 2)}
                for k, (e, _c) in CLASES_SCL.items()]})
            continue
        vals = arr[valid] if b in PROBABILIDADES else apply_scaling(arr[valid], scene_scaling(escena, b))
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            out.append({"banda": b, "vacia": True})
            continue
        lo, hi = rango_de(b)
        tope = max(hi, float(np.percentile(vals, 99.5)))
        conteos, bordes = np.histogram(vals, bins=cubos, range=(min(lo, float(vals.min())), tope))
        out.append({"banda": b, "p2": round(float(np.percentile(vals, 2)), 4),
                    "p98": round(float(np.percentile(vals, 98)), 4),
                    "min": round(float(vals.min()), 4), "max": round(float(vals.max()), 4),
                    "bordes": [round(float(x), 4) for x in bordes], "conteos": [int(c) for c in conteos],
                    "unidad": "%" if b in PROBABILIDADES else "reflectancia"})
    return {"scene": {"id": escena.id, "datetime": escena.datetime}, "bandas": out,
            "muestra": f"vista reducida de la escena entera a {lado}×{lado} píxeles"}


def _valor_en(provider, href: str, lon: float, lat: float) -> float | None:
    import rasterio
    from rasterio.warp import transform

    with rasterio.Env(**_GDAL_ENV), rasterio.open(provider.sign(href)) as src:
        xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
        fila, col = src.index(xs[0], ys[0])
        if not (0 <= fila < src.height and 0 <= col < src.width):
            return None
        v = src.read(1, window=((fila, fila + 1), (col, col + 1)))[0, 0]
        return None if src.nodata is not None and v == src.nodata else float(v)


def punto_de(geojson: Any) -> tuple[float, float] | None:
    """(lon, lat) de un Point, un Feature o un FeatureCollection con un punto (las referencias del
    núcleo, como `punto`, llegan como FeatureCollection)."""
    g = geojson or {}
    if g.get("type") == "FeatureCollection":
        g = next(iter(g.get("features") or []), None) or {}
    if g.get("type") == "Feature":
        g = g.get("geometry") or {}
    if g.get("type") != "Point" or len(g.get("coordinates") or []) < 2:
        return None
    return float(g["coordinates"][0]), float(g["coordinates"][1])


def pixel(provider, scene_id: str, lon: float, lat: float) -> dict:
    """Lo que vale cada banda de la escena en un punto, en reflectancia (o su clase), y los
    índices que salen de ellas."""
    escena = _escena(provider, scene_id)
    if not (escena.bbox[0] <= lon <= escena.bbox[2] and escena.bbox[1] <= lat <= escena.bbox[3]):
        raise ImageryError(f"el punto ({lon}, {lat}) cae fuera de la escena {escena.id}")
    bandas = {b: h for b, h in (escena.bands or {}).items() if b != "visual"}
    with ThreadPoolExecutor(_PIXEL_HILOS) as ex:
        crudos = dict(zip(bandas, ex.map(lambda h: _valor_en(provider, h, lon, lat), bandas.values()),
                          strict=True))
    # En CLD/SNW el 0 es a la vez el nodata del COG y «0 %»: dentro de la escena (el SCL tiene
    # clase) es 0 %, no un hueco.
    con_dato = bool(crudos.get("scl"))
    crudos = {b: (0.0 if v is None and b in PROBABILIDADES and con_dato else v) for b, v in crudos.items()}
    valores: dict[str, Any] = {}
    for b, v in crudos.items():
        codigo, nombre, res = BANDAS.get(b, (b, b, None))
        if v is None:
            valores[b] = {"codigo": codigo, "nombre": nombre, "valor": None}
        elif b == "scl":
            valores[b] = {"codigo": codigo, "nombre": nombre, "valor": int(v),
                          "clase": CLASES_SCL.get(int(v), ("desconocida", ""))[0]}
        elif b in PROBABILIDADES:
            valores[b] = {"codigo": codigo, "nombre": nombre, "valor": v, "unidad": "%"}
        else:
            r = float(apply_scaling(np.array([v], "float32"), scene_scaling(escena, b))[0])
            valores[b] = {"codigo": codigo, "nombre": nombre, "valor": round(r, 4),
                          "unidad": "" if b in ("aot", "wvp") else "reflectancia", "resolucion_m": res}
    indices = {}
    for nombre_i, ind in INDICES.items():
        a, b = (valores.get(x, {}).get("valor") for x in ind["bandas"])
        if isinstance(a, float) and isinstance(b, float) and (a + b) != 0:
            indices[nombre_i] = {"nombre": ind["nombre"], "valor": round((a - b) / (a + b), 4),
                                 "lectura": ind["lectura"]}
    return {"scene": {"id": escena.id, "datetime": escena.datetime}, "punto": {"lon": lon, "lat": lat},
            "bandas": valores, "indices": indices}
