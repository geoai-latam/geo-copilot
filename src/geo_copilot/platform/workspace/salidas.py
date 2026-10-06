"""Lo que SALE del workspace: proyectos guardados, GeoJSON, GeoParquet y teselas vectoriales.

Salió de `DatasetStore` (F4 del plan de calidad: store.py tenía 904 líneas), tal cual.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger
from geo_copilot.platform.contracts import (
    WorkspaceTable,
)
from geo_copilot.platform.workspace.comun import (
    MVT_EXTENT,
    MVT_TIMEOUT_MS,
    ROL_ESCRITURA,
    ROL_LECTURA,
    WorkspaceError,
    _qi,
)

if TYPE_CHECKING:
    from geo_copilot.platform.contracts import LayerRef

logger = get_logger("geo_copilot.platform.workspace.store")


def _store():
    """`store` importa este módulo; lo que las pruebas sustituyen ahí (`MVT_MAX_FEATURES`) se resuelve al usarlo."""
    from geo_copilot.platform.workspace import store

    return store


class SalidasMixin:
    """Lo que SALE del workspace: proyectos guardados, GeoJSON, GeoParquet y teselas vectoriales."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        _pool: Any
        async def get(self, workspace_id: str, dataset_id: str) -> LayerRef | None: ...

    async def guardar_proyecto(self, workspace_id: str, nombre: str, estado: dict[str, Any], *,
                               owner_sub: str, org_id: str) -> dict[str, Any]:
        """Guarda (o reemplaza, por nombre en la sesión) el proyecto; sus datasets dejan de vencer.

        F6: el proyecto es de quien lo guarda (la sesión ya se comprobó suya en la ruta).
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            fila = await conn.fetchrow(
                "INSERT INTO ws_meta.proyectos (id, workspace_id, nombre, estado, owner_sub, org_id) "
                "VALUES ($1, $2, $3, $4::jsonb, $5, $6) "
                "ON CONFLICT (workspace_id, nombre) DO UPDATE SET estado = EXCLUDED.estado, updated_at = now() "
                "RETURNING id, nombre, workspace_id, updated_at",
                f"pr_{uuid.uuid4().hex[:16]}", workspace_id, nombre, json.dumps(estado, default=str),
                owner_sub, org_id,
            )
            await conn.execute("UPDATE ws_meta.datasets SET expires_at = 'infinity' WHERE workspace_id = $1",
                               workspace_id)
        return {**dict(fila), "updated_at": fila["updated_at"].isoformat()}

    async def listar_proyectos(self, owner_sub: str) -> list[dict[str, Any]]:
        """Los proyectos de ESTE usuario (F6: los de otro no existen para él)."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            filas = await conn.fetch(
                "SELECT id, nombre, workspace_id, updated_at, jsonb_array_length(COALESCE(estado->'capas', '[]')) AS capas "
                "FROM ws_meta.proyectos WHERE owner_sub = $1 ORDER BY updated_at DESC LIMIT 200", owner_sub)
        return [{**dict(f), "updated_at": f["updated_at"].isoformat()} for f in filas]

    async def obtener_proyecto(self, proyecto_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            f = await conn.fetchrow(
                "SELECT id, nombre, workspace_id, estado, updated_at, owner_sub FROM ws_meta.proyectos WHERE id = $1",
                proyecto_id)
        if f is None:
            return None
        return {"id": f["id"], "nombre": f["nombre"], "workspace_id": f["workspace_id"], "owner_sub": f["owner_sub"],
                "estado": json.loads(f["estado"]) if isinstance(f["estado"], str) else f["estado"],
                "updated_at": f["updated_at"].isoformat()}

    async def to_geojson(
        self, workspace_id: str, dataset_id: str, *, limit: int = 10_000, muestra: bool = False,
    ) -> dict:
        """El dataset como FeatureCollection 4326 (para entregas chicas).

        `muestra=True`: `limit` elementos repartidos por todo el dataset (orden
        por hash del fid, determinista) en vez de los primeros. Solo para diseñar
        un estilo (H23): nunca para analizar, que sería hacerlo sobre una parte.

        Cada Feature lleva `id` = su `fid` (FH.2): la selección del mapa se refiere
        a los elementos por esa identidad, no por su posición en la colección.
        """
        ref = await self.get(workspace_id, dataset_id)
        if ref is None or not isinstance(ref.storage, WorkspaceTable):
            raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión")
        destino = f"{_qi(ref.storage.schema_name)}.{_qi(ref.storage.table)}"
        cols = [f.name for f in ref.fields]
        props = ", ".join(f"'{c}', {_qi(c)}" for c in cols) or ""
        geom = "ST_AsGeoJSON(geom)::json" if ref.storage.geometry_column else "NULL::json"
        sql = (
            f"SELECT json_build_object('type','FeatureCollection','features', "
            f"coalesce(json_agg(json_build_object('type','Feature','id', fid, 'geometry', {geom}, "
            f"'properties', json_build_object({props}))), '[]'::json)) "
            f"FROM (SELECT * FROM {destino} ORDER BY {'md5(fid::text)' if muestra else 'fid'} "
            f"LIMIT {int(limit)}) t"
        )
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_LECTURA}")
            valor = await conn.fetchval(sql)
        salida: dict = json.loads(valor) if isinstance(valor, str) else dict(valor)
        return salida

    async def export_geoparquet(self, workspace_id: str, dataset_id: str, raiz: str) -> str:
        """Escribe el dataset como GeoParquet bajo `raiz`; devuelve la ruta RELATIVA.

        S2.4: así llegan los datasets al sandbox (volumen de solo lectura allí).
        Un dataset es inmutable, así que el archivo ya escrito se reutiliza.
        """
        import os

        ref = await self.get(workspace_id, dataset_id)
        if ref is None or not isinstance(ref.storage, WorkspaceTable):
            raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión")
        relativa = f"{ref.storage.schema_name}/{dataset_id}.parquet"
        destino = os.path.join(raiz, relativa)
        if os.path.exists(destino):
            return relativa

        tabla = f"{_qi(ref.storage.schema_name)}.{_qi(ref.storage.table)}"
        cols = [f.name for f in ref.fields]
        geom = "ST_AsBinary(geom) AS geom" if ref.storage.geometry_column else "NULL::bytea AS geom"
        select = ", ".join([geom, *(_qi(c) for c in cols)])
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_LECTURA}")
            filas = await conn.fetch(f"SELECT {select} FROM {tabla} ORDER BY fid")

        def _escribir() -> None:
            import geopandas as gpd
            import pandas as pd
            import shapely

            df = pd.DataFrame([dict(r) for r in filas], columns=["geom", *cols])
            wkb = [bytes(g) if g is not None else None for g in df.pop("geom")]
            # jsonb llega como texto y así se queda: un struct heterogéneo no
            # tiene esquema parquet estable.
            gdf = gpd.GeoDataFrame(df, geometry=shapely.from_wkb(wkb), crs="EPSG:4326")
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            # Escritura atómica: el sandbox nunca ve un parquet a medias.
            parcial = f"{destino}.{uuid.uuid4().hex}.tmp"
            gdf.to_parquet(parcial, index=False)
            os.replace(parcial, destino)

        import asyncio

        await asyncio.to_thread(_escribir)
        return relativa

    async def tile(
        self, workspace_id: str, dataset_id: str, z: int, x: int, y: int,
    ) -> bytes | None:
        """Una tesela MVT del dataset (capa `dataset`), o None si no hay nada.

        S2.3: las capas grandes se entregan así en vez de GeoJSON inline. Solo
        datasets de ESTA sesión (el catálogo se filtra por workspace_id).
        """
        ref = await self.get(workspace_id, dataset_id)
        if ref is None or not isinstance(ref.storage, WorkspaceTable) or not ref.storage.geometry_column:
            raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión")
        destino = f"{_qi(ref.storage.schema_name)}.{_qi(ref.storage.table)}"
        # MVT codifica número/texto/booleano; json va aplanado a texto.
        props = "".join(
            f", t.{_qi(f.name)}::text AS {_qi(f.name)}" if f.type == "json" else f", t.{_qi(f.name)}"
            for f in ref.fields
        )
        # El filtro `&&` compara en 4326 (el SRID del workspace) para usar el
        # índice GiST; solo la geometría ya filtrada se proyecta a 3857.
        sql = (
            "WITH b AS (SELECT ST_TileEnvelope($1, $2, $3) AS env), "
            "m AS (SELECT t.fid, ST_AsMVTGeom(ST_Transform(t.geom, 3857), b.env, "
            f"{MVT_EXTENT}, 64, true) AS geom{props} "
            f"FROM {destino} t, b WHERE t.geom && ST_Transform(b.env, 4326) "
            # H24 (V5 F2): si el tope por tesela muerde (zoom bajo), el recorte
            # debe ser UNIFORME en el espacio. Por fid salían los primeros —en
            # códigos postales, solo el noreste de EE. UU. —. Hash del fid:
            # determinista (la misma tesela siempre igual) y repartido.
            f"ORDER BY md5(t.fid::text) LIMIT {_store().MVT_MAX_FEATURES}) "
            f"SELECT ST_AsMVT(m.*, 'dataset', {MVT_EXTENT}, 'geom', 'fid') FROM m"
        )
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_LECTURA}")
            await conn.execute(f"SET LOCAL statement_timeout = {MVT_TIMEOUT_MS}")
            valor = await conn.fetchval(sql, z, x, y)
        return bytes(valor) if valor else None
