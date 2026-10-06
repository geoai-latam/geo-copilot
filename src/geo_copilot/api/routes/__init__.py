"""
Rutas de la API REST.
"""

from geo_copilot.api.routes.approval import router as approval_router
from geo_copilot.api.routes.discovery import router as discovery_router
from geo_copilot.api.routes.metadata import router as metadata_router
from geo_copilot.api.routes.proxy import router as proxy_router
from geo_copilot.api.routes.query import router as query_router
from geo_copilot.api.routes.session import router as session_router
from geo_copilot.api.routes.tiles import router as tiles_router

__all__ = [
    "query_router",
    "approval_router",
    "session_router",
    "metadata_router",
    "discovery_router",
    "proxy_router",
    "tiles_router",
]
