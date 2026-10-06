"""
Catálogo de discovery — multi-región, data-driven.

Carga `config/discovery_catalog.yaml` al import. Estructura:

```
default_region: colombia
regions:
  colombia:
    entities: {IGAC: {aliases, owners, sources, tags}, …}
    publishers: [...]
    zones: {bogota: {label, bbox, aliases, tags}, …}
  global:
    entities: {}
    ...
```

Cuando se quiere añadir un nuevo país/región o entidad, solo se edita el
YAML — sin cambios de código. `OFFICIAL_OWNERS_CO` y `OFFICIAL_SOURCES_CO`
son dicts derivados de la región activa y se usan en `hub_items._score`
para el bonus de "owner/source institucional" del ranker.

Para discovery sin sesgo regional, llamar con `region="global"` o pasar
`region=None` en los helpers que lo aceptan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Modelo en memoria
# =============================================================================
@dataclass
class EntityCatalog:
    """Filtros conocidos para una entidad institucional (e.g. IGAC)."""
    key: str
    aliases: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class ZoneCatalog:
    """Filtros conocidos para una zona geográfica (e.g. Bogotá)."""
    key: str
    label: str = ""
    bbox: list[float] | None = None
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class RegionCatalog:
    """Catálogo completo de una región/país."""
    key: str
    label: str = ""
    country_bbox: list[float] | None = None
    entities: dict[str, EntityCatalog] = field(default_factory=dict)
    publishers: list[str] = field(default_factory=list)
    zones: dict[str, ZoneCatalog] = field(default_factory=dict)


# =============================================================================
# Carga desde YAML
# =============================================================================
def _resolve_catalog_path() -> Path:
    """Resolver path al YAML del catálogo respetando settings.config_dir."""
    try:
        from geo_copilot.core.config import settings
        cfg_dir = Path(settings.config_dir)
    # Settings no importable/validable (ValidationError es ValueError) o sin
    # config_dir: se usa el directorio por defecto.
    except (ImportError, AttributeError, ValueError, TypeError):
        cfg_dir = Path("config")

    if not cfg_dir.is_absolute():
        # F7 (S7.2): primero el directorio de trabajo (la imagen de producción instala el paquete
        # en site-packages y trae `config/` en /app); si no está, el árbol del repo (desarrollo).
        en_cwd = Path.cwd() / cfg_dir
        cfg_dir = en_cwd if en_cwd.is_dir() else Path(__file__).resolve().parents[4] / cfg_dir

    return cfg_dir / "discovery_catalog.yaml"


def _load_raw_catalog() -> dict[str, Any]:
    """Cargar el catálogo desde YAML. Falla fuerte si está ausente o mal
    formado — preferimos error explícito a un catálogo hardcodeado que
    enmascararía un despliegue incompleto.
    """
    path = _resolve_catalog_path()
    if not path.exists():
        raise FileNotFoundError(
            f"[discovery_catalog] Catálogo no encontrado: {path}. "
            "El archivo config/discovery_catalog.yaml debe existir."
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not data.get("regions"):
        raise ValueError(
            f"[discovery_catalog] {path} no define 'regions' — catálogo inválido."
        )
    logger.info(
        f"[discovery_catalog] Loaded {len(data['regions'])} region(s) from {path}"
    )
    return data


def _build_region(key: str, raw: dict[str, Any]) -> RegionCatalog:
    entities = {
        ek: EntityCatalog(
            key=ek,
            aliases=list(ev.get("aliases") or []),
            owners=list(ev.get("owners") or []),
            sources=list(ev.get("sources") or []),
            tags=list(ev.get("tags") or []),
        )
        for ek, ev in (raw.get("entities") or {}).items()
    }
    zones = {
        zk: ZoneCatalog(
            key=zk,
            label=zv.get("label", zk),
            bbox=list(zv["bbox"]) if zv.get("bbox") else None,
            aliases=list(zv.get("aliases") or []),
            tags=list(zv.get("tags") or []),
        )
        for zk, zv in (raw.get("zones") or {}).items()
    }
    return RegionCatalog(
        key=key,
        label=raw.get("label", key),
        country_bbox=list(raw["country_bbox"]) if raw.get("country_bbox") else None,
        entities=entities,
        publishers=list(raw.get("publishers") or []),
        zones=zones,
    )


_raw = _load_raw_catalog()
DEFAULT_REGION: str = _raw.get("default_region", "colombia")
REGIONS: dict[str, RegionCatalog] = {
    rk: _build_region(rk, rv) for rk, rv in (_raw.get("regions") or {}).items()
}


# =============================================================================
# API pública multi-región
# =============================================================================
def get_active_region() -> str:
    """Región activa por defecto. Sobreescribible vía settings."""
    try:
        from geo_copilot.core.config import settings
        override = getattr(settings, "discovery_default_region", None)
        if isinstance(override, str) and override in REGIONS:
            return override
    except (ImportError, ValueError, TypeError):
        # Settings no disponibles u override no hasheable: se ignora el
        # override y se usa la región por defecto del YAML.
        pass
    return DEFAULT_REGION if DEFAULT_REGION in REGIONS else next(iter(REGIONS), "global")


def get_region(key: str | None) -> RegionCatalog:
    """Obtener catálogo de región. None / unknown → región activa."""
    if not key or key not in REGIONS:
        key = get_active_region()
    return REGIONS[key]


def list_region_keys() -> list[str]:
    return list(REGIONS.keys())


def find_entity_by_alias(text: str, region: str | None = None) -> str | None:
    """Resolver una mención NL ('Codazzi', 'IGAC') a una entity key.

    `region=None` usa la región activa. Pasa una key de región específica
    para buscar en otro país.
    """
    r = get_region(region)
    t = (text or "").lower()
    # Match por aliases (más específico primero — alias largos antes que cortos)
    candidates: list[tuple[int, str]] = []
    for ek, ent in r.entities.items():
        for alias in ent.aliases:
            a = alias.lower().strip()
            if a and a in t:
                candidates.append((len(a), ek))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    # Match por la entity key misma (case-insensitive)
    for ek in r.entities:
        if ek.lower() in t:
            return ek
    return None


def find_zone_by_alias(text: str, region: str | None = None) -> str | None:
    """Resolver una mención de zona ('bogotá', 'medellin') a una zone key."""
    r = get_region(region)
    t = (text or "").lower()
    # Normalizar tildes para matching laxo
    t = (
        t.replace("á", "a").replace("é", "e").replace("í", "i")
         .replace("ó", "o").replace("ú", "u")
    )
    candidates: list[tuple[int, str]] = []
    for zk, zone in r.zones.items():
        for alias in zone.aliases:
            a = alias.lower().strip()
            a = (a.replace("á", "a").replace("é", "e").replace("í", "i")
                  .replace("ó", "o").replace("ú", "u"))
            if a and a in t:
                candidates.append((len(a), zk))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    # Fallback a la key misma
    for zk in r.zones:
        if zk in t:
            return zk
    return None


def entity_filters(entity_key: str, region: str | None = None) -> EntityCatalog | None:
    """Filtros conocidos para una entidad. None si no existe."""
    r = get_region(region)
    return r.entities.get(entity_key)


def zone_filters(zone_key: str, region: str | None = None) -> ZoneCatalog | None:
    r = get_region(region)
    return r.zones.get(zone_key)


def all_official_owners(region: str | None = None) -> list[str]:
    """Owners de TODAS las entidades de la región."""
    r = get_region(region)
    return [o for ent in r.entities.values() for o in ent.owners]


def all_official_sources(region: str | None = None) -> list[str]:
    """Sources de entidades + publishers adicionales de la región.

    Lista plana usada como sesgo regional automático en el DiscoveryAgent.
    Cuando `region="global"` o la región no existe, devuelve [] — esto
    desactiva el sesgo y deja que Hub responda con su catálogo completo.
    """
    r = get_region(region)
    base = [s for ent in r.entities.values() for s in ent.sources]
    return list(dict.fromkeys(base + r.publishers))


def all_official_tags(region: str | None = None) -> list[str]:
    r = get_region(region)
    return [t for ent in r.entities.values() for t in ent.tags]


# =============================================================================
# Dicts derivados de la región activa — usados por `hub_search._score` para
# el bonus institucional del ranker. Se construyen una vez al import.
# =============================================================================
def _active_dict_owners() -> dict[str, list[str]]:
    r = get_region(None)
    return {ek: list(ent.owners) for ek, ent in r.entities.items() if ent.owners}


def _active_dict_sources() -> dict[str, list[str]]:
    r = get_region(None)
    return {ek: list(ent.sources) for ek, ent in r.entities.items() if ent.sources}


OFFICIAL_OWNERS_CO: dict[str, list[str]] = _active_dict_owners()
OFFICIAL_SOURCES_CO: dict[str, list[str]] = _active_dict_sources()


__all__ = [
    # Modelo
    "EntityCatalog",
    "ZoneCatalog",
    "RegionCatalog",
    # API multi-región
    "REGIONS",
    "DEFAULT_REGION",
    "get_active_region",
    "get_region",
    "list_region_keys",
    "find_entity_by_alias",
    "find_zone_by_alias",
    "entity_filters",
    "zone_filters",
    "all_official_owners",
    "all_official_sources",
    "all_official_tags",
    # Snapshot región activa (usado por hub_search._score)
    "OFFICIAL_OWNERS_CO",
    "OFFICIAL_SOURCES_CO",
]
