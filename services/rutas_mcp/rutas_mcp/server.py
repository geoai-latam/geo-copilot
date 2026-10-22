"""rutas-mcp — rutas, isócronas y matrices de tiempo/distancia sobre la red vial de OpenStreetMap (Valhalla).

Preguntas que no se responden con una línea recta: «¿cuánto se tarda de A a B?», «¿qué zona queda a 15
minutos del hospital?», «¿cuál de estos colegios está más cerca en tiempo?». Tres herramientas:

- `rutas_ruta`: la ruta entre puntos (capa de línea) con distancia, tiempo e indicaciones.
- `rutas_isocronas`: las zonas alcanzables en N minutos (capa de polígonos) con su área.
- `rutas_matriz`: tiempos y distancias de varios orígenes a varios destinos (tabla), y el más cercano.

Cada extremo se da como REFERENCIA (`punto` marcado, una capa o la selección) o con números (lon/lat,
p. ej. el centro que da lugares_buscar). Los tiempos son estimaciones del motor sin tráfico en vivo.
"""

from __future__ import annotations

import logging
import os
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
    herramientas_en_hilo,
    table,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

from rutas_mcp import puntos, valhalla

logger = logging.getLogger("rutas_mcp")
SCOPES = {"rutas_ruta": "rutas:read", "rutas_isocronas": "rutas:read", "rutas_matriz": "rutas:read"}
MAX_MATRIZ = 50
MOTOR = "Valhalla sobre OpenStreetMap; tiempos estimados por el motor, sin tráfico en vivo"
runner = ToolRunner(timeout_s=120, service="rutas-mcp", expected_errors=(valhalla.ErrorRutas, puntos.ErrorPuntos))
mcp = FastMCP(
    "rutas-mcp",
    instructions=("Rutas sobre la red vial (OpenStreetMap): `rutas_ruta` entre puntos, `rutas_isocronas` para "
                  "la zona alcanzable en N minutos y `rutas_matriz` para tiempos de varios orígenes a varios "
                  "destinos."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
_MODOS = ", ".join(valhalla.MODOS)


@mcp.tool(
    description=("Ruta por la red vial entre un origen y un destino (y paradas intermedias en orden) con "
                 f"distancia, tiempo estimado e indicaciones; la ruta queda como capa de línea. `modo`: {_MODOS}. "
                 "Cada extremo: como referencia (`origen`/`destino` = `punto`, una capa de un elemento o la "
                 "selección) o con números (`origen_lon`/`origen_lat`, p. ej. el centro de lugares_buscar). "
                 "Los tiempos son del motor, sin tráfico en vivo: dilo así."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"origen": ["geometry", "layer_ref"], "destino": ["geometry", "layer_ref"]},
                  outputs=["feature_collection"], cost="low"),
)
def rutas_ruta(origen: dict | None = None, destino: dict | None = None, origen_lon: float | None = None,
               origen_lat: float | None = None, destino_lon: float | None = None, destino_lat: float | None = None,
               paradas: list[list[float]] | None = None, modo: str = "auto") -> CallToolResult:
    def calcular() -> dict:
        notas: list[str] = []
        a = puntos.uno(origen, origen_lon, origen_lat, "origen", notas)
        b = puntos.uno(destino, destino_lon, destino_lat, "destino", notas)
        intermedios = [puntos.par(p, "parada") for p in paradas or []]
        viaje = valhalla.ruta([a, *intermedios, b], modo)
        linea = [c for tramo in viaje["legs"] for c in valhalla.decodificar(tramo["shape"])]
        s = viaje["summary"]
        hechos: dict[str, Any] = {
            "distancia_km": round(s["length"], 2), "tiempo_min": round(s["time"] / 60, 1), "modo": modo,
            "origen": list(a), "destino": list(b), "paradas": len(intermedios), "motor": MOTOR,
            "indicaciones": [m.get("instruction") for t in viaje["legs"] for m in t.get("maneuvers", [])][:15]}
        if notas:
            hechos["notas"] = notas
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {
            "type": "LineString", "coordinates": linea}, "properties": {
            "distancia_km": hechos["distancia_km"], "tiempo_min": hechos["tiempo_min"], "modo": modo}}]}
        return geo_result([feature_collection(f"Ruta ({modo})", fc, crs="EPSG:4326")], facts=hechos)

    return compact_result(runner.run(calcular))


@mcp.tool(
    description=("Isócronas: la zona alcanzable por la red vial en N minutos desde un punto (capa de polígonos, "
                 f"una por tiempo, con su área en km²). `minutos`: hasta 4 valores, máx. 120. `modo`: {_MODOS}. "
                 "El centro como referencia (`centro` = `punto`, una capa de un elemento) o con números "
                 "(`centro_lon`/`centro_lat`). Para «qué queda a menos de N minutos de X»: la isócrona y luego "
                 "cruzarla con la otra capa."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"centro": ["geometry", "layer_ref"]}, outputs=["feature_collection"], cost="medium"),
)
def rutas_isocronas(minutos: list[int], centro: dict | None = None, centro_lon: float | None = None,
                    centro_lat: float | None = None, modo: str = "auto") -> CallToolResult:
    def calcular() -> dict:
        tiempos = sorted({int(m) for m in minutos or []})
        if not tiempos or len(tiempos) > 4 or tiempos[0] < 1 or tiempos[-1] > 120:
            raise valhalla.ErrorRutas("`minutos`: de 1 a 4 valores entre 1 y 120")
        notas: list[str] = []
        c = puntos.uno(centro, centro_lon, centro_lat, "centro", notas)
        fc = valhalla.isocronas(c, tiempos, modo)
        areas = {}
        for f in fc.get("features", []):
            m = int((f.get("properties") or {}).get("contour", 0))
            km2 = puntos.area_km2(f["geometry"])
            f["properties"] = {"minutos": m, "area_km2": km2, "modo": modo}
            areas[f"{m} min"] = km2
        hechos = {"centro": list(c), "modo": modo, "area_km2_por_tiempo": areas, "motor": MOTOR,
                  **({"notas": notas} if notas else {})}
        return geo_result([feature_collection(f"Isócronas ({modo})", fc, crs="EPSG:4326")], facts=hechos)

    return compact_result(runner.run(calcular))


@mcp.tool(
    description=("Matriz de tiempos y distancias por la red vial de cada origen a cada destino (tabla), y para "
                 "cada origen su destino más cercano EN TIEMPO. Orígenes y destinos como referencia (una capa "
                 f"de puntos, la selección o `punto`; hasta {MAX_MATRIZ} de cada lado; de polígonos se usa su "
                 f"centro y se dice) o con números `origenes_lonlat`/`destinos_lonlat`. `modo`: {_MODOS}."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"origenes": ["geometry", "layer_ref"], "destinos": ["geometry", "layer_ref"]},
                  outputs=["table"], cost="medium"),
)
def rutas_matriz(origenes: dict | None = None, destinos: dict | None = None,
                 origenes_lonlat: list[list[float]] | None = None, destinos_lonlat: list[list[float]] | None = None,
                 modo: str = "auto") -> CallToolResult:
    def calcular() -> dict:
        notas: list[str] = []
        a = puntos.varios(origenes, origenes_lonlat, "origenes", MAX_MATRIZ, notas)
        b = puntos.varios(destinos, destinos_lonlat, "destinos", MAX_MATRIZ, notas)
        res = valhalla.matriz([p for p, _ in a], [p for p, _ in b], modo)
        filas, mas_cercano = puntos.tabla_matriz(res, a, b)
        hechos = {"origenes": len(a), "destinos": len(b), "modo": modo, "mas_cercano_por_origen": mas_cercano,
                  "motor": MOTOR, **({"notas": notas} if notas else {})}
        return geo_result([table(list(filas[0]) if filas else [], filas)], facts=hechos)

    return compact_result(runner.run(calcular))


def build_app():
    keys = KeyRing.from_json(os.environ.get("RUTAS_MCP_KEYS", "[]"), known_scopes={"rutas:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("rutas-mcp no arranca sin claves (RUTAS_MCP_KEYS)")
    return GeoMcpAuth(herramientas_en_hilo(mcp).streamable_http_app(), keys, RateLimiter(), service="rutas-mcp")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("RUTAS_MCP_PORT", "9910")))
