"""Constructores del contrato GeoMCP para servidores propios (plan §3.3–§3.4).

- `geo_meta(...)`: el bloque `_meta.geo` de una tool (qué entradas geo acepta,
  qué produce, costo, CRS de salida). Con él el núcleo resuelve argumentos
  desde el mapa y sabe qué esperar.
- `geo_result(...)`: el envoltorio `GeoResult` que va en `structuredContent`.
  El CRS de todo artefacto geo se DECLARA (regla dura 1); `facts` son hechos
  para narrar, nunca instrucciones (regla 3); `style_hint` es una sugerencia.

Sin dependencias del núcleo: un servidor de terceros puede copiar este archivo.
"""

from __future__ import annotations

from typing import Any, Literal

GEO_VERSION = "1"
Acepta = Literal["geometry", "bbox", "layer_ref", "iso_interval", "date"]


def geo_meta(
    *,
    inputs: dict[str, list[str]] | None = None,
    outputs: list[str] | None = None,
    cost: Literal["low", "medium", "high"] = "medium",
    crs_out: str | None = "EPSG:4326",
    geometry_types: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """`_meta` de una tool G1+: `{"geo": {...}}`.

    `inputs`: {nombre_arg: [lo que acepta]} p. ej. {"aoi": ["geometry", "layer_ref"]}.
    `geometry_types`: {nombre_arg: tipos GeoJSON que tienen sentido} p. ej.
    {"aoi": ["Polygon", "MultiPolygon"]} (un área, no un punto). Sin él, cualquiera.
    Con esto el cliente ofrece la tool en el menú contextual de lo que la admite.
    """
    tipos = geometry_types or {}
    return {"geo": {
        "version": GEO_VERSION,
        "inputs": {k: {"accepts": list(v), **({"geometry_types": list(tipos[k])} if k in tipos else {})}
                   for k, v in (inputs or {}).items()},
        "outputs": list(outputs or []),
        "cost": cost,
        "crs_out": crs_out,
    }}


def feature_collection(name: str, data: dict, *, crs: str) -> dict[str, Any]:
    return {"kind": "feature_collection", "name": name, "crs": crs, "data": data}


def feature_ref(name: str, uri: str, *, fmt: str, crs: str, feature_count: int | None = None,
                bbox: list[float] | None = None) -> dict[str, Any]:
    return {"kind": "feature_ref", "name": name, "uri": uri, "format": fmt, "crs": crs,
            "feature_count": feature_count, "bbox": bbox}


def raster_tiles(name: str, tiles: str, *, bounds: list[float] | None = None, minzoom: int = 0,
                 maxzoom: int = 22, legend: dict | None = None, datetime: str | None = None) -> dict[str, Any]:
    """Teselas XYZ servidas por el propio servidor (ruta relativa a su host).

    `datetime` (ISO, opcional): el instante que retrata (p. ej. la fecha de la escena). Con él
    el cliente arma series temporales (control de tiempo) y compara fechas.
    """
    art = {"kind": "raster_tiles", "name": name, "tiles": tiles, "bounds": bounds,
           "minzoom": minzoom, "maxzoom": maxzoom, "legend": legend}
    if datetime:
        art["datetime"] = datetime
    return art


def table(columns: list[str], rows: list[dict], *, geometry: dict | None = None,
          name: str | None = None) -> dict[str, Any]:
    """Tabla; `geometry` = {"column", "encoding": wkb|wkb_hex|wkt|geojson|latlon, "crs"} si trae."""
    art: dict[str, Any] = {"kind": "table", "columns": columns, "rows": rows}
    if name:
        art["name"] = name
    if geometry:
        art["geometry"] = geometry
    return art


def stats(items: list[dict[str, Any]], *, name: str | None = None) -> dict[str, Any]:
    art: dict[str, Any] = {"kind": "stats", "items": items}
    if name:
        art["name"] = name
    return art


def geo_result(artifacts: list[dict], *, facts: dict | None = None,
               style_hint: dict | None = None) -> dict[str, Any]:
    """`structuredContent` de una tool G1+. Valida la regla dura 1 (CRS declarado)."""
    for a in artifacts:
        if a.get("kind") in ("feature_collection", "feature_ref") and not a.get("crs"):
            raise ValueError(f"artefacto {a.get('name')!r} sin CRS declarado")
        g = a.get("geometry")
        if a.get("kind") == "table" and g and not g.get("crs"):
            raise ValueError(f"tabla {a.get('name')!r} con geometría sin CRS declarado")
    out: dict[str, Any] = {"geo_result": GEO_VERSION, "artifacts": artifacts, "facts": facts or {}}
    if style_hint:
        out["style_hint"] = style_hint
    return out


def compact_result(sc: dict[str, Any], *, max_text: int = 4000) -> Any:
    """`CallToolResult` con el `structuredContent` completo y, como texto, solo un RESUMEN.

    Por defecto el SDK repite el resultado entero como texto en `content`: una capa de 15 MB
    viajaba dos veces (y escapada, 64 MB) y un cliente que solo lee el texto (Claude Desktop)
    metía la geometría entera en el contexto del modelo. El resumen lleva los hechos y, de cada
    artefacto, su tipo, nombre, CRS y cuántos elementos tiene. `{"error": …}` sale como error.
    """
    import json

    from mcp.types import CallToolResult, TextContent

    if set(sc) == {"error"}:
        return CallToolResult(content=[TextContent(type="text", text=str(sc["error"]))],
                              structuredContent=sc, isError=True)
    arts = []
    for a in sc.get("artifacts") or []:
        r = {k: a.get(k) for k in ("kind", "name", "crs") if a.get(k) is not None}
        if a.get("kind") == "feature_collection":
            r["features"] = len((a.get("data") or {}).get("features") or [])
        elif a.get("kind") == "table":
            r["rows"] = len(a.get("rows") or [])
        arts.append(r)
    texto = json.dumps({"facts": sc.get("facts") or {}, "artifacts": arts}, ensure_ascii=False, default=str)
    if len(texto) > max_text:
        texto = texto[:max_text] + "… (resumen recortado; el resultado completo va en structuredContent)"
    return CallToolResult(content=[TextContent(type="text", text=texto)], structuredContent=sc)
