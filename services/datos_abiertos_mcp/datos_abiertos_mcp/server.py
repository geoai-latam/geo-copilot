"""datos-abiertos-mcp — portales de datos abiertos: datos.gov.co (Socrata) y CKAN (Datos Abiertos Bogotá…).

Miles de conjuntos de alcaldías y entidades (instituciones educativas, sedes de salud, equipamientos…)
viven en datos.gov.co, no en servicios GIS. Cuatro herramientas, para que el LLM decida:

- `datos_buscar`: conjuntos que responden a un texto, con sus hechos (entidad, columnas y cuáles son de
  ubicación, fecha, y en CKAN cada recurso y con qué herramienta se trae).
- `datos_describir`: columnas con tipo, filas, licencia (Socrata) o recursos (CKAN).
- `datos_consultar`: un conjunto de Socrata filtrado EN el portal (SoQL), como capa si tiene ubicación
  (columna de ubicación o latitud/longitud) o como tabla si no; por zona (`bbox`/`area`).
- `datos_traer_recurso`: un recurso GeoJSON o CSV de un portal CKAN configurado.

`DATOS_PORTALES` (JSON) cambia los portales; por defecto datos.gov.co y Datos Abiertos Bogotá.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

from geo_mcp_kit import (
    GeoMcpAuth,
    KeyRing,
    RateLimiter,
    ToolRunner,
    compact_result,
    geo_meta,
    geo_result,
    herramientas_en_hilo,
    table,
)
from geo_mcp_kit import area as areas
from geo_mcp_kit.red import ErrorRed, UrlNoPermitida
from geo_mcp_kit.resultados import Resultados
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

from datos_abiertos_mcp import ckan, filas, socrata

logger = logging.getLogger("datos_abiertos_mcp")
SCOPES = {"datos_buscar": "datos:read", "datos_describir": "datos:read", "datos_consultar": "datos:read",
          "datos_traer_recurso": "datos:read"}
PORTALES_DEFECTO = [{"id": "datos.gov.co", "tipo": "socrata", "dominio": "www.datos.gov.co",
                     "nombre": "Portal de Datos Abiertos de Colombia"},
                    {"id": "bogota", "tipo": "ckan", "url": "https://datosabiertos.bogota.gov.co",
                     "nombre": "Datos Abiertos Bogotá"}]
resultados = Resultados(os.environ.get("DATOS_RESULTADOS_DIR")
                        or os.path.join(tempfile.gettempdir(), "datos-resultados"))
runner = ToolRunner(timeout_s=170, service="datos-abiertos-mcp",
                    expected_errors=(ErrorRed, UrlNoPermitida, areas.AreaInvalida, filas.ErrorColumnas))
mcp = FastMCP(
    "datos-abiertos-mcp",
    instructions=("Portales de datos abiertos (datos.gov.co, Datos Abiertos Bogotá): busca conjuntos con "
                  "`datos_buscar`, mira sus columnas con `datos_describir`, consulta uno de datos.gov.co con "
                  "`datos_consultar` y trae un recurso GeoJSON/CSV de CKAN con `datos_traer_recurso`."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def portales() -> list[dict[str, Any]]:
    crudo = os.environ.get("DATOS_PORTALES", "").strip()
    return json.loads(crudo) if crudo else PORTALES_DEFECTO


# V5: el agente pasaba siempre `portal: datos.gov.co` aunque le pidieran el de Bogotá: no sabía su id.
PORTALES_TXT = "; ".join(f"«{p['id']}» = {p.get('nombre') or p['id']} ({p['tipo']})" for p in portales())


def _portal(ident: str | None, tipo: str | None = None) -> dict[str, Any]:
    disponibles = [p for p in portales() if tipo is None or p["tipo"] == tipo]
    if ident is None and len(disponibles) == 1:
        return disponibles[0]
    p = next((p for p in disponibles if p["id"] == ident), None)
    if p is None:
        nombres = ", ".join(f"{p['id']} ({p['tipo']})" for p in disponibles)
        raise ErrorRed(f"portal «{ident}» no configurado{' para ' + tipo if tipo else ''}; los que hay: {nombres}")
    return p


@mcp.tool(
    description=("Busca conjuntos de datos abiertos por texto en los portales configurados (datos.gov.co: "
                 "alcaldías y entidades nacionales; Datos Abiertos Bogotá). Cada resultado trae su entidad, "
                 "descripción, fecha, columnas (y cuáles son de ubicación) o, en CKAN, sus recursos y con qué "
                 f"herramienta se trae cada uno. `portal` acota a uno ({PORTALES_TXT}); sin él, todos. Muchos "
                 "conjuntos son de UN municipio: mira la entidad antes de elegir."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=[], cost="low"),
)
def datos_buscar(texto: str, portal: str | None = None, limite: int = 10) -> CallToolResult:
    def buscar() -> dict:
        if not (texto or "").strip():
            raise ErrorRed("falta el texto a buscar")
        hechos: dict[str, Any] = {"buscado": texto, "portales": []}
        if portal:
            otros = [p["id"] for p in portales() if p["id"] != portal]
            hechos["portales_no_consultados"] = otros  # V5: buscaba solo en datos.gov.co y concluía «no hay»
        for p in [_portal(portal)] if portal else portales():
            try:
                hallados = (socrata.buscar(texto, p["dominio"], limite) if p["tipo"] == "socrata"
                            else ckan.buscar(p["url"], texto, limite))
                hechos["portales"].append({"portal": p["id"], "nombre": p.get("nombre"), "conjuntos": hallados})
            except ErrorRed as exc:  # un portal caído no tumba la búsqueda en los demás
                hechos["portales"].append({"portal": p["id"], "error": str(exc)})
        return geo_result([], facts=hechos)

    return compact_result(runner.run(buscar))


@mcp.tool(
    description=(f"Describe un conjunto por su `id` (de datos_buscar) en su `portal` ({PORTALES_TXT}): en "
                 "datos.gov.co, sus columnas con tipo, cuántas filas tiene, cuáles son de ubicación, su "
                 "licencia y los VALORES EXACTOS de las columnas de texto con pocos valores (úsalos tal cual en "
                 "`where`); en CKAN, sus recursos (formato, URL y con qué herramienta se trae cada uno)."
                 ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=[], cost="low"),
)
def datos_describir(id: str, portal: str = "datos.gov.co") -> CallToolResult:
    def describir() -> dict:
        p = _portal(portal)
        hechos = socrata.describir(p["dominio"], id) if p["tipo"] == "socrata" else ckan.describir(p["url"], id)
        return geo_result([], facts={"portal": p["id"], **hechos})

    return compact_result(runner.run(describir))


@mcp.tool(
    description=("Consulta un conjunto de datos.gov.co (Socrata) filtrando EN el portal con SoQL: `select` "
                 "(columnas), `where` (p. ej. «municipio = 'ENVIGADO' AND total_estudiantes::number > 500»), "
                 "`order`, y para agregados `group` y `having` (p. ej. select «departamento, count(*) AS n», "
                 "group «departamento», having «count(*) >= 10»: con datos de personas, deja fuera los grupos "
                 "pequeños). Si la fuente repite registros, `previo_select` + `previo_group` agrupan antes "
                 "(p. ej. por estudiante) y el agregado se calcula sobre esos grupos. Si tiene ubicación, "
                 "sale como CAPA: `columna_geo` (una columna de ubicación) o "
                 "`lat` + `lon` (columnas de latitud y longitud, aunque sean texto); si no, como TABLA. Por "
                 "zona: `bbox` [minx,miny,maxx,maxy] o `area` (una capa de la sesión: solo lo que la "
                 "interseca). Hasta `max_filas` (máx. 50000). Los hechos dicen cuántas cumplen el filtro en el "
                 "portal y si vino todo: no afirmes un total si no vino completo."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"bbox": ["bbox", "layer_ref"], "area": ["geometry", "layer_ref"]},
                  outputs=["feature_collection", "feature_ref", "table"], cost="medium"),
)
def datos_consultar(id: str, portal: str = "datos.gov.co", select: str | None = None,
                    where: str | None = None, order: str | None = None, columna_geo: str | None = None,
                    lat: str | None = None, lon: str | None = None, bbox: list[float] | None = None,
                    area: dict | None = None, max_filas: int = 5000, group: str | None = None,
                    having: str | None = None, previo_select: str | None = None,
                    previo_group: str | None = None) -> CallToolResult:
    def consultar() -> dict:
        p = _portal(portal, "socrata")
        geom = areas.geometria(area) if area else None
        zona = list(geom.bounds) if geom is not None else bbox
        filtro = " AND ".join(f"({w})" for w in (where, zona and socrata.filtro_bbox(
            zona, columna_geo, lat, lon)) if w) or None
        datos, total = socrata.consultar(p["dominio"], id, select=select, where=filtro, order=order,
                                         maximo=max_filas, geojson=bool(columna_geo), group=group, having=having,
                                         previo_select=previo_select, previo_group=previo_group)
        hechos = filas.hechos(id, p, datos, total, max_filas, filtro, select=select, group=group, having=having,
                              previo_group=previo_group)
        if not (columna_geo or (lat and lon)):
            return geo_result([table(sorted({k for f in datos for k in f}), datos)], facts=hechos)
        fc = ({"type": "FeatureCollection", "features": datos} if columna_geo
              else filas.puntos(datos, lat or "", lon or "", hechos))
        if geom is not None:
            hechos["filtro_area"] = areas.recortar(fc, geom)
            hechos["elementos"] = len(fc["features"])
        return resultados.capa(str(hechos.get("conjunto") or id), fc, hechos)

    return compact_result(runner.run(consultar))


@mcp.tool(
    description=("Trae un recurso GeoJSON o CSV de un portal CKAN configurado (su `url`, de datos_buscar o "
                 "datos_describir). Un CSV con `lat` + `lon` sale como capa de puntos; sin ellas, como tabla. "
                 "Los recursos WFS/WMS/OGC o Esri REST no se traen aquí: los hechos de búsqueda dicen con qué "
                 "herramienta van."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["feature_collection", "feature_ref", "table"], cost="medium"),
)
def datos_traer_recurso(url: str, lat: str | None = None, lon: str | None = None,
                        nombre: str | None = None) -> CallToolResult:
    def traer() -> dict:
        dominios = sorted({(p.get("url") or "").split("/")[2] for p in portales() if p["tipo"] == "ckan"})
        tipo, datos = ckan.descargar(url, dominios)
        hechos: dict[str, Any] = {"recurso": url, "formato": tipo}
        if tipo == "geojson":
            hechos["elementos"] = len(datos["features"])
            hechos["crs_declarado"] = crs = ckan.crs_de_geojson(datos)
            if crs != "EPSG:4326":
                # V5: con solo «declara EPSG:3857» el agente dudaba de la ubicación («conviene revisar»)
                hechos["reproyeccion"] = f"de {crs} a EPSG:4326 al cargarla: queda bien ubicada en el mapa"
            return resultados.capa(nombre or "Recurso de datos abiertos", datos, hechos, crs=crs)
        hechos["filas"] = len(datos)
        if lat and lon:
            fc = filas.puntos(datos, lat, lon, hechos)
            return resultados.capa(nombre or "Recurso de datos abiertos", fc, hechos)
        return geo_result([table(list(datos[0]) if datos else [], datos[:5000])], facts=hechos)

    return compact_result(runner.run(traer))


def build_app():
    keys = KeyRing.from_json(os.environ.get("DATOS_MCP_KEYS", "[]"), known_scopes={"datos:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("datos-abiertos-mcp no arranca sin claves (DATOS_MCP_KEYS)")
    portales()  # un DATOS_PORTALES roto se ve al arrancar
    rutas = (resultados.ruta("datos_consultar"),)
    return GeoMcpAuth(herramientas_en_hilo(mcp).streamable_http_app(), keys, RateLimiter(), service="datos-abiertos-mcp", routes=rutas)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("DATOS_MCP_PORT", "9900")))
