"""S2.2 — SQL que cruza dominio y workspace, y SOLO el workspace de la sesión.

El nodo SQL fija en una ContextVar las tablas del workspace de la sesión; el
validador AST las suma a la allowlist del semantic layer. Una sesión no puede
leer las tablas de otra aunque adivine el nombre del esquema.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from geo_copilot.agents.gis_agent.sql_validator import SQLValidator
from geo_copilot.platform.contracts import FieldInfo, LayerRef, Provenance, WorkspaceTable
from geo_copilot.platform.workspace import context as wsctx


def _ref(schema: str, table: str, nombre: str = "Lotes previos") -> LayerRef:
    return LayerRef(
        id=f"ds-{table}", name=nombre, kind="vector", provider="core", crs="EPSG:4326",
        geometry_type="Polygon", feature_count=12,
        fields=[FieldInfo(name="lotcodigo", type="string")],
        storage=WorkspaceTable(schema_name=schema, table=table, geometry_column="geom"),
        provenance=Provenance(capability="core.query_database", produced_at=datetime.now(UTC)),
    )


def _validador() -> SQLValidator:
    return SQLValidator(allowed_tables=lambda: {"public.lotes"} | set(wsctx.tablas_de_sesion.get()))


CRUCE = (
    "SELECT l.lotcodigo FROM public.lotes l "
    "JOIN ws_aaaa.d_previos w ON ST_Intersects(l.geom, w.geom)"
)


def test_sin_workspace_la_tabla_del_workspace_se_rechaza():
    wsctx.fijar_datasets([])
    assert _validador().validate(CRUCE)["is_valid"] is False


def test_con_el_dataset_de_la_sesion_el_cruce_se_acepta():
    wsctx.fijar_datasets([_ref("ws_aaaa", "d_previos")])
    res = _validador().validate(CRUCE)
    assert res["is_valid"] is True, res


def test_otra_sesion_no_lee_el_workspace_ajeno():
    wsctx.fijar_datasets([_ref("ws_bbbb", "d_mios")])
    assert _validador().validate(CRUCE)["is_valid"] is False


async def test_las_tareas_concurrentes_no_se_contaminan():
    """Cada petición corre en su tarea: lo que fija una no lo ve la otra."""
    listo = asyncio.Event()

    async def sesion(schema: str) -> frozenset[str]:
        wsctx.fijar_datasets([_ref(schema, "d_x")])
        await listo.wait()
        return wsctx.tablas_de_sesion.get()

    a = asyncio.create_task(sesion("ws_aaaa"))
    b = asyncio.create_task(sesion("ws_bbbb"))
    await asyncio.sleep(0)
    listo.set()
    assert await a == {"ws_aaaa.d_x"} and await b == {"ws_bbbb.d_x"}


def test_bloque_para_llm_da_nombre_calificado_campos_y_geometria():
    txt = wsctx.bloque_para_llm([_ref("ws_aaaa", "d_previos")])
    assert "ws_aaaa.d_previos" in txt and "«Lotes previos»" in txt
    assert "lotcodigo (string)" in txt and "SRID 4326" in txt
    assert wsctx.bloque_para_llm([]) == ""


async def test_el_nodo_fija_solo_los_datasets_de_su_sesion(monkeypatch):
    from geo_copilot.orchestrator.nodes import gis_agent as nodo

    pedidos: list[str] = []

    class Store:
        async def list_datasets(self, ws):
            pedidos.append(ws)
            return [_ref("ws_aaaa", "d_previos")]

    monkeypatch.setattr(wsctx, "_store", Store())
    bloque = await nodo._contexto_workspace("sesion-A")
    assert pedidos == ["sesion-A"]
    assert "ws_aaaa.d_previos" in bloque
    assert wsctx.tablas_de_sesion.get() == {"ws_aaaa.d_previos"}

    # sin sesión se limpia: nada heredado de un turno anterior
    assert await nodo._contexto_workspace("") == ""
    assert wsctx.tablas_de_sesion.get() == frozenset()


async def test_si_el_workspace_falla_el_sql_sigue_sobre_el_dominio(monkeypatch):
    from geo_copilot.orchestrator.nodes import gis_agent as nodo

    class Roto:
        async def list_datasets(self, ws):
            raise RuntimeError("bd caída")

    monkeypatch.setattr(wsctx, "_store", Roto())
    wsctx.tablas_de_sesion.set(frozenset({"ws_viejo.d_x"}))
    assert await nodo._contexto_workspace("sesion-A") == ""
    assert wsctx.tablas_de_sesion.get() == frozenset()


@pytest.mark.integration
@pytest.mark.postgis
@pytest.mark.asyncio(loop_scope="session")
async def test_cruce_real_dominio_workspace(workspace_pool):
    """En PostGIS real, como el lector: dominio (catastro) JOIN workspace de la sesión."""
    import uuid

    from geo_copilot.platform.workspace import DatasetStore

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4()}"
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"nombre": "zona"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]]},
    }]}
    ref = await store.ingest_features(
        ws, "Zona", fc, crs="EPSG:4326",
        provenance=Provenance(capability="core.query_database", produced_at=datetime.now(UTC)),
    )
    async with workspace_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE gis_readonly")
        fila = await conn.fetchrow(
            "SELECT format('%I.%I', f_table_schema, f_table_name) AS t, "
            "quote_ident(f_geometry_column) AS g FROM geometry_columns "
            r"WHERE f_table_schema NOT LIKE 'ws\_%' ORDER BY 1 LIMIT 1"
        )
        assert fila, "la BD de test no tiene tablas geográficas de dominio"
        dominio, gcol = fila["t"], fila["g"]
        n = await conn.fetchval(
            f"SELECT count(*) FROM {dominio} d "
            f'JOIN "{ref.storage.schema_name}"."{ref.storage.table}" w '
            f"ON ST_Intersects(ST_Transform(d.{gcol}, 4326), w.geom)"
        )
    assert n > 0  # la zona cubre el mundo: todo elemento con geometría cruza
