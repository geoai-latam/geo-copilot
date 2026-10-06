"""hello-geo — servidor GeoMCP de ejemplo sobre geo_mcp_kit (S3.1/S3.3).

Muestra lo mínimo para enchufar un MCP al núcleo: tools con `_meta.geo`, un
resultado `GeoResult` que el núcleo lleva al mapa, y la auth del kit.
"""

from __future__ import annotations

import math
import os
from typing import Any

from geo_mcp_kit import (
    GeoMcpAuth,
    KeyRing,
    RateLimiter,
    ToolRunner,
    VerificadorJwt,
    feature_collection,
    geo_meta,
    geo_result,
    llamante,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

SCOPES = {"hello_circle": "hello:use", "hello_about": "hello:use"}
runner = ToolRunner(timeout_s=10, service="hello-geo")
mcp = FastMCP(
    "hello-geo", instructions="Servidor de ejemplo: círculos métricos alrededor de un punto.",
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _circle(lon: float, lat: float, meters: float, n: int = 64) -> dict:
    """Polígono de radio `meters` con los metros por grado del elipsoide WGS84 en esa latitud."""
    phi = math.radians(lat)
    m_lat = 111_132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_lon = 111_412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    dlat, dlon = meters / m_lat, meters / m_lon
    ring = [[lon + dlon * math.cos(2 * math.pi * i / n), lat + dlat * math.sin(2 * math.pi * i / n)] for i in range(n)]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


@mcp.tool(
    description="Círculo de `meters` metros de radio alrededor del punto (lon, lat) en EPSG:4326; "
                "devuelve la capa y su área aproximada.",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    # lon/lat son coordenadas numéricas, no una geometría que el núcleo resuelva desde una capa
    meta=geo_meta(inputs={}, outputs=["feature_collection"], cost="low"),
    structured_output=True,
)
def hello_circle(lon: float, lat: float, meters: float) -> dict[str, Any]:
    def calcular() -> dict:
        if not (0 < meters <= 50_000) or not (-180 <= lon <= 180) or not (-90 <= lat <= 90):
            return {"error": "parámetros fuera de rango (0 < meters ≤ 50 km, lon/lat válidos)"}
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": _circle(lon, lat, meters), "properties": {"radio_m": meters}},
        ]}
        area = math.pi * meters ** 2
        return geo_result([feature_collection(f"Círculo de {meters:g} m", fc, crs="EPSG:4326")],
                          facts={"radio_m": meters, "area_m2": round(area, 1), "area_ha": round(area / 1e4, 4)})

    return runner.run(calcular)


@mcp.tool(description="Qué es este servidor y a quién está atendiendo (texto; sin geometría).",
          annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def hello_about() -> str:
    quien = llamante()
    if quien is not None and quien.sub:  # F6: token del usuario (token exchange)
        atiendo = f" Te atiendo como el usuario «{quien.name.removeprefix('usuario:')}» de la organización «{quien.org}»."
    else:
        atiendo = f" Te atiendo con la clave de servicio «{quien.name}»." if quien is not None else ""
    return "hello-geo: ejemplo de servidor GeoMCP (G1) construido sobre geo_mcp_kit." + atiendo


def build_app():
    keys = KeyRing.from_json(os.environ.get("HELLO_GEO_KEYS", "[]"), known_scopes={"hello:use"}, tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("hello-geo no arranca sin claves (HELLO_GEO_KEYS)")
    # F6: además de sus claves, tokens de USUARIOS obtenidos por token exchange (HELLO_GEO_JWT_*)
    jwt = VerificadorJwt.desde_entorno("HELLO_GEO", tool_scopes=SCOPES, known_scopes={"hello:use"})
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="hello-geo", jwt=jwt)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("HELLO_GEO_PORT", "9200")))
