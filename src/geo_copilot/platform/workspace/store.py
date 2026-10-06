"""Dataset Store: el workspace espacial en PostGIS (S2.1 del plan de plataforma).

Todo resultado —una capa de la BD, un servicio ArcGIS, la salida de un MCP, un
análisis— se MATERIALIZA como tabla en el esquema de la sesión (`ws_<hash>`) y
queda descrito por un `LayerRef` en `ws_meta.datasets`. Así:

  - fuentes distintas se cruzan en SQL con los datos de dominio (S2.2);
  - las capas grandes se sirven como teselas vectoriales, no como GeoJSON (S2.3);
  - el sandbox recibe N datasets, no `gdf`/`gdf2` (S2.4);
  - desaparece el tope de 10.000 features y de dos capas.

Seguridad: se escribe SIEMPRE dentro de una transacción con
`SET LOCAL ROLE geo_workspace` (el único rol que escribe, y solo en `ws_*`). Los
nombres de esquema/tabla/columna los genera este módulo (hash de la sesión,
uuid, nombres saneados): nada que venga del usuario o del LLM entra como
identificador SQL.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.core.logging import get_logger
from geo_copilot.platform.contracts import (
    LayerRef,
)

logger = get_logger(__name__)

# F4: lo común (roles, límites, errores, utilidades SQL) y la lectura remota viven en sus módulos;
# se reexportan aquí porque el resto del código los importa de `store`.
from geo_copilot.platform.workspace.comun import (  # noqa: F401
    _RESERVADAS,
    _TIPO_PG,
    MVT_EXTENT,
    MVT_MAX_FEATURES,
    MVT_TIMEOUT_MS,
    OPS_TIMEOUT_MS,
    ROL_ESCRITURA,
    ROL_LECTURA,
    QuotaExceeded,
    WorkspaceError,
    WorkspaceLimits,
    _a_texto,
    _esquema_de_propiedades,
    _muestra,
    _nombre_columna,
    _qi,
    _tipo,
    schema_de,
    srid_de,
)

# F4: la ingesta, las capas derivadas y las salidas viven en sus módulos (mixins).
from geo_copilot.platform.workspace.derivados import DerivadosMixin
from geo_copilot.platform.workspace.ingesta import IngestaMixin
from geo_copilot.platform.workspace.remoto import _descargar, leer_remoto  # noqa: F401
from geo_copilot.platform.workspace.salidas import SalidasMixin


class DatasetStore(IngestaMixin, DerivadosMixin, SalidasMixin):
    """Materializa y describe datasets del workspace de cada sesión."""

    def __init__(self, pool: Any, limits: WorkspaceLimits | None = None) -> None:
        self._pool = pool
        self._limits = limits or WorkspaceLimits()

    # ------------------------------------------------------------------
    # Esquema de la sesión
    # ------------------------------------------------------------------

    async def _asegurar_esquema(self, conn: Any, workspace_id: str) -> str:
        esquema = schema_de(workspace_id)
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_qi(esquema)}")
        # El lector de SQL (S2.2) necesita ver el esquema; la allowlist del
        # validador limita a CADA sesión a su propio esquema.
        await conn.execute(f"GRANT USAGE ON SCHEMA {_qi(esquema)} TO {ROL_LECTURA}")
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_qi(esquema)} "
            f"GRANT SELECT ON TABLES TO {ROL_LECTURA}"
        )
        return esquema

    async def _cuota(self, conn: Any, workspace_id: str, nuevas: int) -> None:
        lim = self._limits
        if nuevas > lim.max_features_per_dataset:
            raise QuotaExceeded(
                f"el dataset tiene {nuevas} elementos y el máximo por dataset es "
                f"{lim.max_features_per_dataset}"
            )
        fila = await conn.fetchrow(
            "SELECT count(*) AS n, coalesce(sum(feature_count), 0) AS f "
            "FROM ws_meta.datasets WHERE workspace_id = $1 AND expires_at > now()",
            workspace_id,
        )
        if fila["n"] + 1 > lim.max_datasets_per_workspace:
            raise QuotaExceeded(
                f"el espacio de trabajo ya tiene {fila['n']} datasets "
                f"(máximo {lim.max_datasets_per_workspace})"
            )
        if fila["f"] + nuevas > lim.max_features_per_workspace:
            raise QuotaExceeded(
                f"el espacio de trabajo superaría {lim.max_features_per_workspace} elementos"
            )

    # ------------------------------------------------------------------
    # Ingesta
    # ------------------------------------------------------------------


    async def hechos(self, workspace_id: str, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        """Una fila de cifras (medidas, conteos) sobre datasets de la sesión, como lector."""
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_LECTURA}")
            await conn.execute(f"SET LOCAL statement_timeout = {OPS_TIMEOUT_MS}")
            fila = await conn.fetchrow(sql, *params)
        return dict(fila) if fila else {}

    async def filas(self, workspace_id: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Filas (p. ej. valores + WKB) de datasets de la sesión, como lector."""
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_LECTURA}")
            await conn.execute(f"SET LOCAL statement_timeout = {OPS_TIMEOUT_MS}")
            return [dict(r) for r in await conn.fetch(sql, *params)]

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # FH.11 — proyectos
    # ------------------------------------------------------------------

    # F7 (auditoría): `ws_meta.proyectos` (con dueño e índice por dueño) es de las migraciones
    # (0001/0002). Aquí no hay DDL: el `ALTER TABLE … IF NOT EXISTS` de antes bloqueaba la tabla
    # (ACCESS EXCLUSIVE) en cada guardar/listar/abrir y creaba en silencio una tabla sin índice
    # en una BD sin migrar. Sin migrar, la consulta falla diciendo que la tabla no existe.


    async def list_datasets(self, workspace_id: str) -> list[LayerRef]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_LECTURA}")
            filas = await conn.fetch(
                "SELECT layer_ref FROM ws_meta.datasets "
                "WHERE workspace_id = $1 AND expires_at > now() ORDER BY created_at",
                workspace_id,
            )
        return [LayerRef.model_validate_json(r["layer_ref"]) for r in filas]

    async def get(self, workspace_id: str, dataset_id: str) -> LayerRef | None:
        # La app es NOINHERIT: el catálogo se lee asumiendo el rol lector.
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_LECTURA}")
            valor = await conn.fetchval(
                "SELECT layer_ref FROM ws_meta.datasets "
                "WHERE workspace_id = $1 AND id = $2 AND expires_at > now()",
                workspace_id, dataset_id,
            )
        return LayerRef.model_validate_json(valor) if valor else None


    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def purge_expired(self, raiz_export: str | None = None) -> int:
        """Borra los datasets vencidos (tabla + catálogo + su GeoParquet exportado)."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_ESCRITURA}")
            vencidos = await conn.fetch(
                "SELECT id, schema_name, table_name FROM ws_meta.datasets "
                "WHERE expires_at <= now()"
            )
            for v in vencidos:
                await conn.execute(
                    f"DROP TABLE IF EXISTS {_qi(v['schema_name'])}.{_qi(v['table_name'])}"
                )
            await conn.execute("DELETE FROM ws_meta.datasets WHERE expires_at <= now()")
        if raiz_export:
            import os

            for v in vencidos:
                try:
                    os.remove(os.path.join(raiz_export, v["schema_name"], f"{v['id']}.parquet"))
                except FileNotFoundError:
                    pass
        if vencidos:
            logger.info("[workspace] purgados %d datasets vencidos", len(vencidos))
        return len(vencidos)


# ---------------------------------------------------------------------------
# Remotos (feature_ref)
# ---------------------------------------------------------------------------
