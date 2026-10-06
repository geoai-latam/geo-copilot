"""imagery-mcp — servicio MCP independiente de análisis de imagery satelital.

C3-v1 del roadmap GEO_COPILOT (spec: docs/SPEC_C3_IMAGERY_MCP_2026-07-19.md).
Fuente analítica: STAC → COG Sentinel-2 L2A (Planetary Computer default,
Earth Search fallback), lectura ventaneada con rasterio. Expuesto por MCP
streamable HTTP con autenticación por API keys + scopes.
"""

__version__ = "0.1.0"
