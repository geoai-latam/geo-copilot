"""Autenticación del MCP de imagery: sus scopes y su mapa tool → scope.

La mecánica (Bearer con sha256 en tiempo constante, rate limit por clave,
fail-closed) vive en `geo_mcp_kit` (S3.1); aquí solo lo propio del servicio.

- Scopes: `imagery:read` (búsqueda) · `imagery:compute` (ndvi/change/zonal/composite).
- Una tool fuera de `TOOL_SCOPES` se DENIEGA por defecto (fail-closed): toda
  tool nueva necesita su entrada aquí o responde 403.
"""

from __future__ import annotations

from collections.abc import Iterable

from geo_mcp_kit import ApiKey, RateLimiter, extract_bearer
from geo_mcp_kit import KeyRing as _KitKeyRing

SCOPE_READ = "imagery:read"
SCOPE_COMPUTE = "imagery:compute"
KNOWN_SCOPES = frozenset({SCOPE_READ, SCOPE_COMPUTE})

TOOL_SCOPES: dict[str, str] = {
    "imagery_search_scenes": SCOPE_READ,
    "imagery_ndvi": SCOPE_COMPUTE,
    "imagery_change": SCOPE_COMPUTE,
    "imagery_zonal_stats": SCOPE_COMPUTE,
    "imagery_composite": SCOPE_COMPUTE,
    # El catálogo solo lee metadatos (no calcula píxeles): scope de lectura.
    "imagery_catalog_grid": SCOPE_READ,
    "imagery_catalog_scenes": SCOPE_READ,
    "imagery_catalog_world": SCOPE_READ,
    # Ver, mirar el histograma y leer un píxel abren los COG de la escena: cómputo.
    "imagery_scene_view": SCOPE_COMPUTE,
    "imagery_band_histogram": SCOPE_COMPUTE,
    "imagery_pixel": SCOPE_COMPUTE,
    # El relieve abre los COG del DEM y calcula (la cuenca, un relleno de depresiones): cómputo.
    "imagery_terrain": SCOPE_COMPUTE,
    "imagery_watershed": SCOPE_COMPUTE,
}


class KeyRing(_KitKeyRing):
    """KeyRing del kit con los scopes y el mapa de este servicio."""

    def __init__(self, entries: Iterable[dict]) -> None:
        super().__init__(entries, known_scopes=KNOWN_SCOPES, tool_scopes=TOOL_SCOPES)

    @classmethod
    def from_json(cls, keys_json: str) -> KeyRing:  # type: ignore[override]
        import json

        return cls(json.loads(keys_json))


__all__ = [
    "KNOWN_SCOPES", "SCOPE_COMPUTE", "SCOPE_READ", "TOOL_SCOPES",
    "ApiKey", "KeyRing", "RateLimiter", "extract_bearer",
]
