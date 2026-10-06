"""Regresión C3b-1: _execute_sql devuelve (results, geojson).

execute_query y execute_approved_sql trataban el retorno como una lista:
``row_count = len(tuple)`` daba siempre 2 y la geojson se construía desde
``[list, dict]`` (basura).
"""

from unittest.mock import MagicMock

import pytest

from geo_copilot.agents.gis_agent import GISAgent


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    async def execute(self, sql):
        self.executed.append(sql)

    async def fetch(self, sql):
        return self._rows

    async def fetchval(self, sql):
        # R0.6 (AUD-04): el doble debe responder al chequeo de `gis_readonly`.
        # Antes no lo implementaba y el AttributeError resultante se tragaba en
        # un `except Exception` que cacheaba False y seguía ejecutando con
        # privilegios plenos: estos tests pasaban EJERCITANDO el camino
        # degradado sin notarlo. Ahora el fallo es ruidoso, así que el doble
        # tiene que declarar explícitamente que el rol está disponible.
        return True

    def transaction(self, readonly=False):
        return _Null()


class _Null:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, rows):
        self.conn = _Conn(rows)

    def acquire(self):
        return _Acquire(self.conn)


@pytest.fixture
def rows():
    return [
        {"id": 1, "nombre": "A", "geometry": '{"type":"Point","coordinates":[0,0]}'},
        {"id": 2, "nombre": "B", "geometry": '{"type":"Point","coordinates":[1,1]}'},
        {"id": 3, "nombre": "C", "geometry": '{"type":"Point","coordinates":[2,2]}'},
    ]


@pytest.mark.asyncio
async def test_execute_approved_sql_row_count_is_real(rows):
    gis = GISAgent(
        llm_client=MagicMock(), db_pool=_Pool(rows), db_connection=object()
    )
    # S0.2: con `enforce` por defecto, un GISAgent sin semantic layer tiene
    # la allowlist vacía y rechaza todo (falla cerrado). Este test prueba
    # otra cosa, así que declara las tablas que usa.
    gis.sql_validator.allowed_tables = lambda: {"public.x"}

    result = await gis.execute_approved_sql("SELECT * FROM x LIMIT 3")

    assert result["success"] is True
    # Antes era 2 (len de la tupla). Ahora el conteo real de filas.
    assert result["row_count"] == 3
    assert len(result["results"]) == 3
    # geojson es un FeatureCollection real (no [list, dict]).
    assert result["geojson"]["type"] == "FeatureCollection"
    assert len(result["geojson"]["features"]) == 3


@pytest.mark.asyncio
async def test_execute_query_returns_unpacked_results(rows, monkeypatch):
    # Desactivar HITL para no bloquear en la espera de aprobación.
    import geo_copilot.agents.gis_agent.agent as agent_mod

    monkeypatch.setattr(agent_mod.settings, "hitl_enabled", False)

    gis = GISAgent(
        llm_client=MagicMock(), db_pool=_Pool(rows), db_connection=object()
    )
    # S0.2: con `enforce` por defecto, un GISAgent sin semantic layer tiene
    # la allowlist vacía y rechaza todo (falla cerrado). Este test prueba
    # otra cosa, así que declara las tablas que usa.
    gis.sql_validator.allowed_tables = lambda: {"public.x"}

    result = await gis.execute_query("SELECT * FROM x LIMIT 3")

    assert result["success"] is True
    # results es la lista de filas, no una tupla (list, geojson).
    assert isinstance(result["results"], list)
    assert len(result["results"]) == 3
    assert result["geojson"]["type"] == "FeatureCollection"
