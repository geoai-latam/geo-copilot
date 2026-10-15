"""ogc-mcp — servicios OGC (WMS, WFS, OGC API - Features) con un catálogo curado de Colombia.

La brecha que cierra: la información oficial de Colombia (IGAC, Parques Nacionales, SGC, DANE…) se publica
como servicios OGC, y el copiloto solo sabía de ArcGIS REST. Cuatro herramientas, para que el LLM decida:

- `ogc_catalogo`: los servicios curados y verificados (qué entidad, qué temas, URL por protocolo).
- `ogc_describir`: lo que ofrece un servicio (sus capas, con título, resumen y extensión).
- `ogc_ver_mapa`: una capa WMS en el mapa (imagen por teselas que sirve este servidor).
- `ogc_traer_elementos`: los elementos de una capa WFS / OGC API como capa vectorial (con sus atributos,
  para contar, cruzar o medir), filtrados en el servicio por bbox.

Las URLs vienen de fuera: el servidor bloquea IPs internas y fija la IP resuelta (kit `red`);
`OGC_ALLOWED_DOMAINS` (opcional) restringe a dominios.
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
from typing import Any

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
    raster_tiles,
    respond_json,
    respond_png,
)
from geo_mcp_kit.red import UrlNoPermitida, ip_validada
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

from ogc_mcp import catalogo, elementos, ows, teselas

logger = logging.getLogger("ogc_mcp")
SCOPES = {"ogc_catalogo": "ogc:read", "ogc_describir": "ogc:read", "ogc_ver_mapa": "ogc:read",
          "ogc_traer_elementos": "ogc:read"}
MAX_EN_LINEA = 5000
MAX_BYTES_EN_LINEA = 8_000_000
TTL_RESULTADOS_S = 3600
RESULTADOS = os.environ.get("OGC_RESULTADOS_DIR") or os.path.join(tempfile.gettempdir(), "ogc-resultados")

runner = ToolRunner(timeout_s=170, service="ogc-mcp", expected_errors=(ows.ErrorOgc, UrlNoPermitida))
mcp = FastMCP(
    "ogc-mcp",
    instructions=("Servicios OGC (WMS/WFS/OGC API - Features): mira el catálogo curado de Colombia con "
                  "`ogc_catalogo`, qué capas tiene un servicio con `ogc_describir`, pon una capa WMS en el mapa "
                  "con `ogc_ver_mapa` y trae elementos de una capa WFS/OGC API con `ogc_traer_elementos`."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _url(url: str) -> str:
    url = (url or "").strip()
    ip_validada(url, ows.dominios())  # antes de todo; el cliente lo repite al fijar la IP
    return url


@mcp.tool(
    description=("Catálogo curado de servicios OGC oficiales de Colombia (IGAC: datos fundamentales, suelos, "
                 "ordenamiento; Parques Nacionales: áreas protegidas; SGC: amenazas y geología; DANE: marco "
                 "geoestadístico…), verificados en vivo. Cada servicio trae entidad, temas, descripción y su URL "
                 "por protocolo (wms = ver como imagen; wfs/ogcapi = traer elementos). Sin filtro devuelve todo "
                 "(es corto); `texto` deja las que contienen alguna de sus palabras, primero las que más. Con la "
                 "URL elegida, `ogc_describir` dice qué capas tiene."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    meta=geo_meta(inputs={}, outputs=[], cost="low"),
)
def ogc_catalogo(texto: str | None = None, entidad: str | None = None) -> CallToolResult:
    return compact_result(runner.run(lambda: geo_result([], facts=catalogo.buscar(texto, entidad))))


@mcp.tool(
    description=("Describe un servicio OGC por su URL (WMS, WFS u OGC API - Features): sus capas con nombre (el "
                 "que piden las otras herramientas), título, resumen y extensión en EPSG:4326; en un WMS, sus "
                 "estilos; en un WFS, si entrega GeoJSON. `tipo` (wms/wfs/ogcapi) fuerza el protocolo; sin él se "
                 "prueba. Un servicio con muchas capas se acota con `filtro` (palabra del nombre o título); si "
                 "quedan hasta 3 capas WFS, cada una trae sus atributos (los nombres que usa `campos`)."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=[], cost="low"),
)
def ogc_describir(url: str, tipo: str | None = None, filtro: str | None = None) -> CallToolResult:
    def describir() -> dict:
        caps = ows.describir(_url(url), tipo)
        hechos = ows.resumir(caps, filtro)
        ows.con_campos(caps, hechos)
        return geo_result([], facts=hechos)

    return compact_result(runner.run(describir))


def _plano(texto: str | None) -> str:
    return catalogo._plano((texto or "").strip())


def _capa_wms(caps: dict, capa: str) -> tuple[dict, str | None]:
    """(capa, nota): por su NOMBRE o, si no, por su TÍTULO exacto cuando una sola lo tiene.

    V5 (ogc-mcp): en los WMS de ArcGIS la capa se llama «9» y se titula «Departamento»; el agente pidió
    «Departamento» (y el nombre del WFS) dos veces y se rindió. El título exacto y único es un hecho del
    servicio, no una suposición.
    """
    encontrada = next((c for c in caps["capas"] if c["nombre"] == capa), None)
    if encontrada is not None:
        return encontrada, None
    buscado = _plano(capa.split(":")[-1])
    por_titulo = [c for c in caps["capas"] if _plano(c.get("titulo")) == buscado]
    if len(por_titulo) == 1:
        c = por_titulo[0]
        return c, f"«{capa}» es el título de la capa «{c['nombre']}» del WMS: se usó esa"
    nombres = ", ".join(f"{c['nombre']} ({c.get('titulo')})" for c in caps["capas"][:25])
    raise ows.ErrorOgc(f"la capa «{capa}» no está en el WMS; las que hay (nombre (título)): {nombres}")


@mcp.tool(
    description=("Pone una capa de un WMS en el mapa como imagen (teselas): `url` del WMS y `capa` = el NOMBRE "
                 "que dio ogc_describir (no el título). `estilo` opcional (uno de los suyos). Es solo imagen: para "
                 "contar, medir o cruzar sus elementos usa ogc_traer_elementos sobre el WFS del mismo servicio."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["raster_tiles"], cost="low"),
)
def ogc_ver_mapa(url: str, capa: str, estilo: str | None = None) -> CallToolResult:
    def ver() -> dict:
        caps = ows.capacidades_wms(_url(url))
        c, nota_capa = _capa_wms(caps, capa)
        version = caps.get("version") or "1.3.0"
        crs, nota_crs = teselas.crs_web(c), None
        if crs is None:
            if not teselas.atiende_3857(caps["url"], version, c["nombre"], c.get("bbox_4326")):
                raise ows.ErrorOgc(f"la capa «{capa}» no se dibuja en web-mercator (EPSG:3857): no se puede "
                                   "poner en el mapa como teselas")
            crs, nota_crs = "EPSG:3857", "el servicio no declara EPSG:3857 pero lo atiende (comprobado con una tesela)"
        if estilo and estilo not in c["estilos"]:
            raise ows.ErrorOgc(f"el estilo «{estilo}» no es de la capa; los suyos: {', '.join(c['estilos']) or '—'}")
        ficha = teselas.ficha(caps["url"], c["nombre"], estilo or "", version, crs)
        nombre = c.get("titulo") or c["nombre"]
        hechos = {"capa": nombre, "nombre_en_servicio": c["nombre"], "servicio": caps.get("titulo"),
                  "entidad": caps.get("entidad"), "bbox_4326": c.get("bbox_4326"), "crs_teselas": nota_crs,
                  "capa_resuelta": nota_capa,
                  # V5 (ogc-mcp): con solo la imagen en el mapa, el agente dijo «32 departamentos… según el
                  # servicio» sin haber contado nada. La imagen no da cifras; dónde están, sí es un hecho.
                  "nota": ("es una IMAGEN: no da cifras ni atributos (no cuentes con ella). Para contar o leer "
                           "sus elementos, ogc_traer_elementos sobre el WFS del servicio"),
                  "wfs_del_mismo_servicio": catalogo.wfs_de(caps["url"])}
        art = raster_tiles(nombre, f"/wms/{ficha}/{{z}}/{{x}}/{{y}}.png", bounds=c.get("bbox_4326"))
        return geo_result([art], facts={k: v for k, v in hechos.items() if v is not None})

    return compact_result(runner.run(ver))


@mcp.tool(
    description=("Trae los elementos de una capa vectorial de un WFS o de una OGC API - Features como capa en "
                 "EPSG:4326, con sus atributos: `url` del servicio, `capa` = el NOMBRE que dio ogc_describir, "
                 "`tipo` wfs u ogcapi. Sin filtro trae la capa entera (lo que responde «¿cuántos hay?»). "
                 "`area` = una capa de la sesión (p. ej. el límite de un municipio): deja SOLO los elementos que "
                 "la intersecan («dentro de X»). `bbox` [minx,miny,maxx,maxy] EPSG:4326 o `viewport` filtra por un "
                 "rectángulo. `campos` = solo esos atributos (p. ej. el nombre y la categoría): con menos campos "
                 "ves más filas en el resultado; sin él, todos. "
                 f"Hasta `max_elementos` (máx. {elementos.MAX_ELEMENTOS}). Los hechos dicen cuántos "
                 "hay en el servicio, con y sin filtro, y si vino todo: no afirmes un total si no vino completa."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"bbox": ["bbox", "layer_ref"], "area": ["geometry", "layer_ref"]},
                  outputs=["feature_collection", "feature_ref"], cost="medium"),
)
def ogc_traer_elementos(url: str, capa: str, tipo: str | None = None, bbox: list[float] | None = None,
                        area: dict | None = None, campos: list[str] | None = None,
                        max_elementos: int = 5000) -> CallToolResult:
    def traer() -> dict:
        fc, hechos = elementos.traer(_url(url), capa, bbox, max_elementos, tipo, area=area, campos=campos)
        return _como_capa(fc, hechos)

    return compact_result(runner.run(traer))


def _como_capa(fc: dict, hechos: dict[str, Any]) -> dict:
    """La capa en la respuesta o, si es grande (por número o por peso), como GeoJSON por referencia."""
    nombre = str(hechos.get("capa") or "OGC")
    texto = json.dumps(fc, ensure_ascii=False, separators=(",", ":"))
    if len(fc["features"]) <= MAX_EN_LINEA and len(texto) <= MAX_BYTES_EN_LINEA:
        return geo_result([feature_collection(nombre, fc, crs="EPSG:4326")], facts=hechos)
    os.makedirs(RESULTADOS, exist_ok=True)
    _purgar()
    ident = uuid.uuid4().hex
    with open(os.path.join(RESULTADOS, f"{ident}.geojson"), "w", encoding="utf-8") as fh:
        fh.write(texto)
    return geo_result([feature_ref(nombre, f"/resultados/{ident}.geojson", fmt="geojson", crs="EPSG:4326",
                                   feature_count=len(fc["features"]))], facts=hechos)


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


async def _servir_tesela(scope, send, params, _key):
    import anyio

    spec, z, x, y = params
    try:
        png = await anyio.to_thread.run_sync(lambda: teselas.getmap(spec, z, x, y))
    except (ows.ErrorOgc, UrlNoPermitida) as exc:
        logger.warning(f"tesela WMS {z}/{x}/{y} falló: {exc}")
        await respond_json(send, 502, {"error": "tesela no disponible"})
        return
    await respond_png(send, png)


def build_app():
    keys = KeyRing.from_json(os.environ.get("OGC_MCP_KEYS", "[]"), known_scopes={"ogc:read"}, tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("ogc-mcp no arranca sin claves (OGC_MCP_KEYS)")
    catalogo.cargar()  # un catálogo roto se ve al arrancar, no en la primera pregunta
    rutas = (
        ExtraRoute(lambda p: (m.group(1) if (m := _RESULTADO_RE.match(p)) else None), _servir_resultado,
                   requires_tool="ogc_traer_elementos", weight=0.2),
        ExtraRoute(teselas.parse_ruta, _servir_tesela, requires_tool="ogc_ver_mapa", weight=0.05),
    )
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="ogc-mcp", routes=rutas)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("OGC_MCP_PORT", "9800")))
