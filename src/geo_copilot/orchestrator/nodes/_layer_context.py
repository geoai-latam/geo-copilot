"""
Helper compartido: resolver "qué capa está activa AHORA" para cualquier nodo.

Smart Router (2026-05-31): cuando el usuario pide ajustes sobre una capa
ya cargada ("cámbiala a verde", "haz buffer 500m"), el grafo NO debe
volver a buscar/consultar los datos. Pero los nodos individuales
(``symbology``, ``python_agent``) leían sólo ``state.geojson`` /
``state.external_geojson`` — campos que SE LLENAN en el turno actual.

Si la consulta entra directo al ``symbology_agent`` (skip de
``data_agent``/``gis_agent``), esos campos están vacíos y el nodo cree
que no hay datos. La capa viva está en ``state.previous_geojson``
(hidratado desde la sesión por ``routes/query.py``).

Este helper centraliza la resolución: devuelve el primer GeoJSON
disponible en el orden ``geojson → external_geojson → previous_geojson``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GraphState


def resolve_active_layer(state: GraphState) -> tuple[dict | None, str, str]:
    """Devolver ``(geojson, source_name, source_kind)`` de la capa activa.

    S1.4: delega en `orchestrator.layer_resolution.resolver_capa` (la regla
    única). ``source_kind`` es ``"internal" | "external" | "previous" | "none"``;
    la capa objetivo nombrada (FRT-04) se reporta como ``"external"`` (es una
    capa cargada del mapa).
    """
    from geo_copilot.orchestrator.layer_resolution import resolver_capa

    capa = resolver_capa(dict(state))
    if capa is None:
        return None, "sin capa activa", "none"
    por_defecto = {
        "target": "capa seleccionada",
        "internal": "datos del turno actual",
        "external": "servicio externo",
        "previous": "capa cargada previamente",
    }
    kind = "external" if capa.origen == "target" else capa.origen
    return capa.geojson, capa.name or por_defecto[capa.origen], kind
