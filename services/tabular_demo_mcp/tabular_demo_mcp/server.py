"""almacen-demo — MCP TABULAR de prueba (G0) para el adaptador `tabular_geo` (F5, T5.4).

Imita la forma del MCP oficial de Snowflake: `list_tables` y `run_query(statement)` devuelven
las filas como TEXTO JSON, sin contrato geo (ni `_meta.geo`, ni GeoResult, ni structuredContent).
Así se prueba que el núcleo convierte filas de un tercero en capas SOLO con lo que el LLM
declara (qué columna es la geometría) y lo que el núcleo valida.

Datos: la base de demostración (`postgis-demo`, esquema `educacion`) con un rol de SOLO lectura;
el mismo validador SQL del núcleo (`geo_sql_guard`) rechaza lo que no sea una lectura.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from geo_mcp_kit import GeoMcpAuth, KeyRing, RateLimiter
from geo_sql_guard import analizar
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from psycopg import sql as psql

logger = logging.getLogger("almacen_demo")
SCOPES = {"list_tables": "almacen:read", "run_query": "almacen:read"}
MAX_FILAS = 5000
ESQUEMAS = ["educacion"]

mcp = FastMCP("almacen-demo", instructions="Almacén de datos de prueba (SQL de solo lectura).",
              stateless_http=True, json_response=True,
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


def _dsn() -> str:
    dsn = os.environ.get("ALMACEN_DSN", "")
    if not dsn:
        raise RuntimeError("almacen-demo necesita ALMACEN_DSN")
    return dsn


def _conectar() -> psycopg.Connection:
    con = psycopg.connect(_dsn(), connect_timeout=8)
    con.read_only = True
    return con


def _valor(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return bytes(v).hex()
    return v


def _tablas() -> set[str]:
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT lower(table_schema)||'.'||lower(table_name) FROM information_schema.tables "
                    "WHERE lower(table_schema) = ANY(%s)", (ESQUEMAS,))
        return {r[0] for r in cur.fetchall()}


@mcp.tool(description="Tablas del almacén con sus columnas y tipos.",
          annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def list_tables() -> str:
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT table_schema||'.'||table_name, column_name, data_type, udt_name "
                    "FROM information_schema.columns WHERE lower(table_schema) = ANY(%s) "
                    "ORDER BY 1, ordinal_position", (ESQUEMAS,))
        tablas: dict[str, list[str]] = {}
        for t, c, d, u in cur.fetchall():
            tablas.setdefault(t, []).append(f"{c} ({u if d == 'USER-DEFINED' else d})")
    return json.dumps([{"table": t, "columns": cols} for t, cols in tablas.items()], ensure_ascii=False)


@mcp.tool(description=("Ejecuta un SELECT de solo lectura y devuelve las filas en JSON (máx. 5000). "
                       "Las columnas geometry son PostGIS: para leerlas pídelas como texto, p. ej. "
                       "ST_AsText(geom)."),
          annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
def run_query(statement: str) -> str:
    texto = statement.strip().rstrip(";").strip()
    veredicto = analizar(texto, _tablas())
    if not veredicto.aceptada:
        raise ValueError("SQL rechazado: " + "; ".join(veredicto["motivos"]))
    try:
        with _conectar() as con, con.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '20s'")
            cur.execute(psql.SQL("SELECT * FROM ({}) AS q LIMIT {}").format(  # type: ignore[arg-type]
                psql.SQL(texto), psql.Literal(MAX_FILAS)))
            cols = [d.name for d in cur.description or []]
            filas = [{c: _valor(v) for c, v in zip(cols, r, strict=False)} for r in cur.fetchall()]
    except psycopg.Error as exc:
        raise ValueError(f"PostgreSQL: {str(exc).splitlines()[0][:300]}") from exc
    return json.dumps(filas, ensure_ascii=False, default=str)


def build_app():
    keys = KeyRing.from_json(os.environ.get("ALMACEN_MCP_KEYS", "[]"), known_scopes={"almacen:read"},
                             tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("almacen-demo no arranca sin claves (ALMACEN_MCP_KEYS)")
    return GeoMcpAuth(mcp.streamable_http_app(), keys, RateLimiter(), service="almacen-demo")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("ALMACEN_MCP_PORT", "9500")))
