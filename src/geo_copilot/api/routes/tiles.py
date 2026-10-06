"""
Teselado vectorial MVT on-the-fly desde PostGIS (§5 vector tiling).

    GET /api/v1/tiles/{schema}/{table}/{z}/{x}/{y}.pbf

Genera Mapbox Vector Tiles con ``ST_AsMVT`` / ``ST_AsMVTGeom`` directamente
contra la BD conectada — sin servidor de teselas externo (martin/PMTiles). Deja
renderizar tablas grandes (p.ej. los ~933k lotes catastrales) como capa
vectorial teselada en MapLibre en vez de traer todo el GeoJSON: MapLibre pide
solo las teselas visibles y PostGIS las recorta/simplifica al vuelo.

Seguridad:
- ``schema``/``table`` validados por regex y **confirmados contra
  ``geometry_columns``** (solo tablas geográficas reales → allowlist implícita);
  luego se usan como identificadores CITADOS (quote_ident), nunca interpolados
  crudos.
- ``z/x/y`` enteros acotados al rango válido de la pirámide.
- Las columnas de propiedades salen de ``information_schema`` (no del cliente) y
  se filtran a tipos que MVT sabe codificar.
- LIMIT por tesela + ``statement_timeout`` + ruta bajo ``require_principal``.
- S2.3: los esquemas del workspace (``ws_*``, ``ws_meta``) NO se sirven por
  esta ruta genérica: son de una sesión y solo salen por
  ``/tiles/ws/{session_id}/{dataset_id}/…``, que resuelve el dataset dentro
  del workspace de ESA sesión.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from geo_copilot.agents.gis_agent.sql_validator import quote_ident, quote_qualified
from geo_copilot.api.auth import asegurar_sesion_actual, require_principal
from geo_copilot.api.dependencies import get_app_state, get_db_pool
from geo_copilot.api.limiter import limiter
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/tiles",
    tags=["Tiles"],
    dependencies=[Depends(require_principal)],
)

# Identificadores SQL válidos (letra/_, luego alfanum/_, hasta 63 chars).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Tipos de columna que ST_AsMVT sabe codificar (number/string/bool). Se
# excluyen arrays, json, geometry, etc. para no romper la codificación.
_MVT_SAFE_TYPES = (
    "integer", "bigint", "smallint", "numeric", "double precision", "real",
    "text", "character varying", "character", "boolean", "date",
    "timestamp without time zone", "timestamp with time zone",
)

# S2.3: el id de sesión es la credencial de la sesión (igual que en /query y
# /ws); aquí solo se acota su forma. El dataset lo emite el store (`ds_<hex>`).
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DATASET_RE = re.compile(r"^ds_[0-9a-f]{16}$")

_TILE_FEATURE_LIMIT = 20000  # tope de features por tesela (evita teselas patológicas)
_MVT_EXTENT = 4096
_STATEMENT_TIMEOUT_MS = 15000


async def _geometry_info(pool, schema: str, table: str):
    """Devuelve (geom_column, srid, prop_columns) si la tabla existe en
    geometry_columns; None si no. La presencia en geometry_columns ES la
    allowlist (solo tablas geográficas registradas)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT f_geometry_column AS geom, srid
            FROM geometry_columns
            WHERE f_table_schema = $1 AND f_table_name = $2
            LIMIT 1
            """,
            schema,
            table,
        )
        if not row or not row["geom"]:
            return None
        geom = row["geom"]
        srid = int(row["srid"] or 0)
        cols = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
              AND column_name <> $3
              AND data_type = ANY($4::text[])
            ORDER BY ordinal_position
            """,
            schema,
            table,
            geom,
            list(_MVT_SAFE_TYPES),
        )
        return geom, srid, [c["column_name"] for c in cols]


def _es_workspace(schema: str) -> bool:
    return schema == "ws_meta" or schema.startswith("ws_")


def _tesela_valida(z: int, x: int, y: int) -> None:
    if not (0 <= z <= 24) or not (0 <= x < (1 << z)) or not (0 <= y < (1 << z)):
        raise HTTPException(status_code=400, detail="coordenada de tesela fuera de rango")


@router.get("/ws/{session_id}/{dataset_id}/{z}/{x}/{y}.pbf")
@limiter.limit("600/minute")
async def get_workspace_tile(
    request: Request, session_id: str, dataset_id: str, z: int, x: int, y: int,
) -> Response:
    """S2.3: tesela MVT de un dataset del workspace de la sesión (capa `dataset`)."""
    from geo_copilot.platform.workspace import WorkspaceError

    if not (_SESSION_RE.match(session_id) and _DATASET_RE.match(dataset_id)):
        raise HTTPException(status_code=400, detail="identificador inválido")
    await asegurar_sesion_actual(session_id)  # F6: las teselas del workspace de otro, 404
    _tesela_valida(z, x, y)
    store = get_app_state().dataset_store
    if store is None:
        raise HTTPException(status_code=503, detail="workspace no disponible")
    try:
        tile = await store.tile(session_id, dataset_id, z, x, y)
    except WorkspaceError as exc:
        # Mismo 404 para "no existe" y "es de otra sesión": no se revela cuál.
        raise HTTPException(status_code=404, detail="dataset no encontrado") from exc
    except Exception as exc:  # re-lanza como 502: errores de BD no deben filtrar detalle al cliente
        logger.warning("get_workspace_tile fallo %s z%s: %s", dataset_id, z, exc)
        raise HTTPException(status_code=502, detail="error generando la tesela") from exc
    if not tile:
        return Response(status_code=204)
    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        # De una sesión: ninguna caché compartida debe guardarla.
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/{schema}/{table}/{z}/{x}/{y}.pbf")
@limiter.limit("600/minute")
async def get_tile(
    request: Request,
    schema: str,
    table: str,
    z: int,
    x: int,
    y: int,
    pool=Depends(get_db_pool),
) -> Response:
    """Reemite una tesela MVT de una tabla geográfica allowlisted."""
    if not (_IDENT_RE.match(schema) and _IDENT_RE.match(table)):
        raise HTTPException(status_code=400, detail="identificador inválido")
    if _es_workspace(schema):
        raise HTTPException(status_code=404, detail="tabla geográfica no encontrada")
    _tesela_valida(z, x, y)
    if pool is None:
        raise HTTPException(status_code=503, detail="base de datos no disponible")

    info = await _geometry_info(pool, schema, table)
    if info is None:
        raise HTTPException(status_code=404, detail="tabla geográfica no encontrada")
    geom, srid, props = info

    qtable = quote_qualified(schema, table)
    qgeom = quote_ident(geom)
    layer_name = f"{schema}.{table}"
    prop_select = "".join(f", t.{quote_ident(p)}" for p in props)

    # RENDIMIENTO: el filtro `&&` debe compararse en el SRID de la geometría
    # para usar su índice espacial GiST. Transformamos el ENVELOPE de la tesela
    # al SRID de la tabla (no la geometría a 3857 — eso forzaría un seq scan
    # sobre toda la tabla, ~933k lotes). ST_AsMVTGeom sí proyecta la geometría
    # ya filtrada a 3857 para el espacio de la tesela. `srid` sale de
    # geometry_columns (entero validado), así que interpolarlo es seguro.
    if srid and srid != 3857:
        where_env = f"ST_Transform(bounds.env3857, {srid})"
    else:
        where_env = "bounds.env3857"
    sql = f"""
        WITH bounds AS (
            SELECT ST_TileEnvelope($1, $2, $3) AS env3857
        ),
        mvtgeom AS (
            SELECT
                ST_AsMVTGeom(
                    ST_Transform(t.{qgeom}, 3857),
                    bounds.env3857, {_MVT_EXTENT}, 64, true
                ) AS geom{prop_select}
            FROM {qtable} t, bounds
            WHERE t.{qgeom} && {where_env}
            LIMIT {_TILE_FEATURE_LIMIT}
        )
        SELECT ST_AsMVT(mvtgeom.*, $4, {_MVT_EXTENT}, 'geom') AS tile
        FROM mvtgeom
    """

    try:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute(
                    f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}"
                )
                row = await conn.fetchrow(sql, z, x, y, layer_name)
    except Exception as exc:  # captura amplia a propósito: errores de BD no deben filtrar detalle
        logger.warning("get_tile fallo %s/%s z%s: %s", schema, table, z, exc)
        raise HTTPException(status_code=502, detail="error generando la tesela") from exc

    tile = row["tile"] if row else None
    if not tile:
        # Sin features en el bbox → tesela vacía. 204 evita bytes inútiles.
        return Response(status_code=204)

    return Response(
        content=bytes(tile),
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=3600"},
    )
