"""Las capas DERIVADAS y sus cambios: crear desde SQL, renombrar, reemplazar geometrías y
añadir una medida calculada en PostGIS.

Salió de `DatasetStore` (F4 del plan de calidad: store.py tenía 904 líneas), tal cual.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger
from geo_copilot.platform.contracts import (
    FieldInfo,
    LayerRef,
    Provenance,
    ProvenanceEdit,
    WorkspaceTable,
)
from geo_copilot.platform.workspace.comun import (
    _TIPO_PG,
    OPS_TIMEOUT_MS,
    ROL_ESCRITURA,
    WorkspaceError,
    _muestra,
    _qi,
)

if TYPE_CHECKING:
    from geo_copilot.platform.workspace.comun import WorkspaceLimits

logger = get_logger("geo_copilot.platform.workspace.store")


class DerivadosMixin:
    """Las capas DERIVADAS y sus cambios: crear desde SQL, renombrar, reemplazar geometrías y medir."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        _pool: Any
        _limits: WorkspaceLimits
        async def _asegurar_esquema(self, conn: Any, workspace_id: str) -> str: ...
        async def _cuota(self, conn: Any, workspace_id: str, nuevas: int) -> None: ...
        async def get(self, workspace_id: str, dataset_id: str) -> LayerRef | None: ...

    async def crear_desde_sql(
        self,
        workspace_id: str,
        name: str,
        select_sql: str,
        params: tuple[Any, ...] = (),
        *,
        provenance: Provenance,
        provider: str = "core",
    ) -> LayerRef:
        """Materializa un SELECT sobre datasets de ESTA sesión como dataset nuevo.

        S2.5: así escriben sus resultados las capacidades espaciales deterministas.
        `select_sql` lo arma código propio (nunca el LLM) y debe devolver las
        propiedades más, opcionalmente, una columna `geom` en EPSG:4326.
        """
        hex_id = uuid.uuid4().hex[:16]
        dataset_id, tabla = f"ds_{hex_id}", f"d_{hex_id}"
        expira = datetime.now(UTC) + timedelta(hours=self._limits.ttl_hours)

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            await conn.execute(f"SET LOCAL statement_timeout = {OPS_TIMEOUT_MS}")
            esquema = await self._asegurar_esquema(conn, workspace_id)
            destino = f"{_qi(esquema)}.{_qi(tabla)}"
            await conn.execute(
                f"CREATE TABLE {destino} AS "
                f"SELECT (row_number() OVER ())::integer AS fid, q.* FROM ({select_sql}) q",
                *params,
            )
            n = int(await conn.fetchval(f"SELECT count(*) FROM {destino}"))
            # La cuota se mide sobre lo producido; si no cabe, se deshace todo.
            await self._cuota(conn, workspace_id, n)
            await conn.execute(f"ALTER TABLE {destino} ADD PRIMARY KEY (fid)")

            columnas = await conn.fetch(
                "SELECT column_name AS c, udt_name AS t FROM information_schema.columns "
                "WHERE table_schema = $1 AND table_name = $2 AND column_name <> 'fid' "
                "ORDER BY ordinal_position",
                esquema, tabla,
            )
            con_geom = any(c["c"] == "geom" and c["t"] == "geometry" for c in columnas)
            bbox = tipo_geom = None
            if con_geom:
                await conn.execute(
                    f"ALTER TABLE {destino} ALTER COLUMN geom TYPE geometry(Geometry, 4326) "
                    f"USING ST_SetSRID(geom, 4326)"
                )
                await conn.execute(f"CREATE INDEX ON {destino} USING gist (geom)")
                ext = await conn.fetchrow(
                    f"SELECT ST_XMin(e) AS a, ST_YMin(e) AS b, ST_XMax(e) AS c, ST_YMax(e) AS d "
                    f"FROM (SELECT ST_Extent(geom) AS e FROM {destino}) s"
                )
                if ext and ext["a"] is not None:
                    bbox = (ext["a"], ext["b"], ext["c"], ext["d"])
                tipos = await conn.fetch(
                    f"SELECT DISTINCT ST_GeometryType(geom) AS t FROM {destino} WHERE geom IS NOT NULL"
                )
                nombres = sorted({r["t"].removeprefix("ST_") for r in tipos})
                tipo_geom = nombres[0] if len(nombres) == 1 else ("Geometry" if nombres else None)
            await conn.execute(f"ANALYZE {destino}")
            bytes_ = await conn.fetchval(f"SELECT pg_total_relation_size('{esquema}.{tabla}')")

            props = [c for c in columnas if c["c"] != "geom"]
            muestras = await conn.fetch(
                f"SELECT {', '.join(_qi(c['c']) for c in props) or '1'} FROM {destino} ORDER BY fid LIMIT 3"
            ) if props else []
            campos = [
                FieldInfo(
                    name=c["c"], type=_TIPO_PG.get(c["t"], "unknown"),  # type: ignore[arg-type]
                    sample=[_muestra(m[c["c"]]) for m in muestras if m[c["c"]] is not None][:3],
                )
                for c in props
            ]
            ref = LayerRef(
                id=dataset_id, name=name, kind="vector" if con_geom else "table",
                provider=provider, crs="EPSG:4326",
                storage=WorkspaceTable(
                    schema_name=esquema, table=tabla,
                    geometry_column="geom" if con_geom else None,
                    srid=4326 if con_geom else None,
                ),
                provenance=provenance, geometry_type=tipo_geom, bbox=bbox,
                feature_count=n, fields=campos,
            )
            await conn.execute(
                "INSERT INTO ws_meta.datasets (id, workspace_id, schema_name, table_name, name, "
                "kind, feature_count, bytes, layer_ref, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)",
                dataset_id, workspace_id, esquema, tabla, name, ref.kind, n,
                int(bytes_ or 0), ref.model_dump_json(), expira,
            )
        logger.info("[workspace] %s: %s (%d elementos, derivado)", workspace_id[:8], name, n)
        return ref

    async def renombrar(self, workspace_id: str, dataset_id: str, nombre: str) -> LayerRef:
        """FH.3: el nombre de un dataset de la sesión (el que ven el usuario y el agente)."""
        nombre = nombre.strip()
        if not nombre or len(nombre) > 120:
            raise WorkspaceError("el nombre debe tener entre 1 y 120 caracteres")
        ref = await self.get(workspace_id, dataset_id)
        if ref is None:
            raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión")
        nuevo = ref.model_copy(update={"name": nombre})
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            await conn.execute(
                "UPDATE ws_meta.datasets SET name = $3, layer_ref = $4::jsonb "
                "WHERE workspace_id = $1 AND id = $2",
                workspace_id, dataset_id, nombre, nuevo.model_dump_json(),
            )
        return nuevo

    async def reemplazar_geometrias(self, workspace_id: str, dataset_id: str, fc: dict) -> LayerRef:
        """FH.3: nueva geometría de elementos EXISTENTES, por su `fid` (el `id` de cada Feature).

        Para editar los vértices de un dibujo: el dataset conserva su id, así que lo
        que ya lo referencia (el mapa, el agente, otra capa) sigue apuntando a él.
        """
        ref = await self.get(workspace_id, dataset_id)
        if ref is None or not isinstance(ref.storage, WorkspaceTable) or not ref.storage.geometry_column:
            raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión o no tiene geometría")
        cambios: list[tuple[int, str]] = []
        for f in (fc.get("features") or []) if isinstance(fc, dict) else []:
            fid, geom = (f or {}).get("id"), (f or {}).get("geometry")
            if not isinstance(fid, int) or isinstance(fid, bool) or not isinstance(geom, dict):
                raise WorkspaceError("cada elemento necesita su `id` (fid) y una geometría")
            cambios.append((fid, json.dumps(geom)))
        if not cambios:
            raise WorkspaceError("no hay geometrías que guardar")
        destino = f"{_qi(ref.storage.schema_name)}.{_qi(ref.storage.table)}"
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            for fid, geom in cambios:
                hecho = await conn.execute(
                    f"UPDATE {destino} SET geom = (SELECT CASE WHEN ST_IsValid(x) THEN x ELSE ST_MakeValid(x) END "
                    f"FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON($2), 4326) AS x) s) WHERE fid = $1",
                    fid, geom,
                )
                if hecho.endswith(" 0"):
                    raise WorkspaceError(f"el elemento {fid} no existe en {dataset_id}")
            ext = await conn.fetchrow(
                f"SELECT ST_XMin(e) AS a, ST_YMin(e) AS b, ST_XMax(e) AS c, ST_YMax(e) AS d "
                f"FROM (SELECT ST_Extent(geom) AS e FROM {destino}) s"
            )
            bbox = (ext["a"], ext["b"], ext["c"], ext["d"]) if ext and ext["a"] is not None else None
            nuevo = ref.model_copy(update={"bbox": bbox})
            await conn.execute(
                "UPDATE ws_meta.datasets SET layer_ref = $3::jsonb WHERE workspace_id = $1 AND id = $2",
                workspace_id, dataset_id, nuevo.model_dump_json(),
            )
        return nuevo

    #: FH (V5 en Chrome): medidas geométricas por elemento, EXACTAS (geodésicas). Lista
    #: cerrada: la expresión nunca viene del LLM.
    MEDIDAS: dict[str, tuple[str, str]] = {
        "area": ("area_m2", "ST_Area(geom::geography)"),
        "longitud": ("longitud_m", "ST_Length(geom::geography)"),
        "perimetro": ("perimetro_m", "ST_Perimeter(geom::geography)"),
    }

    async def agregar_medida(self, workspace_id: str, dataset_id: str, medida: str) -> LayerRef:
        """Añade a CADA elemento del dataset su área / longitud / perímetro (m², m) como un
        campo más. El dataset conserva su id y sus fid: la capa del mapa se actualiza en su
        sitio (antes, un script de Python creaba una capa nueva encima)."""
        if medida not in self.MEDIDAS:
            raise WorkspaceError(f"medida no soportada: {medida}; usa una de {', '.join(self.MEDIDAS)}")
        ref = await self.get(workspace_id, dataset_id)
        if ref is None or not isinstance(ref.storage, WorkspaceTable) or not ref.storage.geometry_column:
            raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión o no tiene geometría")
        campo, expresion = self.MEDIDAS[medida]
        destino = f"{_qi(ref.storage.schema_name)}.{_qi(ref.storage.table)}"
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            await conn.execute(f"ALTER TABLE {destino} ADD COLUMN IF NOT EXISTS {_qi(campo)} double precision")
            await conn.execute(f"UPDATE {destino} SET {_qi(campo)} = round(({expresion})::numeric, 2)")
            muestra = [r[0] for r in await conn.fetch(
                f"SELECT {_qi(campo)} FROM {destino} WHERE {_qi(campo)} IS NOT NULL ORDER BY fid LIMIT 3")]
            campos = [f for f in ref.fields if f.name != campo] + [FieldInfo(name=campo, type="number", sample=muestra)]
            edicion = ProvenanceEdit(capability="core.add_measure", produced_at=datetime.now(UTC),
                                     arguments={"measure": medida, "campo": campo})
            prov = ref.provenance.model_copy(update={"edits": [*ref.provenance.edits, edicion]})
            nuevo = ref.model_copy(update={"fields": campos, "provenance": prov})
            await conn.execute(
                "UPDATE ws_meta.datasets SET layer_ref = $3::jsonb WHERE workspace_id = $1 AND id = $2",
                workspace_id, dataset_id, nuevo.model_dump_json(),
            )
        return nuevo
