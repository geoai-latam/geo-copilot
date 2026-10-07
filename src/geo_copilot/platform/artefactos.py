"""Resultado del grafo → artefactos tipados del contrato (F4, S4.1).

El grafo devuelve un dict con campos por fuente (`geojson`, `external_geojson`,
`external_imagery`, `data`, `visualization`, `found_services`, `symbology`…).
Aquí se traduce, UNA vez y en el borde, a la lista de artefactos que el frontend
dibuja con su registro de renderers: capa + tabla + gráfico + servicios pueden
salir del mismo turno.

No decide nada del contenido: solo lo describe con el contrato. Si algo no
cumple el contrato se descarta ese artefacto (y se registra) en vez de romper la
respuesta: la narración del LLM sigue llegando.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from geo_copilot.core.logging import get_logger
from geo_copilot.platform.contracts import (
    MAX_INLINE_FEATURES,
    ChartOut,
    ChartSpec,
    LayerArtifact,
    LayerRef,
    MapCommand,
    MapCommandOut,
    SetStyle,
    SetStyleArgs,
    Stat,
    StatsOut,
    StyleSpec,
    TableOut,
)
from geo_copilot.platform.contracts.artifacts import ServiceCard, ServicesOut, VectorTilesOut

logger = get_logger(__name__)

_ORDEN: TypeAdapter[Any] = TypeAdapter(MapCommand)

_GEOM_COLS = frozenset({"geom", "geometry", "geom_geojson", "shape", "the_geom", "wkb_geometry"})
_RESTYLE = frozenset({"apply_symbology", "symbology"})
_CHARTS = frozenset({"bar", "line", "pie", "scatter", "histogram", "area"})
_CAMPOS_ESTILO = set(StyleSpec.model_fields) - {"pinned"}


def _huella(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def estilo_de(symbology: dict | None) -> StyleSpec | None:
    """La simbología del agente (SymbologyConfig) como StyleSpec del contrato."""
    if not isinstance(symbology, dict) or not symbology:
        return None
    datos = {k: v for k, v in symbology.items() if k in _CAMPOS_ESTILO and v not in (None, "", [], {})}
    for k in ("fill", "stroke", "marker", "label"):
        if isinstance(datos.get(k), dict):
            datos[k] = {kk: vv for kk, vv in datos[k].items() if vv is not None}
    datos["class_breaks"] = [
        {kk: vv for kk, vv in b.items() if kk in ("label", "color", "min_value", "max_value", "count")}
        for b in symbology.get("class_breaks") or [] if isinstance(b, dict)
    ]
    try:
        return StyleSpec.model_validate(datos)
    except ValidationError as exc:
        logger.warning("[artefactos] simbología fuera de contrato, se entrega sin estilo: %s", exc)
        return None


def _procedencia(result: dict, capacidad: str | None = None) -> dict:
    return {"capability": capacidad or f"core.{result.get('intent') or 'consulta'}",
            "produced_at": datetime.now(UTC).isoformat(), "sql": result.get("sql")}


def _capa_vectorial(result: dict, layer_ref: dict | None, tiles: dict | None,
                    replaces: str | None) -> LayerArtifact | None:
    # Solo `geojson`: `external_geojson` también lleva la capa del mapa inyectada
    # (A4a) en cada turno, y reemitirla la duplicaba (V5 F4).
    geojson = result.get("geojson")
    features = geojson.get("features") if isinstance(geojson, dict) else None
    tiene = bool(features)
    estilo = estilo_de(result.get("symbology"))
    if layer_ref:
        ref = LayerRef.model_validate({**layer_ref, "style": estilo.model_dump() if estilo else None})
    elif features and len(features) <= MAX_INLINE_FEATURES:
        # sin workspace (best-effort caído): la capa viaja solo inline
        ref = LayerRef.model_validate({
            "id": f"tmp_{_huella(geojson)}", "name": str(result.get("layer_name") or str(result.get("query") or "")[:60] or "Resultado"),
            "kind": "vector", "provider": "core", "crs": "EPSG:4326",
            "storage": {"kind": "geojson-inline", "data": geojson},
            "provenance": _procedencia(result), "feature_count": len(features),
            "style": estilo.model_dump() if estilo else None,
        })
    else:
        return None
    return LayerArtifact(
        layer=ref,
        inline=geojson if tiene and tiles is None else None,
        tiles=VectorTilesOut(url_template=tiles["url"], source_layer=tiles.get("source_layer") or "dataset",
                             fields=list(tiles.get("fields") or [])) if tiles else None,
        replaces=replaces,
    )


def _instante(valor: object) -> str:
    """Una fecha de escena («2026-03-14») como instante con zona (UTC): el contrato lo exige."""
    texto = str(valor)
    return f"{texto[:10]}T00:00:00+00:00" if len(texto) == 10 else texto


def _capa_raster(imagery: dict) -> LayerArtifact | None:
    url = str(imagery.get("service_url") or "")
    if not url:
        return None
    ext = imagery.get("extent") or None
    bbox = [ext["xmin"], ext["ymin"], ext["xmax"], ext["ymax"]] if ext else None
    m = re.match(r"^/api/v1/proxy/mcp/([a-z][a-z0-9_]*)/", url)
    if "{z}" in url:
        storage: dict[str, Any] = {"kind": "raster-tiles", "url_template": url,
                                   "legend": imagery.get("legend") or None,
                                   **({"cog": imagery["cog"]} if imagery.get("cog") else {})}
    else:
        storage = {"kind": "arcgis-image", "service_url": url}
    ref = LayerRef.model_validate({
        "id": f"rast_{_huella(url)}", "name": str(imagery.get("name") or "Imagen"),
        "kind": "raster", "provider": f"mcp:{m.group(1)}" if m else "external",
        "crs": "EPSG:3857", "storage": storage, "bbox": bbox,
        "provenance": imagery.get("provenance") or {
            "capability": f"mcp.{m.group(1)}" if m else "core.load_external",
            "produced_at": datetime.now(UTC).isoformat()},
        # FH.10: el instante que retrata (serie temporal, comparar fechas)
        **({"time": {"start": _instante(imagery["time"])}} if imagery.get("time") else {}),
    })
    return LayerArtifact(layer=ref)


def _filas(result: dict) -> list[dict]:
    data = result.get("data")
    filas = data.get("results") if isinstance(data, dict) else None
    return [{k: v for k, v in _plana(f).items() if k not in _GEOM_COLS} for f in (filas or []) if isinstance(f, dict)]


def _plana(fila: dict) -> dict:
    """Una fila que es una FEATURE ({id, properties, geometry}) se muestra por sus atributos
    (V5 en Chrome: la tabla salía «Id | Properties» con el JSON entero en una celda)."""
    props = fila.get("properties")
    if isinstance(props, dict) and ("geometry" in fila or fila.get("type") == "Feature" or set(fila) <= {"id", "properties", "type", "geometry", "bbox"}):
        return dict(props)
    return fila


def _tabulares(result: dict, layer_ref: dict | None, row_count: int) -> list[Any]:
    filas = _filas(result)
    if not filas:
        return []
    viz = result.get("visualization") or {}
    columnas = list(dict.fromkeys(k for f in filas for k in f))
    if set(columnas) == {"métrica", "valor"}:
        return [StatsOut(items=[Stat(label=str(f["métrica"]), value=f["valor"]) for f in filas
                                if isinstance(f.get("valor"), (int, float, str)) or f.get("valor") is None])]
    salida: list[Any] = []
    if viz.get("type") == "chart" and viz.get("chart_type") in _CHARTS and viz.get("x_axis") and viz.get("y_axis"):
        salida.append(ChartOut(spec=ChartSpec(chart_type=viz["chart_type"], x_key=viz["x_axis"],
                                              y_key=viz["y_axis"], title=viz.get("title")),
                               data=filas[:MAX_INLINE_FEATURES]))
    # La tabla va SIEMPRE que hay filas, también junto al gráfico: son las cifras
    # detrás de las barras (V5 F4: «tabla y gráfico del área» mostraba el gráfico
    # y, como tabla, los atributos de la capa — sin el área).
    salida.append(TableOut(title=None if salida else viz.get("title"), columns=columnas,
                           preview=filas[:MAX_INLINE_FEATURES], total_rows=max(row_count, len(filas)),
                           rows_ref=(layer_ref or {}).get("id")))
    return salida


def _servicios(result: dict) -> ServicesOut | None:
    if not result.get("new_search_executed"):
        return None
    tarjetas = []
    for s in result.get("found_services") or []:
        if not isinstance(s, dict):
            continue
        vistas = s.get("views")
        tarjetas.append(ServiceCard(
            name=str(s.get("name") or s.get("title") or "Sin nombre"), url=str(s.get("url") or ""),
            type=str(s.get("type") or s.get("service_type") or "desconocido"),
            description=str(s.get("description") or ""),
            layer_count=s.get("layer_count") if isinstance(s.get("layer_count"), int) else None,
            source=s.get("source") if isinstance(s.get("source"), str) else None,
            credits=(str(s.get("credits") or s.get("org") or "").strip() or None),
            views=vistas if isinstance(vistas, int) and vistas >= 0 else None,
        ))
    return ServicesOut(items=tarjetas) if tarjetas else None


def _capa_con_dataset(result: dict, dataset_id: str | None) -> str | None:
    """El [id] de la capa del mapa que ya lleva este dataset, si hay una."""
    if not dataset_id:
        return None
    for capa in ((result.get("map_context") or {}).get("layers") or []):
        if isinstance(capa, dict) and capa.get("dataset_id") == dataset_id and capa.get("id"):
            return str(capa["id"])
    return None


def construir_artefactos(result: dict, *, layer_ref: dict | None, tiles: dict | None,
                         target_layer_id: str | None, row_count: int) -> list[Any]:
    """Los artefactos del turno, en el orden en que se muestran."""
    artefactos: list[Any] = []
    restyle = result.get("intent") in _RESTYLE
    # La capa del turno es un dataset que YA está en el mapa (p. ej. se le añadió un campo):
    # se actualiza en su sitio, no se añade otra encima (V5 en Chrome).
    misma = _capa_con_dataset(result, (layer_ref or {}).get("id"))
    pasos = [
        lambda: _capa_vectorial(result, layer_ref, tiles, misma or (target_layer_id if restyle else None)),
        # FH.10: los rasters anteriores del turno (el NDVI de marzo antes que el de junio), en orden
        *[(lambda img=img: _capa_raster(img)) for img in (result.get("imagery_previas") or []) if isinstance(img, dict)],
        lambda: _capa_raster(result["external_imagery"]) if result.get("external_imagery") else None,
    ]
    for paso in pasos:
        try:
            art = paso()
        except (ValidationError, KeyError, TypeError) as exc:
            logger.warning("[artefactos] capa fuera de contrato, descartada: %s", exc)
            art = None
        if art is not None:
            artefactos.append(art)
    # re-estilo sin datos nuevos: una orden al mapa sobre la capa que ya está
    hay_capa = any(isinstance(a, LayerArtifact) and a.layer.kind == "vector" for a in artefactos)
    estilo = estilo_de(result.get("symbology"))
    if restyle and estilo is not None and not hay_capa and target_layer_id:
        artefactos.append(MapCommandOut(command=SetStyle(
            layer_id=target_layer_id, args=SetStyleArgs(style=estilo))))
    # FH.1: las órdenes al mapa que decidió el agente (zoom, visibilidad, orden…),
    # en el orden en que las dio. Una que no cumple el contrato no llega al mapa.
    for orden in result.get("map_commands") or []:
        try:
            artefactos.append(MapCommandOut(command=_ORDEN.validate_python(orden)))
        except ValidationError as exc:
            logger.warning("[artefactos] orden al mapa fuera de contrato, descartada: %s", exc)
    # Los resultados analíticos de pasos ANTERIORES del turno (ReAct), en orden: el
    # último viaja en `data`/`visualization` (ya recortado al tope inline).
    analiticos = [a for a in (result.get("analiticos") or []) if isinstance(a, dict)]
    previos = analiticos[:-1] if result.get("data") else analiticos
    for a in previos:
        filas = ((a.get("data") or {}).get("results")) or []
        try:
            artefactos.extend(_tabulares({**result, **a}, None, len(filas)))
        except ValidationError as exc:
            logger.warning("[artefactos] resultado analítico previo fuera de contrato, descartado: %s", exc)
    try:
        artefactos.extend(_tabulares(result, layer_ref, row_count))
    except ValidationError as exc:
        logger.warning("[artefactos] tabla/gráfico fuera de contrato, descartado: %s", exc)
    servicios = _servicios(result)
    if servicios is not None:
        artefactos.append(servicios)
    return artefactos
