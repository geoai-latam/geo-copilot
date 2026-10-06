"""geo_mcp_kit — base común de los servidores GeoMCP propios (plan §3.7, S3.1).

Un servidor nuevo = tools + motor. El kit trae: auth por API key con scopes
fail-closed, rate limit con backend enchufable, ejecución con timeout y errores
honestos, `/health` y `/metrics`, rutas HTTP extra (teselas) con su scope, y los
constructores del contrato GeoMCP (`_meta.geo`, `GeoResult`).
"""

from geo_mcp_kit.asgi import ExtraRoute, GeoMcpAuth, jsonrpc_tool_names, respond_json, respond_png
from geo_mcp_kit.auth import ApiKey, KeyRing, MemoryRateBackend, RateLimiter, extract_bearer
from geo_mcp_kit.geo import (
    compact_result,
    feature_collection,
    feature_ref,
    geo_meta,
    geo_result,
    raster_tiles,
    stats,
    table,
)
from geo_mcp_kit.oidc import VerificadorJwt, llamante
from geo_mcp_kit.runner import ToolRunner

__all__ = [
    "ApiKey", "ExtraRoute", "GeoMcpAuth", "KeyRing", "MemoryRateBackend", "RateLimiter",
    "ToolRunner", "compact_result", "extract_bearer", "feature_collection", "feature_ref", "geo_meta",
    "geo_result", "jsonrpc_tool_names", "raster_tiles", "respond_json", "respond_png",
    "VerificadorJwt", "llamante", "stats", "table",
]
