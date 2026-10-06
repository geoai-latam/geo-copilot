"""
Prompts del RouterAgent — Fase 6 #2.

Extraídos del agente para que el prompt sea testeable de forma aislada,
versionable, y reusable. El agente sólo orquesta la sustitución de
variables y la llamada al LLM.
"""

from __future__ import annotations

from geo_copilot.prompts import cargar_prompt

ROUTER_SYSTEM_PROMPT_TEMPLATE = cargar_prompt("router")


_SCHEMA_FALLBACK = "Base de datos conectada (schema disponible al consultar)"

_NEUTRAL_SOURCES = (
    "FUENTES EXTERNAS DISPONIBLES:\n"
    "- Portales de datos abiertos regionales y ArcGIS Hub (las entidades "
    "oficiales concretas dependen de la región del usuario; no asumas un país)\n"
    "- URLs directas a archivos .geojson, .shp, .gpkg, .kml"
)


def format_region_sources(region_key: str | None) -> str:
    """F2.2: bloque 'FUENTES EXTERNAS DISPONIBLES' según la región de sesión.

    Antes el prompt fijaba 'datos.gov.co / IGAC / IDEAM / Catastro Bogotá' como
    las ÚNICAS fuentes. Ahora se rinde según la región:
      * ``region_key=None``      → región configurada del producto (default del
        despliegue) — su catálogo de entidades.
      * ``'global'`` o sin catálogo → texto neutral (sin anclar a ningún país).
      * key con catálogo          → label + entidades oficiales de esa región.
    """
    try:
        from geo_copilot.agents.data_agent.catalogo_regiones import (
            REGIONS,
            get_active_region,
            get_region,
        )
    except Exception:  # noqa: BLE001 — sin catálogo, degradar a neutral
        return _NEUTRAL_SOURCES

    key = region_key if region_key is not None else get_active_region()
    if not key or key == "global" or key not in REGIONS:
        return _NEUTRAL_SOURCES

    r = get_region(key)
    ent_keys = list(getattr(r, "entities", {}).keys())[:8]
    ents = ", ".join(ent_keys) if ent_keys else "entidades oficiales del catálogo"
    return (
        "FUENTES EXTERNAS DISPONIBLES:\n"
        f"- Región activa: {r.label}\n"
        f"- Servicios ArcGIS / portales oficiales: {ents}, y otros\n"
        "- URLs directas a archivos .geojson, .shp, .gpkg, .kml"
    )


def build_router_system_prompt(
    *,
    schema_info: str,
    conversation_context: str,
    services_context: str,
    external_data_context: str = "",
    session_region: str | None = None,
) -> str:
    """Render the router system prompt with the runtime substitutions.

    Centralizing this here makes the prompt:
      * versionable (one file, easy diff),
      * unit-testable (no LLM, just substitution),
      * shareable if the agent ever splits into helpers.

    ``session_region`` (F2.2) selecciona qué fuentes externas se presentan; su
    ausencia usa la región configurada (no un hardcode de Colombia).
    """
    return ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
        schema_info=schema_info if schema_info else _SCHEMA_FALLBACK,
        conversation_context=conversation_context,
        services_context=services_context,
        external_data_context=external_data_context,
        external_sources_context=format_region_sources(session_region),
    )
