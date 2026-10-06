"""arcgis-mcp — servidor GeoMCP de ArcGIS sobre geo_mcp_kit (F5, S5.2 / T5.2).

Lo que antes eran conectores dentro del núcleo (`connectors/arcgis.py`, `hub_search.py`,
`extent_utils`) es ahora un servidor aparte, usable por el copiloto y por cualquier cliente MCP
(Claude Desktop con su propia key):

- `arcgis_search_items`: el catálogo abierto de ArcGIS Hub con los filtros que se pidan.
- `arcgis_describe_service`: una capa, un servicio o un ImageServer (campos, geometría,
  cuántos elementos, extensión en EPSG:4326; si es imagen, el descriptor para el mapa).
- `arcgis_query_features`: elementos de una capa con el filtro EMPUJADO al servicio (`where`,
  `bbox`, campos), en EPSG:4326; si no se trae todo, los hechos lo dicen. Hasta MAX_EN_LINEA
  elementos van en la respuesta; más, como `feature_ref`: un GeoJSON que sirve este mismo servidor
  en `/resultados/…` (el núcleo lo descarga con su credencial; caduca en una hora).

Las URLs de servicios vienen de fuera: el servidor bloquea IPs internas y puertos raros y fija
la IP resuelta (anti DNS rebinding). `ARCGIS_ALLOWED_DOMAINS` (opcional) restringe a dominios.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid

from geo_mcp_kit import (
    ExtraRoute,
    GeoMcpAuth,
    KeyRing,
    RateLimiter,
    ToolRunner,
    compact_result,
    feature_collection,
    feature_ref,
    geo_meta,
    geo_result,
    respond_json,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

from arcgis_mcp import hub, rest
from arcgis_mcp.red import UrlNoPermitida, ip_validada

logger = logging.getLogger("arcgis_mcp")
SCOPES = {"arcgis_search_items": "arcgis:read", "arcgis_describe_service": "arcgis:read",
          "arcgis_query_features": "arcgis:read"}
MAX_EN_LINEA = 5000          # hasta aquí, capa en la respuesta; más, GeoJSON por referencia
MAX_BYTES_EN_LINEA = 8_000_000  # y que pese hasta esto (polígonos detallados: pocos y enormes)
TTL_RESULTADOS_S = 3600
RESULTADOS = os.environ.get("ARCGIS_RESULTADOS_DIR") or os.path.join(tempfile.gettempdir(), "arcgis-resultados")

runner = ToolRunner(timeout_s=120, service="arcgis-mcp",
                    expected_errors=(rest.ErrorArcGIS, hub.BusquedaInvalida, UrlNoPermitida))
mcp = FastMCP(
    "arcgis-mcp",
    instructions=("Servicios ArcGIS públicos: busca en el catálogo de ArcGIS Hub con `arcgis_search_items`, "
                  "mira qué tiene un servicio con `arcgis_describe_service` y trae los elementos de una capa "
                  "con `arcgis_query_features` (filtra en el servicio con where/bbox)."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _dominios() -> list[str] | None:
    crudo = os.environ.get("ARCGIS_ALLOWED_DOMAINS", "").strip()
    return [d.strip().lower() for d in crudo.split(",") if d.strip()] or None


def _url_permitida(url: str) -> str:
    """Valida antes de todo (el cliente HTTP lo vuelve a hacer al fijar la IP)."""
    url = (url or "").strip()
    ip_validada(url, _dominios())
    return url


@mcp.tool(
    description=("Busca datasets publicados en ArcGIS: en ArcGIS Hub y en ArcGIS Online a la vez (catálogos "
                 "abiertos mundiales), sin duplicados. Combina `text_query` con filtros (tags, owner, source, "
                 "tipo de servicio): el texto solo es ruidoso. `bbox` [minx,miny,maxx,maxy] EPSG:4326 deja solo "
                 "los items DE esa zona. Devuelve de cada uno: título, organización/créditos, dueño, tipo "
                 "(FeatureServer/MapServer/ImageServer), URL, extensión, fecha y, cuando se sabe, vistas, "
                 "completitud de sus metadatos (0-100) y si el servicio es de una sola capa: hechos para "
                 "juzgar cuál es la fuente adecuada."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"bbox": ["bbox", "layer_ref"]}, outputs=[], cost="low"),
)
def arcgis_search_items(text_query: str | None = None, tags_all: list[str] | None = None,
                        tags_any: list[str] | None = None, owner_any: list[str] | None = None,
                        source_any: list[str] | None = None, service_types: list[str] | None = None,
                        modified_after: str | None = None, bbox: list[float] | None = None,
                        only_loadable: bool = True, max_results: int = 50, sort: str | None = None) -> CallToolResult:
    def _buscar() -> dict:
        items, avisos = hub.buscar(text_query=text_query, tags_all=tags_all, tags_any=tags_any, owner_any=owner_any,
                                   source_any=source_any, service_types=service_types,
                                   modified_after=modified_after, bbox=bbox, only_loadable=only_loadable,
                                   max_results=max_results, sort=sort)
        return geo_result([], facts={"items": items, "encontrados": len(items),
                                     **({"avisos": avisos} if avisos else {})})

    return compact_result(runner.run(_buscar))


@mcp.tool(
    description=("Describe un servicio ArcGIS por su URL: una capa (…/FeatureServer/0: campos, geometría, "
                 "cuántos elementos tiene, extensión), un servicio con varias capas (la lista) o una imagen "
                 "(Map/ImageServer: extensión y el descriptor para verla en el mapa)."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
)
def arcgis_describe_service(url: str) -> CallToolResult:
    return compact_result(runner.run(lambda: geo_result([], facts=rest.describir(_url_permitida(url)))))


@mcp.tool(
    description=("Trae los elementos de una capa ArcGIS (…/FeatureServer/0 o …/MapServer/2) como capa en "
                 "EPSG:4326, con el filtro hecho EN el servicio: `where` (SQL de ArcGIS sobre sus campos), "
                 "`bbox` [minx,miny,maxx,maxy] EPSG:4326 y `out_fields`. Hasta `max_features` (máx. "
                 f"{rest.MAX_ELEMENTOS}); si piden la capa entera, no lo bajes: lo grande viaja como archivo. "
                 "Los hechos dicen cuántos hay en el servicio y si vino todo."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"bbox": ["bbox", "layer_ref"]}, outputs=["feature_collection", "feature_ref"],
                  cost="medium"),
)
def arcgis_query_features(url: str, where: str | None = None, bbox: list[float] | None = None,
                          out_fields: list[str] | None = None, max_features: int = 2000) -> CallToolResult:
    def _consultar() -> dict:
        fc, hechos = rest.consultar(_url_permitida(url), where=where, bbox=bbox, out_fields=out_fields,
                                    max_features=max_features)
        nombre = str(hechos.get("capa") or "ArcGIS")
        # pocos elementos pero PESADOS (rama arcgis-busqueda, V5: 1882 polígonos del RUNAP = 110 MB) no
        # caben en la respuesta MCP: el cliente se cansaba de esperar. Lo grande, por número o por peso,
        # viaja como archivo.
        texto = json.dumps(fc, ensure_ascii=False, separators=(",", ":"))
        if len(fc["features"]) <= MAX_EN_LINEA and len(texto) <= MAX_BYTES_EN_LINEA:
            return geo_result([feature_collection(nombre, fc, crs="EPSG:4326")], facts=hechos)
        os.makedirs(RESULTADOS, exist_ok=True)
        _purgar()
        ident = uuid.uuid4().hex
        with open(os.path.join(RESULTADOS, f"{ident}.geojson"), "w", encoding="utf-8") as fh:
            fh.write(texto)
        hechos["formato"] = "geojson"
        return geo_result([feature_ref(nombre, f"/resultados/{ident}.geojson", fmt="geojson", crs="EPSG:4326",
                                       feature_count=len(fc["features"]))], facts=hechos)

    return compact_result(runner.run(_consultar))


_candado_purga = threading.Lock()


def _purgar() -> None:
    """Resultados más viejos que el TTL fuera (el núcleo los descarga al momento)."""
    with _candado_purga:
        limite = time.time() - TTL_RESULTADOS_S
        for nombre in os.listdir(RESULTADOS):
            ruta = os.path.join(RESULTADOS, nombre)
            try:
                if os.path.getmtime(ruta) < limite:
                    os.remove(ruta)
            except OSError:
                continue


_RESULTADO_RE = re.compile(r"^/resultados/([0-9a-f]{32})\.geojson$")


async def _servir_resultado(scope, send, params, _key):
    ruta = os.path.join(RESULTADOS, f"{params}.geojson")
    if not os.path.isfile(ruta):
        await respond_json(send, 404, {"error": "resultado no encontrado o vencido; repite la consulta"})
        return
    with open(ruta, "rb") as fh:
        datos = fh.read()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/geo+json"),
                            (b"content-length", str(len(datos)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": datos})


def build_app():
    keys = KeyRing.from_json(os.environ.get("ARCGIS_MCP_KEYS", "[]"), known_scopes={"arcgis:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("arcgis-mcp no arranca sin claves (ARCGIS_MCP_KEYS)")
    ruta = ExtraRoute(lambda p: (m.group(1) if (m := _RESULTADO_RE.match(p)) else None), _servir_resultado,
                      requires_tool="arcgis_query_features", weight=0.2)
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="arcgis-mcp", routes=(ruta,))


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("ARCGIS_MCP_PORT", "9400")))
