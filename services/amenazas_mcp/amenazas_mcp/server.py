"""amenazas-mcp — sismos, incendios, caudales de ríos y clima de fuentes públicas sin clave.

«¿Qué sismos fuertes hubo cerca de Cali este año?», «¿hay incendios activos en el Vichada?», «¿cómo viene el
caudal del Magdalena en Honda?», «¿cuánto llovió en Yopal en 2023?». Cuatro herramientas:

- `amenazas_sismos` (USGS), `amenazas_incendios` (NASA FIRMS, últimos 7 días),
- `amenazas_caudal` (GloFAS: la celda del punto Y sus vecinas, porque el río puede pasar por la de al lado),
- `clima_serie` (ERA5 por Open-Meteo —también pronóstico— o NASA POWER, con la elevación que usa cada modelo).

Las capas de amenaza OFICIALES de Colombia (SGC: sísmica, volcánica, movimientos en masa) están en ogc-mcp.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

from geo_mcp_kit import (
    GeoMcpAuth,
    KeyRing,
    RateLimiter,
    ToolRunner,
    compact_result,
    feature_collection,
    geo_meta,
    geo_result,
    table,
)
from geo_mcp_kit import area as areas
from geo_mcp_kit.red import ErrorRed, UrlNoPermitida
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

from amenazas_mcp import clima, fuentes, resumen

logger = logging.getLogger("amenazas_mcp")
SCOPES = {"amenazas_sismos": "amenazas:read", "amenazas_incendios": "amenazas:read",
          "amenazas_caudal": "amenazas:read", "clima_serie": "amenazas:read"}
runner = ToolRunner(timeout_s=170, service="amenazas-mcp",
                    expected_errors=(ErrorRed, UrlNoPermitida, resumen.ErrorAmenazas, areas.AreaInvalida))
mcp = FastMCP(
    "amenazas-mcp",
    instructions=("Sismos (USGS), incendios activos (NASA FIRMS), caudales de ríos (GloFAS) y clima (ERA5, NASA "
                  "POWER). Las capas oficiales de amenaza del SGC están en el servidor ogc."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
ZONA = {"area": ["geometry", "layer_ref"], "bbox": ["bbox", "layer_ref"]}
#: V5: con `viewport` (lo visible al empezar) se respondió por un país entero. Va en cada descripción con zona.
_ZONA_TXT = ("Para un país, departamento o municipio pasa su LÍMITE como `area` (lugares_limite); `viewport` "
             "es solo lo visible en el mapa, no un lugar.")


def _recortar(fc: dict, geom: Any, hechos: dict, clave: str) -> None:
    if geom is not None:
        hechos["filtro_area"] = areas.recortar(fc, geom)
        hechos[clave] = len(fc["features"])


@mcp.tool(
    description=("Sismos del catálogo de la USGS en una zona (`area` = una capa de la sesión, o `bbox`) y periodo "
                 "(`desde`/`hasta` AAAA-MM-DD; por defecto el último año), con `magnitud_min` (por defecto 2.5): "
                 "capa de puntos con magnitud, lugar, fecha y profundidad, los 5 mayores y cuántos hay en el "
                 f"catálogo. {_ZONA_TXT} Para la amenaza sísmica oficial (NSR-10, SGC) usa el servidor ogc."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs=ZONA, outputs=["feature_collection"], cost="low"),
)
def amenazas_sismos(area: dict | None = None, bbox: list[float] | None = None, desde: str | None = None,
                    hasta: str | None = None, magnitud_min: float = 2.5, max_sismos: int = 2000) -> CallToolResult:
    def buscar() -> dict:
        caja, geom = resumen.zona(area, bbox)
        h = hasta or date.today().isoformat()
        d = desde or (date.fromisoformat(h) - timedelta(days=365)).isoformat()
        fc, total = fuentes.sismos(caja, d, h, magnitud_min, max(1, min(int(max_sismos), 20000)))
        # V5: «sismos en Colombia» con `viewport` (lo visible, alrededor de Bogotá) dio 0 y se dijo «en Colombia»
        hechos = {"zona_consultada_bbox": caja, "periodo": [d, h], "magnitud_min": magnitud_min,
                  **resumen.sismos(fc, total)}
        _recortar(fc, geom, hechos, "sismos")
        return geo_result([feature_collection(f"Sismos M≥{magnitud_min:g}", fc, crs="EPSG:4326")], facts=hechos)

    return compact_result(runner.run(buscar))


@mcp.tool(
    description=("Focos de calor ACTIVOS (posibles incendios) de NASA FIRMS en una zona (`area` o `bbox`) en las "
                 "últimas 24 h, 48 h o 7 días (`periodo`: 24h, 48h, 7d), por `sensor` (viirs_snpp, viirs_noaa20, "
                 f"modis): capa de puntos con fecha, confianza y potencia (FRP). Solo los últimos 7 días. {_ZONA_TXT}"),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs=ZONA, outputs=["feature_collection"], cost="low"),
)
def amenazas_incendios(area: dict | None = None, bbox: list[float] | None = None, periodo: str = "7d",
                       sensor: str = "viirs_snpp") -> CallToolResult:
    def buscar() -> dict:
        if periodo not in fuentes.FIRMS_PERIODOS or sensor not in fuentes.FIRMS_SENSORES:
            raise resumen.ErrorAmenazas(f"periodo: {', '.join(fuentes.FIRMS_PERIODOS)}; sensor: "
                                        f"{', '.join(fuentes.FIRMS_SENSORES)}")
        caja, geom = resumen.zona(area, bbox)
        fc, hechos = resumen.incendios(fuentes.incendios(caja, periodo, sensor), periodo, sensor)
        hechos["zona_consultada_bbox"] = caja
        _recortar(fc, geom, hechos, "focos")
        return geo_result([feature_collection(f"Focos de calor ({periodo})", fc, crs="EPSG:4326")], facts=hechos)

    return compact_result(runner.run(buscar))


@mcp.tool(
    description=("Caudal diario de un río con GloFAS (modelo, malla ~5 km): la serie de los últimos `dias_pasados` "
                 "y el pronóstico de `dias_pronostico` (≤30) en la celda del punto, y el caudal medio de las celdas "
                 "VECINAS de mayor a menor (el río puede pasar por la de al lado: un río grande lleva cientos o "
                 "miles de m³/s). El punto: referencia (`punto`, una capa de un elemento) o `lon`/`lat`."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"punto": ["geometry", "layer_ref"]}, outputs=["table"], cost="low"),
)
def amenazas_caudal(punto: dict | None = None, lon: float | None = None, lat: float | None = None,
                    dias_pasados: int = 30, dias_pronostico: int = 7) -> CallToolResult:
    def consultar() -> dict:
        x, y = resumen.punto(punto, lon, lat)
        celdas = fuentes.caudal(x, y, max(0, min(int(dias_pasados), 92)), max(1, min(int(dias_pronostico), 30)), 2)
        serie, hechos = resumen.caudal(celdas, x, y)
        return geo_result([table(["fecha", "caudal_m3s"], serie)], facts={"punto": [x, y], **hechos})

    return compact_result(runner.run(consultar))


@mcp.tool(
    description=("Clima diario de un punto: temperatura media/máx./mín. (°C) y precipitación (mm) entre `desde` y "
                 "`hasta` (AAAA-MM-DD), con resumen mensual. `fuente`: era5 (Open-Meteo; si `hasta` es futuro, "
                 "pronóstico ≤16 días) o nasa_power. Cada fuente modela el punto a SU elevación (se dice: en "
                 "montaña cambia mucho la temperatura). El punto: referencia o `lon`/`lat`."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"punto": ["geometry", "layer_ref"]}, outputs=["table"], cost="low"),
)
def clima_serie(desde: str, hasta: str, punto: dict | None = None, lon: float | None = None,
                lat: float | None = None, fuente: str = "era5") -> CallToolResult:
    def consultar() -> dict:
        x, y = resumen.punto(punto, lon, lat)
        filas, hechos = clima.serie(x, y, desde, hasta, fuente)
        columnas = ["fecha", "t_media_c", "t_max_c", "t_min_c", "precipitacion_mm"]
        return geo_result([table(columnas, filas)], facts=hechos)

    return compact_result(runner.run(consultar))


def build_app():
    keys = KeyRing.from_json(os.environ.get("AMENAZAS_MCP_KEYS", "[]"), known_scopes={"amenazas:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("amenazas-mcp no arranca sin claves (AMENAZAS_MCP_KEYS)")
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="amenazas-mcp")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("AMENAZAS_MCP_PORT", "9930")))
