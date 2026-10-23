"""web-mcp — APIs web públicas de entidades del Estado, con buenas maneras (Terranova F5, pieza G4).

Para las fuentes que no están en datos.gov.co ni en ArcGIS: la relatoría de Colombia Compra (API REST de
WordPress) y las series del Banco de la República (SDMX). Dos herramientas:

- `web_json`: una API JSON; la lista de elementos (en la raíz o en `filas_en`) sale como tabla, con el
  total que dé la fuente (`X-WP-Total`).
- `web_sdmx`: un mensaje SDMX-ML 2.1; una fila por observación, con las dimensiones de su serie.

Solo dominios de `WEB_MCP_DOMINIOS`; respeta `robots.txt`; una petición por segundo por sitio.
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
    completitud,
    geo_meta,
    geo_result,
    herramientas_en_hilo,
    table,
)
from geo_mcp_kit.red import ErrorRed, UrlNoPermitida
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

from web_mcp import fuentes, paginas, tablas

logger = logging.getLogger("web_mcp")
SCOPES = {"web_json": "web:read", "web_sdmx": "web:read", "web_html": "web:read", "web_tabla": "web:read"}
runner = ToolRunner(timeout_s=170, service="web-mcp", expected_errors=(ErrorRed, UrlNoPermitida, fuentes.ErrorWeb))
mcp = FastMCP(
    "web-mcp",
    instructions=("APIs web públicas de entidades del Estado (relatoría de Colombia Compra, series del Banco de "
                  "la República) en una lista de dominios permitidos."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _tabla(filas: list[dict[str, Any]], hechos: dict[str, Any]) -> dict[str, Any]:
    return geo_result([table(sorted({k for f in filas for k in f}), filas)], facts=hechos)


@mcp.tool(
    description=("Lee una API JSON pública (`url` https de un dominio permitido, `params` de la consulta). La lista "
                 "de elementos (en la raíz o en `filas_en`, p. ej. `data.items`) sale como tabla; los objetos "
                 "anidados se aplanan (`title.rendered`). Los hechos dicen cuántos hay si la fuente lo informa "
                 "(`X-WP-Total`)."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["table"], cost="low"),
)
def web_json(url: str, params: dict | None = None, filas_en: str | None = None) -> CallToolResult:
    def leer() -> dict:
        r = fuentes.pedir(url, params)
        try:
            datos = r.json()
        except ValueError as exc:
            raise fuentes.ErrorWeb("la respuesta no es JSON", "fuente_respuesta_invalida") from exc
        filas = fuentes.filas_json(datos, filas_en)
        total = fuentes.total_de_cabeceras(r.headers)
        hechos = {"url": url, "params": params or {}, "filas": len(filas),
                  "completitud": completitud(len(filas), total if total is not None else None,
                                             truncado=None if total is not None else False)}
        return _tabla(filas, hechos)

    return compact_result(runner.run(leer))


@mcp.tool(
    description=("Lee datos SDMX-ML 2.1 (`url` https de un dominio permitido, `params` como `startPeriod`, "
                 "`endPeriod`, `lastNObservations`): una fila por observación con `TIME_PERIOD`, `OBS_VALUE` y "
                 "las dimensiones de su serie."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["table"], cost="low"),
)
def web_sdmx(url: str, params: dict | None = None) -> CallToolResult:
    def leer() -> dict:
        r = fuentes.pedir(url, params)
        filas = fuentes.filas_sdmx(r.text)
        cortado = len(filas) >= fuentes.MAX_FILAS
        hechos = {"url": url, "params": params or {}, "filas": len(filas),
                  "completitud": completitud(len(filas), None if cortado else len(filas), truncado=cortado,
                                             motivo=f"tope de {fuentes.MAX_FILAS} observaciones" if cortado else None)}
        return _tabla(filas, hechos)

    return compact_result(runner.run(leer))


MAX_TEXTO = 200_000


@mcp.tool(
    description=("Lee una página HTML pública (`url` https de un dominio permitido): su título y su texto limpio, "
                 "sin menús ni scripts. Para una norma o una sentencia larga, `buscar` (p. ej. «ARTÍCULO 5») "
                 "devuelve solo los fragmentos alrededor de cada aparición; `enlaces_con` (expresión regular "
                 "sobre la URL) lista los enlaces de un índice. Los hechos dicen cuántos caracteres tiene la "
                 "página y si el texto vino entero."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["table"], cost="low"),
)
def web_html(url: str, params: dict | None = None, buscar: str | None = None, enlaces_con: str | None = None,
             max_caracteres: int = 20_000) -> CallToolResult:
    def leer() -> dict:
        r = fuentes.pedir(url, params)
        if "html" not in r.headers.get("content-type", "html").lower():
            raise fuentes.ErrorWeb("la respuesta no es HTML", "fuente_respuesta_invalida")
        # la base de los enlaces es la URL pedida, no `r.url`: el cliente del kit fija la IP validada y
        # `r.url` sale con la IP; un enlace relativo armado sobre ella ya no es del dominio permitido
        final = url
        contenido = r.content if isinstance(getattr(r, "content", None), bytes) else r.text.encode("utf-8")
        html = paginas.decodificar(contenido, r.headers.get("content-type", ""))
        titulo, texto, enlaces = paginas.leer(html, final)
        tope = max(1, min(int(max_caracteres), MAX_TEXTO))
        hechos: dict[str, Any] = {"url": final, "titulo": titulo, "caracteres": len(texto)}
        if buscar:
            filas, total = paginas.fragmentos(texto, buscar)
            hechos.update({"buscar": buscar, "apariciones": total,
                           "completitud": completitud(len(filas), total, truncado=len(filas) < total)})
        elif enlaces_con:
            filas = paginas.enlaces_con(enlaces, enlaces_con)
            hechos.update({"enlaces": len(filas), "completitud": completitud(len(filas), len(filas))})
        else:
            filas = [{"titulo": titulo, "texto": texto[:tope]}]
            hechos["completitud"] = completitud(min(len(texto), tope), len(texto), truncado=len(texto) > tope,
                                                motivo=f"max_caracteres={tope}" if len(texto) > tope else None)
        return _tabla(filas, hechos)

    return compact_result(runner.run(leer))


@mcp.tool(
    description=("Lee una tabla de un archivo publicado (.xlsx o .csv): `url` directa, o `pagina` + `enlace_con` "
                 "(expresión regular) para encontrar el archivo vigente en su página índice. `hoja`, "
                 "`fila_encabezado` (1 = primera), `rellenar` (las N primeras columnas combinadas se rellenan "
                 "hacia abajo), `filas_encabezado` (2 si el año va arriba combinado y debajo los meses o las "
                 "zonas), `largo_desde` (posición desde la que las columnas pasan a filas `columna`/`valor`: "
                 "un año por columna → un año por fila) y `filtros` {columna: valor}. Los hechos dicen el archivo, "
                 "las hojas y cuántas filas cumplen."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["table"], cost="medium"),
)
def web_tabla(url: str | None = None, pagina: str | None = None, enlace_con: str | None = None,
              hoja: str | None = None, fila_encabezado: int = 1, rellenar: int = 0, largo_desde: int | None = None,
              filtros: dict | None = None, max_filas: int = 2000, filas_encabezado: int = 1) -> CallToolResult:
    def leer() -> dict:
        archivo = url
        if archivo is None:
            if not (pagina and enlace_con):
                raise fuentes.ErrorWeb("hace falta `url`, o `pagina` y `enlace_con`")
            indice = fuentes.pedir(pagina, None)
            final = pagina  # no `indice.url`: trae la IP fijada por el kit (ver web_html)
            _, _, enlaces = paginas.leer(paginas.decodificar(indice.content, indice.headers.get("content-type", "")), final)
            encontrados = paginas.enlaces_con(enlaces, enlace_con)
            if not encontrados:
                raise fuentes.ErrorWeb(f"la página no enlaza ningún archivo que cumpla {enlace_con!r}",
                                       "fuente_respuesta_invalida")
            archivo = encontrados[0]["url"]
        r = fuentes.pedir(archivo, None)
        try:
            crudas, hojas = tablas.filas_crudas(r.content, archivo, hoja)
            registros = tablas.filtrar(tablas.tabla(crudas, fila_encabezado, rellenar, largo_desde, filas_encabezado),
                                       filtros)
        except ValueError as exc:
            raise fuentes.ErrorWeb(str(exc)) from exc
        tope = max(1, min(int(max_filas), tablas.MAX_FILAS))
        filas = registros[:tope]
        hechos = {"archivo": archivo, "hoja": hoja, "hojas": hojas, "filas": len(filas),
                  "completitud": completitud(len(filas), len(registros))}
        return geo_result([table(list(dict.fromkeys(k for f in filas for k in f)), filas)], facts=hechos)

    return compact_result(runner.run(leer))


def build_app():
    keys = KeyRing.from_json(os.environ.get("WEB_MCP_KEYS", "[]"), known_scopes={"web:read"}, tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("web-mcp no arranca sin claves (WEB_MCP_KEYS)")
    if not fuentes.dominios():
        raise RuntimeError("web-mcp no arranca sin dominios permitidos (WEB_MCP_DOMINIOS)")
    return GeoMcpAuth(herramientas_en_hilo(mcp).streamable_http_app(), keys, RateLimiter(), service="web-mcp")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("WEB_MCP_PORT", "9940")))
