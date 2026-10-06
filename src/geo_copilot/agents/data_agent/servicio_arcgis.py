"""El discovery del núcleo sobre el servidor MCP de ArcGIS (T5.2).

El núcleo ya no tiene conectores de ArcGIS: busca en el Hub, describe servicios y trae capas
llamando al servidor `arcgis` registrado en `config/mcp_servers.yaml` (el mismo que puede usar
Claude Desktop). Estas funciones son el único punto por donde el núcleo lo hace; un fallo del
servidor se dice (no se convierte en «no hay resultados»).
"""

from __future__ import annotations

from typing import Any

from geo_copilot.core.logging import get_logger

from .hub_items import HubItem

logger = get_logger(__name__)

SERVIDOR = "arcgis"


class ArcGISNoDisponible(RuntimeError):
    """El servidor de ArcGIS no está configurado, no responde o la tool falló (el mensaje dice cuál)."""


async def _llamar(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    from geo_copilot.platform.mcp.connection import McpError
    from geo_copilot.platform.mcp.hub import hub_actual

    hub = hub_actual()
    if hub is None:
        raise ArcGISNoDisponible("no hay servidores MCP configurados (falta el de ArcGIS)")
    try:
        return await hub.llamar_directo(SERVIDOR, tool, {k: v for k, v in args.items() if v is not None})
    except McpError as exc:
        raise ArcGISNoDisponible(str(exc)) from exc


async def _descargar(uri: str, formato: str, crs: str) -> dict[str, Any]:
    from geo_copilot.platform.mcp.connection import McpError
    from geo_copilot.platform.mcp.hub import hub_actual
    from geo_copilot.platform.workspace.store import leer_remoto

    hub = hub_actual()
    if hub is None:
        raise ArcGISNoDisponible("no hay servidores MCP configurados (falta el de ArcGIS)")
    if crs.upper() != "EPSG:4326":
        raise ArcGISNoDisponible(f"el servidor de ArcGIS devolvió la capa en {crs or 'un CRS sin declarar'}")
    try:
        contenido = await hub.descargar_recurso(SERVIDOR, uri)
    except McpError as exc:
        raise ArcGISNoDisponible(str(exc)) from exc
    return leer_remoto(contenido, format=formato, crs_declarado=crs)


async def buscar_en_hub(**params: Any) -> tuple[list[HubItem], list[str]]:
    """(items sin ordenar, avisos del servidor). Sin ningún filtro no se consulta (catálogo mundial)."""
    filtros = ("text_query", "tags_all", "tags_any", "owner_any", "source_any", "service_types")
    if not any(params.get(k) for k in filtros):
        logger.warning("[discovery] búsqueda sin filtros: no se consulta el Hub")
        return [], []
    hechos = (await _llamar("arcgis_search_items", params)).get("facts") or {}
    items = [HubItem.from_dict(d) for d in hechos.get("items") or [] if isinstance(d, dict)]
    return items, [str(a) for a in hechos.get("avisos") or []]


async def describir(url: str) -> dict[str, Any]:
    """Capa, servicio o imagen: campos, cuántos elementos, extensión 4326 y (si es imagen) su descriptor."""
    return (await _llamar("arcgis_describe_service", {"url": url})).get("facts") or {}


async def consultar_capa(url: str, *, where: str | None = None, bbox: list[float] | None = None,
                         max_features: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(FeatureCollection EPSG:4326, hechos: total_en_servicio, traidos, completo, aviso…)."""
    if max_features is None:  # completa por defecto (paginada en el servidor), no una muestra
        from geo_copilot.core.config import get_settings

        max_features = get_settings().max_external_features
    sc = await _llamar("arcgis_query_features", {"url": url, "where": where, "bbox": bbox,
                                                "max_features": max_features})
    arts = [a for a in sc.get("artifacts") or [] if isinstance(a, dict)]
    fc = next((a.get("data") for a in arts if a.get("kind") == "feature_collection"), None)
    ref = next((a for a in arts if a.get("kind") == "feature_ref"), None)
    if fc is None and ref is not None:
        # capa grande: el servidor la deja como archivo (E2.1: 41.033 puntos llegaban cortados a 10.000)
        fc = await _descargar(str(ref.get("uri") or ""), str(ref.get("format") or "geojson"),
                              str(ref.get("crs") or ""))
    if not isinstance(fc, dict):
        raise ArcGISNoDisponible("el servidor de ArcGIS no devolvió la capa")
    return fc, sc.get("facts") or {}
