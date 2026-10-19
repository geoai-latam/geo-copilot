"""Salidas: el agente prepara la DESCARGA de una capa en el formato y el sistema de referencia que
se pidan («pásame los cauces en shapefile en origen nacional»).

El archivo no viaja por el chat: la herramienta comprueba lo pedido (formato, CRS, que haya
elementos) y devuelve el enlace `[[descarga:…]]` que la respuesta incluye; el navegador lo
descarga con la credencial del usuario (el mismo camino que el botón «Exportar» de la capa).
"""

from __future__ import annotations

from typing import Any

from geo_copilot.platform.capabilities import Capability, ToolOutcome
from geo_copilot.platform.workspace.context import store_actual
from geo_copilot.platform.workspace.exportar import FORMATOS, ExportacionInvalida, crs_de_salida


def _ce():
    from geo_copilot.orchestrator import capabilities_espaciales

    return capabilities_espaciales


async def _exportar(graph: Any, working: dict, args: dict) -> ToolOutcome:
    ce = _ce()
    ds = await ce._resolver(args.get("dataset"), working)
    if ds is None:
        from geo_copilot.orchestrator.capacidades_operaciones import _falta

        return _falta(str(args.get("dataset")))
    formato = str(args.get("format") or "gpkg")
    if formato not in FORMATOS:
        return ToolOutcome(f"formato desconocido «{formato}»; válidos: {', '.join(FORMATOS)}", success=False)
    try:
        crs = crs_de_salida(args.get("crs") or None, formato)
    except ExportacionInvalida as exc:
        return ToolOutcome(str(exc), success=False)
    store, session_id = store_actual(), working.get("session_id") or ""
    ref = await store.get(session_id, ds) if store is not None and session_id else None
    if ref is None:
        return _falta_ds(ds)
    n = ref.feature_count or 0
    if n == 0:
        return ToolOutcome(f"«{ref.name}» no tiene elementos: no hay nada que descargar", success=False)
    f = FORMATOS[formato]
    consulta = f"formato={formato}" + (f"&crs={crs}" if formato != "kml" else "")
    enlace = f"[[descarga:{ds}?{consulta}|Descargar «{ref.name}» ({f.nombre})]]"
    hechos = {"capa": ref.name, "dataset": ds, "elementos": n, "formato": f.nombre, "crs": crs,
              "geometria": ref.geometry_type}
    notas = []
    if formato == "shp" and ref.geometry_type and "Geometry" in ref.geometry_type:
        notas.append("la capa mezcla tipos de geometría: el zip lleva un Shapefile por tipo")
    if formato == "shp":
        notas.append("los nombres de campo de más de 10 caracteres se recortan (límite del Shapefile)")
    if formato == "dxf":
        notas.append("el DXF lleva solo el dibujo, sin atributos")
    if notas:
        hechos["notas"] = notas
    return ToolOutcome(
        f"Descarga lista ({n} elementos). Pon en la respuesta EXACTAMENTE este enlace para que el "
        f"usuario la descargue: {enlace}\nHechos: {hechos}",
        success=True,
    )


def _falta_ds(ds: str) -> ToolOutcome:
    return ToolOutcome(f"el dataset {ds} no está en el workspace de la sesión", success=False)


SALIDAS: tuple[Capability, ...] = (
    Capability(
        id="core.export_layer", tool_name="export_layer",
        description=(
            "Prepara la DESCARGA de una capa del workspace como archivo: GeoPackage (`gpkg`), "
            "Shapefile en zip (`shp`), KML para Google Earth (`kml`), GeoJSON, CSV con la geometría en "
            "WKT (`csv`) o DXF para CAD (`dxf`, solo dibujo), en el sistema de referencia pedido "
            "(`crs`: p. ej. EPSG:4326 WGS84, EPSG:9377 MAGNA-SIRGAS Origen Nacional de Colombia, o "
            "cualquier EPSG). Si la capa está filtrada sale lo filtrado; con `seleccion`, lo "
            "seleccionado. Devuelve el enlace de descarga que va en la respuesta."
        ),
        parameters={
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "description": (
                    "`activa`, `seleccion`, un `ds_…` del workspace o el [id] de una capa del mapa.")},
                "format": {"type": "string", "enum": list(FORMATOS)},
                "crs": {"type": "string", "description": "Código EPSG de salida (por defecto EPSG:4326)."},
            },
            "required": ["dataset", "format"],
            "additionalProperties": False,
        },
        executor=_exportar, available=lambda _g: store_actual() is not None,
        geo_inputs={"dataset": {"accepts": ["dataset"]}},
        blurb="descarga de una capa como GeoPackage / Shapefile / KML / GeoJSON / CSV / DXF, en el CRS pedido.",
        risk="read", step=("agent_loop", "Preparando la descarga"),
    ),
)
