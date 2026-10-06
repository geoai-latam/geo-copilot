"""postgis-mcp — servidor GeoMCP de SQL multi-BD sobre geo_mcp_kit (F5, S5.1 / T5.1).

Cualquier PostgreSQL/PostGIS se conecta como FUENTE por configuración (`SQL_SOURCES`), sin
tocar el núcleo. El LLM ve qué fuentes hay, describe sus tablas y escribe el SQL; el servidor
garantiza, de su lado, que solo se LEE:

- el MISMO validador estructural del núcleo (`geo_sql_guard`, sqlglot), con la lista de tablas
  de los esquemas publicados de esa fuente (una tabla fuera de ellos «no existe»);
- un rol de base de datos de solo lectura por fuente, en transacción READ ONLY y con
  `statement_timeout`;
- un tope de filas: si el resultado lo alcanza, se dice (no se presenta una muestra como total).

Un resultado con geometría vuelve como `feature_collection` en EPSG:4326 (el núcleo lo
materializa en el workspace y se cruza con cualquier otra fuente); sin geometría, como tabla.
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
from dataclasses import dataclass, field
from typing import Any

import psycopg
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
    table,
)
from geo_sql_guard import analizar, fijar_srid_de_columnas
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations
from psycopg import sql as psql

from postgis_mcp.areas import base_con_area
from postgis_mcp.hechos import del_resultado

logger = logging.getLogger("postgis_mcp")
SCOPES = {"sql_sources": "sql:read", "sql_describe": "sql:read", "sql_query": "sql:read"}
#: El usuario: «debe traer todo». Una capa (con geometría) llega completa hasta MAX_ELEMENTOS: hasta
#: MAX_EN_LINEA en la respuesta; más, como GeoJSON que sirve este servidor en /resultados/ (el núcleo
#: lo baja al workspace y se dibuja por teselas). Una TABLA (sin geometría) no va al mapa: MAX_FILAS.
MAX_FILAS = 5000
MAX_EN_LINEA = 5000
MAX_ELEMENTOS = 200_000
POR_DEFECTO = 50_000
TIMEOUT_SQL_S = 60
TTL_RESULTADOS_S = 3600
RESULTADOS = os.environ.get("SQL_RESULTADOS_DIR") or os.path.join(tempfile.gettempdir(), "sql-resultados")


class ErrorDeConsulta(Exception):
    """Un error que el LLM puede corregir (SQL inválido, tabla inexistente, timeout…)."""


@dataclass
class Fuente:
    id: str
    descripcion: str
    dsn: str
    esquemas: list[str]
    _tablas: set[str] | None = field(default=None, repr=False)
    _srid: int | None = field(default=None, repr=False)


def cargar_fuentes(raw: str | None = None, entorno: dict[str, str] | None = None) -> dict[str, Fuente]:
    """`SQL_SOURCES` = [{id, descripcion, dsn_env, esquemas}]; el DSN sale del entorno (secreto)."""
    entorno = os.environ if entorno is None else entorno
    fuentes: dict[str, Fuente] = {}
    for f in json.loads(raw if raw is not None else entorno.get("SQL_SOURCES", "[]")):
        dsn = entorno.get(str(f.get("dsn_env") or ""), "")
        if not dsn:
            logger.warning("fuente %s sin DSN (%s): se omite", f.get("id"), f.get("dsn_env"))
            continue
        esquemas = [str(e).lower() for e in f.get("esquemas") or []]
        if not esquemas:
            raise ValueError(f"la fuente {f.get('id')!r} debe publicar al menos un esquema")
        fuentes[str(f["id"])] = Fuente(str(f["id"]), str(f.get("descripcion") or ""), dsn, esquemas)
    return fuentes


FUENTES: dict[str, Fuente] = {}
_candado_validador = threading.Lock()  # el validador guarda el SRID de columnas por proceso
runner = ToolRunner(timeout_s=TIMEOUT_SQL_S + 10, service="postgis-mcp", expected_errors=(ErrorDeConsulta,))
mcp = FastMCP(
    "postgis-mcp",
    instructions=("Bases de datos PostgreSQL/PostGIS conectadas como fuentes. Mira las fuentes con "
                  "`sql_sources`, sus tablas con `sql_describe` y consulta con `sql_query` (solo SELECT)."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _fuente(source: str) -> Fuente:
    f = FUENTES.get(source)
    if f is None:
        raise ErrorDeConsulta(f"no hay una fuente «{source}»; las que hay: {', '.join(FUENTES) or 'ninguna'}")
    return f


def _conectar(f: Fuente) -> psycopg.Connection:
    con = psycopg.connect(f.dsn, connect_timeout=8, autocommit=False)
    con.read_only = True
    return con


def _tablas(f: Fuente) -> set[str]:
    """Las tablas y vistas de los esquemas publicados (esquema.tabla, minúsculas)."""
    if f._tablas is None:
        with _conectar(f) as con, con.cursor() as cur:
            cur.execute("SELECT lower(table_schema) || '.' || lower(table_name) FROM information_schema.tables "
                        "WHERE lower(table_schema) = ANY(%s)", (f.esquemas,))
            f._tablas = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT srid FROM geometry_columns WHERE lower(f_table_schema) = ANY(%s) "
                        "GROUP BY srid ORDER BY count(*) DESC LIMIT 1", (f.esquemas,))
            fila = cur.fetchone()
            f._srid = int(fila[0]) if fila and fila[0] else None
    return f._tablas


@mcp.tool(description=("Las bases de datos conectadas como fuentes: id, qué contienen, sus esquemas y (si son "
                       "pequeñas) sus tablas con las columnas y los valores de las que toman pocos. Úsalos "
                       "tal cual en el SQL (mayúsculas incluidas)."),
          annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False), structured_output=True)
def sql_sources() -> dict[str, Any]:
    # F3 (verdades ×5): tras ver solo «equipamientos», el agente respondía «no hay ninguna fuente con lotes»
    # (el catastro está en la base del propio agente, no aquí)
    return runner.run(lambda: geo_result([], facts={
        "alcance": "SOLO las fuentes de ESTE servicio: que un dato no esté aquí no significa que no exista (el "
                   "agente tiene su propia base de datos y otros servicios)",
        "fuentes": [_resumen_fuente(f) for f in FUENTES.values()]}))


def _columnas_con_valores(cur, esquema: str, tabla: str) -> list[dict]:
    """Columnas de una tabla con su tipo y, si toma pocos valores distintos, cuáles (hechos).

    Los valores salen de las estadísticas del planificador (sin leer la tabla): V3 F5 filtró
    `sector = 'Oficial'` donde el valor es 'OFICIAL' y dijo «ninguna oficial» con 11. Es el mismo
    hecho que ve el generador de la BD interna (T5.1).
    """
    cur.execute("SELECT column_name, data_type, udt_name FROM information_schema.columns "
                "WHERE lower(table_schema) = %s AND lower(table_name) = %s ORDER BY ordinal_position",
                (esquema, tabla))
    columnas = [{"columna": c, "tipo": u if d == "USER-DEFINED" else d} for c, d, u in cur.fetchall()]
    # n_distinct > 0 es el número de valores; < 0, la fracción de las filas (tablas pequeñas)
    cur.execute("SELECT s.attname, CASE WHEN s.n_distinct < 0 THEN -s.n_distinct * greatest(c.reltuples, 0) "
                "ELSE s.n_distinct END, s.most_common_vals::text FROM pg_stats s JOIN pg_class c "
                "ON c.oid = (quote_ident(s.schemaname) || '.' || quote_ident(s.tablename))::regclass "
                "WHERE lower(s.schemaname) = %s AND lower(s.tablename) = %s", (esquema, tabla))
    estad = [(a, n, v) for a, n, v in cur.fetchall() if v and n is not None]
    valores = {a: v for a, n, v in estad if 0 < n <= 20}
    # con muchos valores, unos EJEMPLOS (los más frecuentes): enseñan cómo se escriben (p. ej. en
    # mayúsculas: V3 F5 filtró 'Soacha' donde el dato es 'SOACHA' y concluyó que no había sedes)
    ejemplos = {a: ",".join(v.strip("{}").split(",")[:5]) for a, n, v in estad if n > 20}
    for c in columnas:
        if c["columna"] in valores:
            c["valores"] = valores[c["columna"]].strip("{}")[:300]
        elif c["columna"] in ejemplos:
            c["ejemplos"] = ejemplos[c["columna"]][:200]
    return columnas


MAX_TABLAS_EN_RESUMEN = 8


def _resumen_fuente(f: Fuente) -> dict:
    """La fuente con sus tablas y columnas (y valores) si es pequeña: es lo primero que el LLM
    pide, y sin ello escribía SQL con columnas y valores supuestos (V3 F5)."""
    out: dict[str, Any] = {"id": f.id, "descripcion": f.descripcion, "esquemas": f.esquemas}
    try:
        tablas = sorted(_tablas(f))
        if len(tablas) > MAX_TABLAS_EN_RESUMEN:
            out["tablas"] = tablas[:50]
            out["nota"] = "muchas tablas: mira las columnas de la que necesites con sql_describe(source, table)"
            return out
        with _conectar(f) as con, con.cursor() as cur:
            out["tablas"] = [{"tabla": t, "columnas": [
                c["columna"] + (f" [valores: {c['valores']}]" if c.get("valores") else
                                (f" [ej.: {c['ejemplos']}]" if c.get("ejemplos") else ""))
                for c in _columnas_con_valores(cur, *t.split(".", 1))]} for t in tablas]
    except psycopg.Error as exc:
        out["nota"] = f"no se pudo leer su catálogo ({str(exc).splitlines()[0][:120]}); usa sql_describe"
    return out


def _describir(source: str, tabla: str | None) -> dict:
    f = _fuente(source)
    tablas = _tablas(f)
    with _conectar(f) as con, con.cursor() as cur:
        if not tabla:
            cur.execute(
                "SELECT t.table_schema || '.' || t.table_name, obj_description((quote_ident(t.table_schema) || '.' || "
                "quote_ident(t.table_name))::regclass), g.f_geometry_column, g.type, g.srid, "
                "(SELECT reltuples::bigint FROM pg_class WHERE oid = (quote_ident(t.table_schema) || '.' || "
                "quote_ident(t.table_name))::regclass) "
                "FROM information_schema.tables t LEFT JOIN geometry_columns g "
                "ON g.f_table_schema = t.table_schema AND g.f_table_name = t.table_name "
                "WHERE lower(t.table_schema) = ANY(%s) ORDER BY 1", (f.esquemas,))
            return geo_result([], facts={"fuente": f.id, "tablas": [
                {"tabla": r[0], "descripcion": r[1], "geometria": r[2], "tipo": r[3], "srid": r[4],
                 "filas_aprox": max(int(r[5] or 0), 0)} for r in cur.fetchall()]})
        nombre = tabla.lower()
        if nombre not in tablas:
            raise ErrorDeConsulta(f"la tabla «{tabla}» no está en la fuente «{f.id}»; las que hay: "
                                  f"{', '.join(sorted(tablas)) or 'ninguna'}")
        esquema, _, t = nombre.partition(".")
        columnas = _columnas_con_valores(cur, esquema, t)
        cur.execute("SELECT f_geometry_column, type, srid FROM geometry_columns "
                    "WHERE lower(f_table_schema) = %s AND lower(f_table_name) = %s", (esquema, t))
        geoms = [{"columna": g, "tipo": ty, "srid": s} for g, ty, s in cur.fetchall()]
        no_geo = [c["columna"] for c in columnas if c["columna"] not in {g["columna"] for g in geoms}][:12]
        cur.execute(psql.SQL("SELECT {} FROM {}.{} LIMIT 3").format(
            psql.SQL(", ").join(psql.Identifier(c) for c in no_geo), psql.Identifier(esquema), psql.Identifier(t)))
        muestra = [dict(zip(no_geo, (str(v) if v is not None else None for v in fila), strict=False))
                   for fila in cur.fetchall()]
    return geo_result([], facts={"fuente": f.id, "tabla": nombre, "columnas": columnas, "geometria": geoms,
                                 "muestra": muestra})


@mcp.tool(description=("Tablas de una fuente (sin `table`: con su geometría, SRID y filas aproximadas) o las "
                       "columnas, la geometría y 3 filas de muestra de una tabla (`table` = esquema.tabla)."),
          annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False), structured_output=True)
def sql_describe(source: str, table: str | None = None) -> dict[str, Any]:
    return runner.run(_describir, source, table)


#: gpt-5.4 (2026-10-05): con la columna real a la vista, preguntaba «¿lo corro con area_m2?» en vez de repetir
REPITE = (". Si una de ellas es sin duda la que quisiste, repite la consulta con ella tú mismo; no le preguntes "
          "al usuario")


def _columnas_de(f: Fuente, tablas: list[str]) -> str:
    """«Columnas de esquema.tabla: …» de las tablas publicadas que usa la consulta (hechos)."""
    partes = []
    try:
        with _conectar(f) as con, con.cursor() as cur:
            for t in sorted({str(x).lower() for x in tablas} & _tablas(f)):
                esquema, _, nombre = t.partition(".")
                cur.execute("SELECT column_name FROM information_schema.columns WHERE lower(table_schema) = %s "
                            "AND lower(table_name) = %s ORDER BY ordinal_position", (esquema, nombre))
                partes.append(f"columnas de {t}: {', '.join(r[0] for r in cur.fetchall())}")
    except psycopg.Error:
        return "mira las columnas reales con sql_describe(source, table)"
    return "; ".join(partes) or "mira las columnas reales con sql_describe(source, table)"


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


def _consultar(source: str, consulta: str, max_filas: int, aoi: dict | None = None) -> dict:  # noqa: PLR0915
    f = _fuente(source)
    tablas = _tablas(f)
    texto = consulta.strip().rstrip(";").strip()
    with _candado_validador:
        fijar_srid_de_columnas(f._srid)
        veredicto = analizar(texto, tablas)
    if not veredicto.aceptada:
        motivos = "; ".join(veredicto["motivos"])
        if "no disponible en el catálogo" in motivos:
            # V5 (sql): el LLM probó «lotes», «localidades», «bd_interna»… hasta agotar el turno: el
            # rechazo no decía qué tablas SÍ hay. Ahora lo dice (como ya lo hacía con las columnas).
            motivos += (f". Tablas publicadas en «{f.id}»: {', '.join(sorted(tablas)[:40])}. Las capas del mapa o "
                        "del workspace (p. ej. el límite de un lugar) NO son tablas de esta fuente: para filtrar por "
                        "el área de una, pásala en `aoi` y deja el SQL solo sobre estas tablas")
        raise ErrorDeConsulta("SQL rechazado: " + motivos)
    limite = max(1, min(int(max_filas), MAX_ELEMENTOS))
    try:
        with _conectar(f) as con, con.cursor() as cur:
            cur.execute(psql.SQL("SET LOCAL statement_timeout = {}").format(psql.Literal(f"{TIMEOUT_SQL_S}s")))
            cur.execute("SELECT oid FROM pg_type WHERE typname = 'geometry'")
            fila = cur.fetchone()
            oid_geom = fila[0] if fila else None
            # columnas del resultado (sin traer filas) para saber cuál es la geometría
            cur.execute(psql.SQL("SELECT * FROM ({}) AS q LIMIT 0").format(psql.SQL(texto)))  # type: ignore[arg-type]
            cols = [(d.name, d.type_code) for d in cur.description or []]
            geo = next((n for n, t in cols if oid_geom is not None and t == oid_geom), None)
            base: Any = psql.SQL(texto)  # type: ignore[arg-type]
            if aoi:  # el área filtra EN EL ORIGEN (también un count/avg: se filtra la tabla)
                base = base_con_area(cur, texto, geo, aoi, sorted(veredicto.get("tablas") or []), ErrorDeConsulta)
            otras = [n for n, t in cols if not (oid_geom is not None and t == oid_geom)]
            campos = [psql.SQL("q.{}").format(psql.Identifier(n)) for n in otras]
            if geo:
                campos.append(psql.SQL("ST_AsGeoJSON(ST_Transform(q.{}, 4326), 7) AS __geojson").format(
                    psql.Identifier(geo)))
            cur.execute(psql.SQL("SELECT {} FROM ({}) AS q LIMIT {}").format(
                psql.SQL(", ").join(campos), base, psql.Literal(limite + 1)))
            if not geo:
                limite = min(limite, MAX_FILAS)  # una tabla no va al mapa ni al workspace: tope de filas
            filas = cur.fetchmany(limite + 1)
    except psycopg.errors.QueryCanceled as exc:
        raise ErrorDeConsulta(f"la consulta superó {TIMEOUT_SQL_S} s; acótala (un filtro, un índice espacial)") from exc
    except psycopg.errors.UndefinedColumn as exc:
        # V3 F5: el LLM adivinó 5 nombres de columna seguidos («municipio», «mun»…) hasta agotar el
        # turno: el error de PostgreSQL no dice cuáles SÍ existen. Aquí se dicen.
        raise ErrorDeConsulta(f"PostgreSQL: {str(exc).splitlines()[0][:200]}. "
                              + _columnas_de(f, veredicto["tablas"]) + REPITE) from exc
    except psycopg.Error as exc:
        raise ErrorDeConsulta(f"PostgreSQL: {str(exc).splitlines()[0][:300]}") from exc
    completo = len(filas) <= limite
    filas = filas[:limite]
    hechos = del_resultado(f.id, veredicto, texto, filas, completo=completo, limite=limite, otras=otras, geo=geo,
                           aoi=aoi, conectar=lambda: _conectar(f), timeout_s=TIMEOUT_SQL_S)

    def valor(v: Any) -> Any:
        return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)

    if geo:
        feats = [{"type": "Feature", "geometry": json.loads(r[-1]) if r[-1] else None,
                  "properties": {n: valor(v) for n, v in zip(otras, r[:-1], strict=False)}} for r in filas]
        fc = {"type": "FeatureCollection", "features": feats}
        if len(feats) <= MAX_EN_LINEA:
            return geo_result([feature_collection(f"{f.id} · consulta", fc, crs="EPSG:4326")], facts=hechos)
        # capa grande: como archivo (no satura la respuesta MCP); el núcleo la baja al workspace
        os.makedirs(RESULTADOS, exist_ok=True)
        _purgar()
        ident = uuid.uuid4().hex
        with open(os.path.join(RESULTADOS, f"{ident}.geojson"), "w", encoding="utf-8") as fh:
            json.dump(fc, fh, ensure_ascii=False)
        return geo_result([feature_ref(f"{f.id} · consulta", f"/resultados/{ident}.geojson", fmt="geojson",
                                       crs="EPSG:4326", feature_count=len(feats))], facts=hechos)
    rows = [{n: valor(v) for n, v in zip(otras, r, strict=False)} for r in filas]
    return geo_result([table(otras, rows, name=f"{f.id} · consulta")], facts=hechos)


@mcp.tool(
    description=("Ejecuta un SELECT de solo lectura en una fuente (`source` = su id). Usa las tablas con su "
                 "esquema (esquema.tabla) tal como las da `sql_describe`. Con geometría devuelve una capa "
                 "(EPSG:4326) que queda en el workspace para cruzarla con otras fuentes; sin geometría, una "
                 "tabla (selecciona la geometría TAL CUAL, sin ST_AsGeoJSON/ST_AsText: el servidor la "
                 "convierte). Con geometría trae hasta `max_filas` (por defecto 50000, máx. 200000; si piden "
                 "«todos», no lo bajes: las capas grandes viajan como archivo y se dibujan por teselas); una "
                 "tabla sin geometría, hasta 5000 filas. Si se alcanza el tope, lo dice. `aoi` (un área: una "
                 "capa, p. ej. el límite de un lugar) deja solo lo que la interseca, filtrado en la base: para "
                 "«los lotes de <un lugar>» cuando la tabla no tiene una columna con ese lugar."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    meta=geo_meta(inputs={"aoi": ["geometry", "layer_ref"]}, outputs=["feature_collection", "feature_ref", "table"],
                  cost="medium", geometry_types={"aoi": ["Polygon", "MultiPolygon"]}),
)
def sql_query(source: str, sql: str, max_filas: int = POR_DEFECTO, aoi: dict | None = None) -> CallToolResult:
    return compact_result(runner.run(_consultar, source, sql, max_filas, aoi))

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
    FUENTES.clear()
    FUENTES.update(cargar_fuentes())
    if not FUENTES:
        raise RuntimeError("postgis-mcp no arranca sin fuentes (SQL_SOURCES con su DSN)")
    keys = KeyRing.from_json(os.environ.get("POSTGIS_MCP_KEYS", "[]"), known_scopes={"sql:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("postgis-mcp no arranca sin claves (POSTGIS_MCP_KEYS)")
    ruta = ExtraRoute(lambda p: (m.group(1) if (m := _RESULTADO_RE.match(p)) else None), _servir_resultado,
                      requires_tool="sql_query", weight=0.2)
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="postgis-mcp", routes=(ruta,))


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("POSTGIS_MCP_PORT", "9300")))
