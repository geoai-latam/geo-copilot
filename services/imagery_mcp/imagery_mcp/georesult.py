"""Resultados del motor de imagery → contrato GeoResult (G2, S3.6).

El motor no cambia; esto solo declara QUÉ es cada cosa para que cualquier
núcleo GeoMCP lo lleve al mapa sin conocer imagery:

- capas visuales → `raster_tiles` (rutas bajo /tiles/, /tiles-diff/, /tiles-rgb/
  de este mismo servicio; el núcleo las proxifica con su credencial),
- estadísticas → `stats`,
- estadística zonal → `feature_collection` con `ndvi_mean`/`ndvi_std` por feature,
- todo lo que un analista necesita para fiarse del número (escena, fecha, nubes,
  máscara, reflectancia, píxeles descartados, escenas alternativas) → `facts`,
  estructurado: el LLM del núcleo lo interpreta y lo narra.
"""

from __future__ import annotations

import copy
from typing import Any

from geo_mcp_kit import feature_collection, geo_result, raster_tiles, stats, table

_HECHOS = ("index", "collection", "scene", "scene_a", "scene_b", "stats", "diff_stats", "facts", "cloud_mask",
           "reflectance", "descartes", "alternatives", "degraded", "skipped", "combo")


def _hechos(r: dict) -> dict[str, Any]:
    return {k: r[k] for k in _HECHOS if r.get(k) is not None}


def _fecha(escena: dict | None) -> str:
    return str((escena or {}).get("datetime", ""))[:10]


def _query_rescale(r: dict) -> str:
    rs = (r.get("tiles") or {}).get("rescale") or []
    return f"?rescale={rs[0]},{rs[1]}" if len(rs) == 2 else ""


def _items(d: dict | None) -> list[dict[str, Any]]:
    return [{"label": k, "value": v} for k, v in (d or {}).items()]


def ndvi(r: dict) -> dict:
    if r.get("error"):
        return r
    escena = (r.get("scene") or {}).get("id")
    t = r.get("tiles") or {}
    nombre = (r.get("index") or {}).get("nombre") or "NDVI"
    col = (r.get("collection") or {}).get("nombre")
    sufijo = f" ({col})" if col and col != "Sentinel-2 L2A" else ""
    arts = [stats(_items(r.get("stats")), name=f"Estadísticas {nombre}")]
    if escena:
        rs = t.get("rescale") or [-1.0, 1.0]
        url = t.get("url_template") or f"/tiles/{escena}/{{z}}/{{x}}/{{y}}.png{_query_rescale(r)}"
        arts.insert(0, raster_tiles(
            f"{nombre} {_fecha(r.get('scene'))}{sufijo}", url,
            bounds=t.get("bounds"), legend={"type": "ramp", "field": nombre, "min": rs[0], "max": rs[1]},
            datetime=_fecha(r.get("scene")) or None,
        ))
    return geo_result(arts, facts=_hechos(r))


def change(r: dict) -> dict:
    if r.get("error"):
        return r
    a, b = (r.get("scene_a") or {}).get("id"), (r.get("scene_b") or {}).get("id")
    t = r.get("tiles") or {}
    arts = [stats(_items(r.get("diff_stats")), name="Cambio de NDVI")]
    if a and b:
        arts.insert(0, raster_tiles(
            f"Cambio NDVI {_fecha(r.get('scene_a'))}→{_fecha(r.get('scene_b'))}",
            f"/tiles-diff/{a}/{b}/{{z}}/{{x}}/{{y}}.png{_query_rescale(r)}", bounds=t.get("bounds"),
            legend={"type": "ramp", "field": "ΔNDVI", "min": -0.5, "max": 0.5,
                    "nota": "rojo = pérdida, verde = ganancia"},
        ))
    return geo_result(arts, facts=_hechos(r))


def composite(r: dict) -> dict:
    if r.get("error"):
        return r
    escena = (r.get("scene") or {}).get("id")
    combo = r.get("combo") or (r.get("tiles") or {}).get("combo") or "true_color"
    arts = []
    if escena:
        arts.append(raster_tiles(
            f"{combo} {_fecha(r.get('scene'))}", f"/tiles-rgb/{escena}/{combo}/{{z}}/{{x}}/{{y}}.png",
            bounds=(r.get("tiles") or {}).get("bounds"), datetime=_fecha(r.get("scene")) or None,
        ))
    return geo_result(arts, facts=_hechos(r))


def zonal(r: dict, features_geojson: dict) -> dict:
    """La capa de entrada enriquecida con el NDVI de cada feature (lista para colorear)."""
    if r.get("error"):
        return r
    enriquecida = copy.deepcopy(features_geojson)
    pre = str((r.get("index") or {}).get("id") or "ndvi")
    nombre = (r.get("index") or {}).get("nombre") or "NDVI"
    res_m = (r.get("collection") or {}).get("resolucion_m") or 10
    por_indice = {row["feature_index"]: row for row in r.get("rows") or []}
    for i, feat in enumerate(enriquecida.get("features", [])):
        row = por_indice.get(i)
        if row:
            props = feat.setdefault("properties", {}) or {}
            feat["properties"] = props
            props.update({f"{pre}_mean": row["mean"], f"{pre}_std": row["std"],
                          f"{pre}_min": row["min"], f"{pre}_max": row["max"]})
            if row.get("px_compartidos"):
                props[f"{pre}_px_compartidos"] = True
    compartidos = sum(1 for row in por_indice.values() if row.get("px_compartidos"))
    hechos = {**_hechos(r), "features_calculadas": len(por_indice)}
    if compartidos:
        hechos["features_menores_que_el_pixel"] = {
            "cuantas": compartidos,
            "nota": f"geometrías más pequeñas que el píxel ({res_m} m): su valor es el de los píxeles "
                    "que comparten con las vecinas, así que lotes contiguos pueden salir iguales",
        }
    return geo_result(
        [feature_collection(f"{nombre} por feature {_fecha(r.get('scene'))}", enriquecida, crs="EPSG:4326")],
        facts=hechos,
        style_hint={"field": f"{pre}_mean", "method": "quantile"},
    )


def search(r: dict) -> dict:
    if r.get("error"):
        return r
    escenas = r.get("scenes") or []
    cols = sorted({k for s in escenas for k in s}) if escenas else []
    return geo_result([table(cols, escenas, name="Escenas")],
                      facts={"escenas": len(escenas)})


def cuadricula(r: dict) -> dict:
    """Teselas MGRS con su disponibilidad → capa para colorear (por defecto, nubes mínima)."""
    if r.get("error"):
        return r
    filas = r.get("filas") or []
    feats = [{"type": "Feature", "geometry": f["huella"],
              "properties": {k: v for k, v in f.items() if k != "huella"}}
             for f in filas if f.get("huella")]
    hechos = {"teselas": len(feats), "escenas": sum(int(f["escenas"]) for f in filas),
              "ventana": f"{r.get('desde')}…{r.get('hasta')}",
              "fuente": "catálogo GeoParquet de Earth Search sentinel-2-c1-l2a (Source Cooperative)",
              "huella": "la de la escena con más cobertura de cada tesela"}
    return geo_result(
        [feature_collection(f"Imágenes Sentinel-2 {r.get('desde')}…{r.get('hasta')}",
                            {"type": "FeatureCollection", "features": feats}, crs="EPSG:4326")],
        facts=hechos, style_hint={"field": "nubes_min", "method": "quantile"},
    )


def escenas_catalogo(r: dict) -> dict:
    if r.get("error"):
        return r
    filas = r.get("filas") or []
    cols = ["id", "tile", "fecha", "nubes", "cobertura", "plataforma", "miniatura"]
    return geo_result([table(cols, filas, name="Escenas Sentinel-2")],
                      facts={"escenas": len(filas), "ventana": f"{r.get('desde')}…{r.get('hasta')}"})
