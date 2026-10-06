"""archivos-mcp — archivos geoespaciales (GeoParquet, FlatGeobuf, CSV…) con DuckDB-spatial (F5, T5.6).

DuckDB es la FUENTE, no el almacén (decisión D1): los archivos (locales o en S3/Azure/HTTP) se
leen donde están y el filtro se hace AQUÍ, sobre el archivo —atributos (`where`), área (`aoi`,
una geometría: el núcleo pone la del dibujo o capa que el usuario nombró) y columnas—, así que
al núcleo solo viaja el resultado:

- hasta 5000 elementos → `feature_collection` (EPSG:4326);
- más → `feature_ref`: un GeoParquet que sirve este mismo servidor en `/resultados/…` (con la
  misma clave); el núcleo lo descarga y lo materializa en el workspace.

Seguridad: las fuentes las fija la configuración (`ARCHIVOS_SOURCES`); el LLM no da rutas. El
`where` pasa por el MISMO validador SQL del núcleo (`geo_sql_guard`) contra una única tabla: sin
subconsultas a otra cosa, sin funciones de lectura de archivos ni de sistema.
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
from dataclasses import dataclass
from typing import Any

import duckdb
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
    stats,
)
from geo_sql_guard import analizar
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

logger = logging.getLogger("archivos_mcp")
SCOPES = {"archivos_list": "archivos:read", "archivos_query": "archivos:read",
          "archivos_estadisticas": "archivos:read"}
MAX_EN_LINEA = 5000          # hasta aquí, capa en la respuesta; más, GeoParquet por referencia
MAX_ELEMENTOS = 200_000
TTL_RESULTADOS_S = 3600
FORMATOS = ("geoparquet", "parquet", "flatgeobuf", "geojson", "csv")


class ErrorArchivos(Exception):
    """Un error que el LLM puede corregir (fuente, columna o filtro inválidos…)."""


@dataclass
class Fuente:
    id: str
    descripcion: str
    uri: str
    formato: str
    crs: str = "EPSG:4326"
    lon: str | None = None        # CSV con coordenadas
    lat: str | None = None


def cargar_fuentes(raw: str | None = None) -> dict[str, Fuente]:
    """`ARCHIVOS_SOURCES` = [{id, descripcion, uri, formato, crs?, lon?, lat?}]."""
    fuentes: dict[str, Fuente] = {}
    for f in json.loads(raw if raw is not None else os.environ.get("ARCHIVOS_SOURCES", "[]")):
        formato = str(f.get("formato") or "").lower()
        if formato not in FORMATOS:
            raise ValueError(f"fuente {f.get('id')!r}: formato {formato!r} no soportado ({', '.join(FORMATOS)})")
        if formato == "csv" and not (f.get("lon") and f.get("lat")):
            raise ValueError(f"fuente {f.get('id')!r}: un CSV necesita las columnas `lon` y `lat`")
        fuentes[str(f["id"])] = Fuente(str(f["id"]), str(f.get("descripcion") or ""), str(f["uri"]), formato,
                                       str(f.get("crs") or "EPSG:4326"), f.get("lon"), f.get("lat"))
    return fuentes


FUENTES: dict[str, Fuente] = {}
RESULTADOS = os.environ.get("ARCHIVOS_RESULTADOS_DIR") or os.path.join(tempfile.gettempdir(), "archivos-resultados")
_EXT_DIR = os.environ.get("DUCKDB_EXTENSION_DIR")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

runner = ToolRunner(timeout_s=120, service="archivos-mcp", expected_errors=(ErrorArchivos,))
mcp = FastMCP(
    "archivos-mcp",
    instructions=("Archivos geoespaciales (GeoParquet, FlatGeobuf, CSV…) leídos donde están con DuckDB. Mira "
                  "las fuentes con `archivos_list` y trae lo que necesitas con `archivos_query`, filtrando AQUÍ "
                  "(where, aoi, columnas): solo viaja el resultado."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _conectar() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    if _EXT_DIR:
        con.execute(f"SET extension_directory = '{_EXT_DIR}'")
    con.execute("LOAD spatial")
    return con


def _literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _relacion(f: Fuente) -> str:
    """SQL que lee la fuente; la geometría queda en la columna `geom`."""
    u = _literal(f.uri)
    if f.formato in ("geoparquet", "parquet"):
        return f"SELECT * FROM read_parquet({u})"
    if f.formato == "csv":
        return f'SELECT *, ST_Point("{f.lon}", "{f.lat}") AS geom FROM read_csv_auto({u})'
    return f"SELECT * FROM ST_Read({u})"   # flatgeobuf, geojson (GDAL)


def _fuente(source: str) -> Fuente:
    f = FUENTES.get(source)
    if f is None:
        raise ErrorArchivos(f"no hay una fuente «{source}»; las que hay: {', '.join(FUENTES) or 'ninguna'}")
    return f


def _columnas(con, f: Fuente) -> tuple[list[tuple[str, str]], str | None]:
    """(columnas, columna de geometría): la geometría es la que el propio archivo tipa como GEOMETRY."""
    filas = con.execute(f"DESCRIBE {_relacion(f)}").fetchall()
    cols = [(str(r[0]), str(r[1])) for r in filas]
    geo = next((n for n, t in cols if t.upper().startswith("GEOMETRY")), None)
    return cols, geo


def _geom_4326(geo: str, f: Fuente) -> str:
    g = f'"{geo}"'
    if f.crs.upper() in ("EPSG:4326", "OGC:CRS84"):
        return g
    return f"ST_Transform({g}, {_literal(f.crs)}, 'EPSG:4326', always_xy := true)"


def _listar() -> dict:
    fuentes = []
    with _conectar() as con:
        for f in FUENTES.values():
            try:
                cols, geo = _columnas(con, f)
                rel = _relacion(f)
                n = con.execute(f"SELECT count(*) FROM ({rel})").fetchone()[0]
                ext = None
                if geo:
                    e = con.execute(f"SELECT ST_XMin(x), ST_YMin(x), ST_XMax(x), ST_YMax(x) FROM "
                                    f"(SELECT ST_Extent_Agg({_geom_4326(geo, f)}) AS x FROM ({rel}))").fetchone()
                    ext = [round(float(v), 6) for v in e] if e and e[0] is not None else None
                fuentes.append({"id": f.id, "descripcion": f.descripcion, "formato": f.formato, "elementos": n,
                                "geometria": geo, "extent_4326": ext,
                                "columnas": [{"columna": c, "tipo": t} for c, t in cols if c != geo][:60]})
            except duckdb.Error as exc:
                fuentes.append({"id": f.id, "descripcion": f.descripcion,
                                "error": f"no se pudo leer: {str(exc).splitlines()[0][:200]}"})
    return geo_result([], facts={"fuentes": fuentes})


@mcp.tool(description=("Las fuentes de archivos disponibles: qué contienen, formato, cuántos elementos, su "
                       "columna de geometría, su extensión (EPSG:4326) y sus columnas con tipo."),
          annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def archivos_list() -> CallToolResult:
    return compact_result(runner.run(_listar))


def _validar_where(where: str, columnas: set[str]) -> str:
    texto = where.strip().rstrip(";").strip()
    veredicto = analizar(f"SELECT * FROM t WHERE {texto}", {"public.t"})
    if not veredicto.aceptada:
        motivos = "; ".join(veredicto["motivos"])
        if "no disponible en el catálogo" in motivos:
            # F3 (verdades ×5): «lotes > 1000 m² en Chapinero» metía el límite en el `where`
            # (`ST_Intersects(geom, (SELECT geom FROM ds_…))`) y, rechazado, daba el archivo entero
            motivos += (". El `where` solo ve las columnas de la fuente: una capa del mapa o del workspace "
                        "(p. ej. el límite de un lugar) NO es una tabla; para filtrar por su área, pásala en `aoi`")
        raise ErrorArchivos(f"filtro `where` rechazado: {motivos}. Columnas: {', '.join(sorted(columnas))}")
    return texto


REPITE = (". Si una de ellas es sin duda la que quisiste (p. ej. `area_m2` por `area`), repite la llamada "
          "con ella tú mismo; no le preguntes al usuario. Si filtrabas por un LUGAR (localidad, barrio, "
          "municipio) que no es una columna, pasa su límite como capa en `aoi` (geocodifícalo antes)")


def _error_duckdb(exc: Exception, nombres: set[str], geo: str | None) -> str:
    """El error de DuckDB para el LLM; si es una columna que no existe, con las que SÍ hay (como en sql)."""
    texto = f"DuckDB: {str(exc).splitlines()[0][:300]}"
    if "column" in str(exc).lower() and ("not found" in str(exc).lower() or "does not exist" in str(exc).lower()):
        # gpt-5.4 (2026-10-05): «el campo se llama area_m2, no area… ¿lo corro?» en vez de repetir
        texto += f". Columnas de la fuente: {', '.join(sorted(nombres - {geo}))}" + REPITE
    return texto


def _base_filtrada(f: Any, geo: str, nombres: set[str], where: str | None, aoi: dict | None) -> tuple[str, list]:
    """El SELECT de la fuente con el filtro aplicado en el origen (atributos y/o área) y sus parámetros."""
    condiciones, params = [], []
    if where and where.strip():
        condiciones.append(f"({_validar_where(where, nombres)})")
    if aoi:
        geom_aoi = aoi.get("geometry") if aoi.get("type") == "Feature" else aoi
        if aoi.get("type") == "FeatureCollection":
            geoms = [x.get("geometry") for x in aoi.get("features") or [] if x.get("geometry")]
            if not geoms:
                raise ErrorArchivos("el área (aoi) no tiene geometría")
            geom_aoi = {"type": "GeometryCollection", "geometries": geoms}
        condiciones.append(f"ST_Intersects({_geom_4326(geo, f)}, ST_GeomFromGeoJSON(?))")
        params.append(json.dumps(geom_aoi))
    filtro = " AND ".join(condiciones) or "TRUE"
    return f"SELECT * FROM ({_relacion(f)}) AS t WHERE {filtro}", params


_NUMERICOS = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE", "REAL", "DECIMAL")


def _estadisticas(source: str, where: str | None, aoi: dict | None, campos: list[str] | None) -> dict:
    """Cuántos cumplen y, por campo, su resumen — calculado sobre TODAS las filas que cumplen, en el origen.

    V5 (archivos): «¿cuántos lotes hay y cuál es su área promedio?» → el agente trajo 10 lotes y dio
    212,38 m² como el promedio de 22.387 (el real: 882,73). Una cifra del conjunto se calcula aquí.
    """
    f = _fuente(source)
    with _conectar() as con:
        cols, geo = _columnas(con, f)
        tipos = dict(cols)
        nombres = set(tipos)
        pedidos = [c for c in (campos or []) if c not in (geo, "*")]
        malas = [c for c in pedidos if c not in nombres or not _IDENT.match(c)]
        if malas:
            raise ErrorArchivos(f"campos que no existen: {malas}; los que hay: {', '.join(sorted(nombres - {geo}))}" + REPITE)
        elegidos = pedidos or [c for c in tipos if c != geo][:20]
        base, params = _base_filtrada(f, geo, nombres, where, aoi)
        try:
            total = con.execute(f"SELECT count(*) FROM ({base})", params).fetchone()[0]
            items: list[dict] = [{"label": "elementos", "value": int(total)}]
            hechos: dict[str, Any] = {"fuente": f.id, "elementos": int(total), "campos": {},
                                      "calculado_sobre": "TODAS las filas que cumplen el filtro, en el origen"}
            for c in elegidos:
                tipo = str(tipos.get(c) or "").upper()
                if any(tipo.startswith(t) for t in _NUMERICOS):
                    r = con.execute(f'SELECT min("{c}"), max("{c}"), avg("{c}"), sum("{c}"), median("{c}"), '
                                    f'count("{c}") FROM ({base})', params).fetchone()
                    resumen = {k: (round(float(v), 4) if v is not None else None)
                               for k, v in zip(("min", "max", "media", "suma", "mediana"), r[:5], strict=False)}
                    resumen["con_valor"] = int(r[5])
                    for k in ("media", "suma", "mediana"):
                        items.append({"label": f"{c} · {k}", "value": resumen[k]})
                else:
                    filas = con.execute(f'SELECT "{c}"::VARCHAR, count(*) FROM ({base}) GROUP BY 1 ORDER BY 2 DESC '
                                        f'LIMIT 11', params).fetchall()
                    resumen = {"mas_frecuentes": [[v, int(n)] for v, n in filas[:10]],
                               **({"hay_mas_valores": True} if len(filas) > 10 else {})}
                hechos["campos"][c] = resumen
        except duckdb.Error as exc:
            raise ErrorArchivos(_error_duckdb(exc, nombres, geo)) from exc
    if where and GEOMETRIA_ESCRITA.search(where):
        hechos["aviso_area"] = ("el filtro lleva una geometría escrita en el texto (coordenadas). Si representa un "
                                "lugar, NO es su límite: dilo así, o pasa el límite real como capa en `aoi`")
    return geo_result([stats(items, name=f"{f.id} · estadísticas")], facts=hechos)


def _consultar(source: str, where: str | None, aoi: dict | None, columns: list[str] | None,
               max_features: int) -> dict:
    f = _fuente(source)
    tope = max(1, min(int(max_features), MAX_ELEMENTOS))
    with _conectar() as con:
        cols, geo = _columnas(con, f)
        if not geo:
            raise ErrorArchivos(f"la fuente «{f.id}» no tiene geometría")
        nombres = {c for c, _ in cols}
        # V5 (archivos): `columns=['*']` (la forma SQL de «todas») se rechazaba dos veces seguidas y el LLM,
        # al reintentar, soltaba el área (aoi): el resultado ya no era el del lugar pedido.
        pedidas = [c for c in (columns or []) if c not in (geo, "*")]
        malas = [c for c in pedidas if c not in nombres or not _IDENT.match(c)]
        if malas:
            raise ErrorArchivos(f"columnas que no existen: {malas}; las que hay: {', '.join(sorted(nombres - {geo}))}" + REPITE)
        props = pedidas or [c for c, _ in cols if c != geo]
        base, params = _base_filtrada(f, geo, nombres, where, aoi)
        try:
            total = con.execute(f"SELECT count(*) FROM ({base})", params).fetchone()[0]
            campos = ", ".join(f'"{c}"' for c in props)
            sel = f"SELECT {campos}{', ' if campos else ''}{_geom_4326(geo, f)} AS geom FROM ({base}) LIMIT {tope}"
            hechos: dict[str, Any] = {
                "fuente": f.id, "total_que_cumplen": total, "traidos": min(total, tope), "completo": total <= tope,
                # V5: con la fuente «predios_chapinero» el agente narró «1217 lotes en Chapinero» sin filtrar
                # por el área: el archivo trae Chapinero Y Teusaquillo. Lo que cubre la fuente va con el resultado.
                "que_cubre_la_fuente": f.descripcion or f.id,
                **({} if aoi else {"sin_filtro_de_area": "no se filtró por un área (aoi): el resultado cubre todo lo "
                                                         "que la fuente cubre, no un lugar dentro de ella"}),
                "filtrado_en_origen": "el filtro (where/aoi) y las columnas se aplicaron en DuckDB sobre el "
                                      "archivo: solo viajó el resultado",
            }
            if where and GEOMETRIA_ESCRITA.search(where):
                # V5 (archivos): «los lotes del barrio Chicó» con `where=geom && ST_MakeEnvelope(...)`: 3530 lotes de
                # un RECTÁNGULO narrados como los del barrio. El hecho va con el resultado.
                hechos["aviso_area"] = ("el filtro lleva una geometría escrita en el texto (coordenadas: un rectángulo o un polígono). Si "
                           "representa un lugar, NO es su límite: dilo así, o pasa el límite real como capa en `aoi`")
            if total > tope:
                # V5: el agente pidió max_features=1000 y lo narró como «límite técnico»: el tope fue suyo
                hechos["aviso"] = (f"es una MUESTRA: {tope} de {total} porque esta llamada pidió max_features={tope} "
                                   f"(no es un límite del servidor: admite hasta {MAX_ELEMENTOS}); para todos, "
                                   "repite sin max_features")
            if min(total, tope) <= MAX_EN_LINEA:
                filas = con.execute(f"SELECT * EXCLUDE (geom), ST_AsGeoJSON(geom) AS __g FROM ({sel})", params).fetchall()
                nombres_sal = [d[0] for d in con.description][:-1]
                feats = [{"type": "Feature", "geometry": json.loads(r[-1]) if r[-1] else None,
                          "properties": {n: _valor(v) for n, v in zip(nombres_sal, r[:-1], strict=False)}} for r in filas]
                return geo_result([feature_collection(f.descripcion or f.id, {"type": "FeatureCollection",
                                                                                "features": feats}, crs="EPSG:4326")],
                                  facts=hechos)
            os.makedirs(RESULTADOS, exist_ok=True)
            _purgar()
            ident = uuid.uuid4().hex
            ruta = os.path.join(RESULTADOS, f"{ident}.parquet")
            con.execute(f"COPY ({sel}) TO {_literal(ruta)} (FORMAT PARQUET)", params)
            hechos["formato"] = "geoparquet"
            return geo_result([feature_ref(f.descripcion or f.id, f"/resultados/{ident}.parquet", fmt="geoparquet",
                                           crs="EPSG:4326", feature_count=min(total, tope))], facts=hechos)
        except duckdb.Error as exc:
            raise ErrorArchivos(_error_duckdb(exc, nombres, geo)) from exc


def _valor(v: Any) -> Any:
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


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


@mcp.tool(
    description=("Trae elementos de una fuente de archivos filtrando EN EL ORIGEN: `where` (SQL sobre sus "
                 "columnas, p. ej. \"area > 100 AND uso = 'residencial'\"), `aoi` (un área: solo lo que la "
                 "interseca) y `columns` (qué atributos traer). `max_features`: por defecto 50000 (máx. 200000); "
                 "si piden «todos», no lo bajes: los resultados grandes viajan como archivo, no saturan. Los "
                 "hechos dicen cuántos cumplen y si vino todo. El resultado queda como capa del workspace."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    meta=geo_meta(inputs={"aoi": ["geometry", "layer_ref"]}, outputs=["feature_collection", "feature_ref"],
                  cost="medium", geometry_types={"aoi": ["Polygon", "MultiPolygon"]}),
)
def archivos_query(source: str, where: str | None = None, aoi: dict | None = None,
                   columns: list[str] | None = None, max_features: int = 50_000) -> CallToolResult:
    return compact_result(runner.run(_consultar, source, where, aoi, columns, max_features))


#: Una geometría escrita en el texto del filtro (no una capa): ST_MakeEnvelope, WKT, GeoJSON literal.
GEOMETRIA_ESCRITA = re.compile(r"ST_MakeEnvelope|ST_GeomFromText|ST_GeomFromGeoJSON|POLYGON\s*\(|ST_Point\s*\(",
                               re.IGNORECASE)

@mcp.tool(
    description=("Cuántos elementos de una fuente cumplen un filtro y el resumen de sus campos (numéricos: mínimo, "
                 "máximo, media, suma, mediana; de texto: los valores más frecuentes), calculado EN EL ORIGEN sobre "
                 "TODAS las filas que cumplen — sin traerlas. Para «¿cuántos…?», «el promedio de…», «el total de…» "
                 "usa esto, no una muestra traída con archivos_query. Filtros como archivos_query: `where` y `aoi`."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    meta=geo_meta(inputs={"aoi": ["geometry", "layer_ref"]}, outputs=["stats"], cost="low",
                  geometry_types={"aoi": ["Polygon", "MultiPolygon"]}),
)
def archivos_estadisticas(source: str, where: str | None = None, aoi: dict | None = None,
                          campos: list[str] | None = None) -> CallToolResult:
    return compact_result(runner.run(_estadisticas, source, where, aoi, campos))


_RESULTADO_RE = re.compile(r"^/resultados/([0-9a-f]{32})\.parquet$")


async def _servir_resultado(scope, send, params, _key):
    ruta = os.path.join(RESULTADOS, f"{params}.parquet")
    if not os.path.isfile(ruta):
        await respond_json(send, 404, {"error": "resultado no encontrado o vencido; repite la consulta"})
        return
    with open(ruta, "rb") as fh:
        datos = fh.read()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/vnd.apache.parquet"),
                            (b"content-length", str(len(datos)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": datos})


def build_app():
    FUENTES.clear()
    FUENTES.update(cargar_fuentes())
    if not FUENTES:
        raise RuntimeError("archivos-mcp no arranca sin fuentes (ARCHIVOS_SOURCES)")
    keys = KeyRing.from_json(os.environ.get("ARCHIVOS_MCP_KEYS", "[]"), known_scopes={"archivos:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("archivos-mcp no arranca sin claves (ARCHIVOS_MCP_KEYS)")
    ruta = ExtraRoute(lambda p: (m.group(1) if (m := _RESULTADO_RE.match(p)) else None), _servir_resultado,
                      requires_tool="archivos_query", weight=0.2)
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="archivos-mcp", routes=(ruta,))


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("ARCHIVOS_MCP_PORT", "9600")))
