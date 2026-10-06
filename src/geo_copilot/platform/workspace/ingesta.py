"""La INGESTA: features, tablas y servicios remotos se materializan como capa del workspace.

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
    GeometryColumn,
    LayerRef,
    Provenance,
    WorkspaceTable,
)
from geo_copilot.platform.workspace.comun import (
    ROL_ESCRITURA,
    WorkspaceError,
    _a_texto,
    _esquema_de_propiedades,
    _qi,
    srid_de,
)
from geo_copilot.platform.workspace.remoto import leer_remoto

if TYPE_CHECKING:
    from geo_copilot.platform.workspace.comun import WorkspaceLimits

logger = get_logger("geo_copilot.platform.workspace.store")


def _store():
    """`store` importa este módulo; lo que las pruebas sustituyen ahí (`_descargar`) se resuelve al usarlo."""
    from geo_copilot.platform.workspace import store

    return store


class IngestaMixin:
    """La INGESTA: features, tablas y servicios remotos se materializan como capa del workspace."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        _pool: Any
        _limits: WorkspaceLimits
        async def _asegurar_esquema(self, conn: Any, workspace_id: str) -> str: ...
        async def _cuota(self, conn: Any, workspace_id: str, nuevas: int) -> None: ...

    async def ingest_features(
        self,
        workspace_id: str,
        name: str,
        feature_collection: dict[str, Any],
        *,
        crs: str,
        provenance: Provenance,
        provider: str = "core",
    ) -> LayerRef:
        """Materializa un FeatureCollection (en el CRS DECLARADO) como tabla 4326."""
        if feature_collection.get("type") != "FeatureCollection":
            raise WorkspaceError("se esperaba un FeatureCollection")
        features = feature_collection.get("features") or []
        srid = srid_de(crs)
        filas = [f.get("properties") or {} for f in features]
        geoms = [f.get("geometry") for f in features]
        return await self._materializar(
            workspace_id, name, filas, geoms, srid=srid, encoding="geojson",
            provenance=provenance, provider=provider,
        )

    async def ingest_table(
        self,
        workspace_id: str,
        name: str,
        rows: list[dict[str, Any]],
        *,
        geometry: GeometryColumn | None,
        provenance: Provenance,
        provider: str = "core",
    ) -> LayerRef:
        """Materializa filas; si `geometry` se declara, la columna se vuelve `geom`.

        La columna de geometría y su codificación las DECLARA quien produce la
        tabla (§3.5 del plan): aquí no se adivina qué columna es la geometría.
        """
        if geometry is None:
            return await self._materializar(
                workspace_id, name, rows, None, srid=None, encoding=None,
                provenance=provenance, provider=provider,
            )
        srid = srid_de(geometry.crs)
        quitar = {geometry.column} | ({geometry.lat_column} if geometry.lat_column else set())
        props = [{k: v for k, v in r.items() if k not in quitar} for r in rows]
        if geometry.encoding == "latlon":
            lon_c, lat_c = geometry.column, geometry.lat_column or ""
            geoms = [
                {"type": "Point", "coordinates": [r.get(lon_c), r.get(lat_c)]}
                if r.get(lon_c) is not None and r.get(lat_c) is not None
                else None
                for r in rows
            ]
            encoding = "geojson"
        else:
            geoms = [r.get(geometry.column) for r in rows]
            encoding = geometry.encoding
        return await self._materializar(
            workspace_id, name, props, geoms, srid=srid, encoding=encoding,
            provenance=provenance, provider=provider,
        )

    async def ingest_remote(
        self,
        workspace_id: str,
        name: str,
        uri: str,
        *,
        format: str,
        crs: str,
        provenance: Provenance,
        provider: str = "core",
        max_bytes: int = 200 * 1024 * 1024,
    ) -> LayerRef:
        """Descarga un `feature_ref` (GeoJSON / FlatGeobuf / GeoParquet) y lo materializa.

        La descarga va por `safe_http` (validación SSRF, IP pinning, sin
        redirects) y con tope de tamaño. El CRS lo declara el productor; si el
        archivo trae uno propio y NO coincide, se falla honesto (no se elige).
        """
        contenido = await _store()._descargar(uri, max_bytes=max_bytes)
        fc = leer_remoto(contenido, format=format, crs_declarado=crs)
        return await self.ingest_features(
            workspace_id, name, fc, crs=crs, provenance=provenance, provider=provider,
        )

    async def _materializar(  # noqa: PLR0915
        self,
        workspace_id: str,
        name: str,
        filas: list[dict[str, Any]],
        geoms: list[Any] | None,
        *,
        srid: int | None,
        encoding: str | None,
        provenance: Provenance,
        provider: str,
    ) -> LayerRef:
        columnas = _esquema_de_propiedades(filas)
        hex_id = uuid.uuid4().hex[:16]
        dataset_id, tabla = f"ds_{hex_id}", f"d_{hex_id}"
        expira = datetime.now(UTC) + timedelta(hours=self._limits.ttl_hours)

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            await self._cuota(conn, workspace_id, len(filas))
            esquema = await self._asegurar_esquema(conn, workspace_id)
            destino = f"{_qi(esquema)}.{_qi(tabla)}"

            defs = ["fid integer PRIMARY KEY"] + [f"{_qi(c)} {t}" for _, c, t, _ in columnas]
            if geoms is not None:
                defs.append("geom geometry(Geometry, 4326)")
            await conn.execute(f"CREATE TABLE {destino} ({', '.join(defs)})")

            # Carga por COPY a una tabla de paso con todo como texto; el cast y
            # la reproyección los hace PostGIS en un INSERT…SELECT (rápido y sin
            # codecs de geometría en el cliente).
            paso = f"paso_{hex_id}"
            cols_paso = ["fid integer"] + [f"{_qi(c)} text" for _, c, _, _ in columnas]
            if geoms is not None:
                cols_paso.append("g text")
            await conn.execute(f"CREATE TEMP TABLE {_qi(paso)} ({', '.join(cols_paso)}) ON COMMIT DROP")
            registros = []
            for i, props in enumerate(filas):
                fila: list[Any] = [i] + [_a_texto(props.get(k), t) for k, _, t, _ in columnas]
                if geoms is not None:
                    g = geoms[i]
                    if isinstance(g, (dict, list)):
                        g = json.dumps(g)
                    elif isinstance(g, (bytes, bytearray)):
                        g = bytes(g).hex()
                    fila.append(None if g in (None, "") else str(g))
                registros.append(tuple(fila))
            await conn.copy_records_to_table(paso, records=registros)

            sel = ["fid"] + [f"{_qi(c)}::{t}" for _, c, t, _ in columnas]
            if geoms is not None:
                crudo = {
                    "geojson": "ST_GeomFromGeoJSON(g)",
                    "wkt": "ST_GeomFromText(g)",
                    "wkb": "ST_GeomFromWKB(decode(g, 'hex'))",
                    "wkb_hex": "ST_GeomFromWKB(decode(g, 'hex'))",
                }.get(encoding or "")
                if crudo is None:
                    raise WorkspaceError(f"codificación de geometría no soportada: {encoding!r}")
                geom = f"ST_SetSRID({crudo}, {int(srid or 4326)})"
                if srid != 4326:
                    geom = f"ST_Transform({geom}, 4326)"
                # Reparar solo lo inválido (hecho verificable, no retoque estético).
                sel.append(
                    f"CASE WHEN g IS NULL THEN NULL ELSE "
                    f"(SELECT CASE WHEN ST_IsValid(x) THEN x ELSE ST_MakeValid(x) END "
                    f"FROM (SELECT {geom} AS x) s) END"
                )
            await conn.execute(f"INSERT INTO {destino} SELECT {', '.join(sel)} FROM {_qi(paso)}")

            bbox = None
            tipo_geom = None
            if geoms is not None:
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

            campos = [
                FieldInfo(
                    name=c, type=ct,  # type: ignore[arg-type]
                    sample=[p.get(k) for p in filas[:3] if p.get(k) is not None][:3],
                )
                for k, c, _, ct in columnas
            ]
            ref = LayerRef(
                id=dataset_id, name=name, kind="vector" if geoms is not None else "table",
                provider=provider, crs="EPSG:4326",
                storage=WorkspaceTable(
                    schema_name=esquema, table=tabla,
                    geometry_column="geom" if geoms is not None else None,
                    srid=4326 if geoms is not None else None,
                ),
                provenance=provenance, geometry_type=tipo_geom, bbox=bbox,
                feature_count=len(filas), fields=campos,
            )
            await conn.execute(
                "INSERT INTO ws_meta.datasets (id, workspace_id, schema_name, table_name, name, "
                "kind, feature_count, bytes, layer_ref, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)",
                dataset_id, workspace_id, esquema, tabla, name, ref.kind, len(filas),
                int(bytes_ or 0), ref.model_dump_json(), expira,
            )
        logger.info(
            "[workspace] %s: %s (%d elementos) en %s.%s", workspace_id[:8], name,
            len(filas), esquema, tabla,
        )
        return ref
