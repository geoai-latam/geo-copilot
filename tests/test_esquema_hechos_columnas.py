"""F5 (V5): el esquema que ve el generador de SQL lleva los HECHOS de columnas y tablas.

«lotes del catastro de Bogotá» → el generador filtró `lotdistrit = 11001` (y luego `= 1`) y la
respuesta fue 0 lotes donde había 3. Ahora cada columna con pocos valores dice cuáles toma
(estadísticas del planificador: no se lee la tabla) y cada tabla su comentario (su alcance).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from geo_copilot.agents.gis_agent.agent import GISAgent

COLUMNAS = [
    {"table_schema": "catastro", "table_name": "lotes", "column_name": "lotcodigo", "udt_name": "varchar", "geometry_type": None},
    {"table_schema": "catastro", "table_name": "lotes", "column_name": "lotdistrit", "udt_name": "int4", "geometry_type": None},
    {"table_schema": "catastro", "table_name": "lotes", "column_name": "shape", "udt_name": "geometry", "geometry_type": "GEOMETRY"},
]


class _Con:
    async def fetch(self, sql, *args):
        if "information_schema.columns" in sql:
            return COLUMNAS
        if "DISTINCT srid" in sql:
            return [{"srid": 4326}]
        if "pg_stats" in sql:
            return [{"schemaname": "catastro", "tablename": "lotes", "attname": "lotdistrit", "n_distinct": 2,
                     "vals": "{0,1}"}]
        if "col_description" in sql:
            return []
        if "obj_description" in sql:
            return [{"t": "catastro.lotes", "com": "Lotes catastrales de Bogotá D.C.: TODOS los registros son de Bogotá."}]
        raise AssertionError(sql)


class _Pool:
    @asynccontextmanager
    async def acquire(self):
        yield _Con()


@pytest.mark.asyncio
async def test_el_esquema_dice_los_valores_de_las_columnas_y_el_alcance_de_la_tabla():
    agente = GISAgent.__new__(GISAgent)
    agente.db_pool = _Pool()
    esquema = await agente.get_db_schema()
    assert "TABLA catastro.lotes:" in esquema
    assert "Descripción: Lotes catastrales de Bogotá D.C.: TODOS los registros son de Bogotá." in esquema
    assert '"lotdistrit" (int4; valores: 0,1)' in esquema
    assert '"lotcodigo" (varchar)' in esquema  # sin hechos, la columna queda igual
