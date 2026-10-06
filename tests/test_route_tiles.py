"""Tests del endpoint de teselado vectorial MVT: /api/v1/tiles/{s}/{t}/{z}/{x}/{y}.pbf.

Mockean el pool asyncpg (vía dependency override) para verificar la validación
de identificadores/coordenadas, el allowlist por geometry_columns (404 si la
tabla no es geográfica), y el happy-path que reemite los bytes MVT.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app
from geo_copilot.api.dependencies import get_db_pool

_MVT_BYTES = b"\x1a\x0bgeo_copilot"


class _CM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *_a):
        return False


def _mock_pool(geom_row, cols, tile):
    conn = MagicMock()
    # fetchrow: 1ª llamada = geometry_columns, 2ª = ST_AsMVT.
    conn.fetchrow = AsyncMock(side_effect=[geom_row, {"tile": tile}])
    conn.fetch = AsyncMock(return_value=cols)
    conn.execute = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_CM(None))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_CM(conn))
    return pool


def _client_with_pool(pool):
    app = create_app()
    app.dependency_overrides[get_db_pool] = lambda: pool
    return TestClient(app)


@pytest.fixture
def client():
    return TestClient(create_app())


class TestTileHappyPath:
    def test_reemite_bytes_mvt(self):
        pool = _mock_pool(
            geom_row={"geom": "shape", "srid": 4326},
            cols=[{"column_name": "objectid"}, {"column_name": "lotcodigo"}],
            tile=_MVT_BYTES,
        )
        client = _client_with_pool(pool)
        resp = client.get("/api/v1/tiles/catastro/lotes/12/1170/2020.pbf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.mapbox-vector-tile"
        assert resp.content == _MVT_BYTES

    def test_tesela_vacia_da_204(self):
        pool = _mock_pool(geom_row={"geom": "shape", "srid": 4326}, cols=[], tile=None)
        client = _client_with_pool(pool)
        resp = client.get("/api/v1/tiles/catastro/lotes/12/1170/2020.pbf")
        assert resp.status_code == 204


class TestTileAllowlist:
    def test_tabla_no_geografica_da_404(self):
        # geometry_columns no devuelve fila → 404.
        pool = _mock_pool(geom_row=None, cols=[], tile=None)
        client = _client_with_pool(pool)
        resp = client.get("/api/v1/tiles/public/usuarios/12/1170/2020.pbf")
        assert resp.status_code == 404


class TestTileValidation:
    def test_identificador_invalido_da_400(self, client):
        resp = client.get("/api/v1/tiles/cata-stro/lo;tes/12/1170/2020.pbf")
        assert resp.status_code == 400

    def test_zxy_fuera_de_rango_da_400(self, client):
        # z=3 → max x/y = 8; 9999 está fuera de rango.
        resp = client.get("/api/v1/tiles/catastro/lotes/3/9999/1.pbf")
        assert resp.status_code == 400

    def test_zoom_excesivo_da_400(self, client):
        resp = client.get("/api/v1/tiles/catastro/lotes/40/1/1.pbf")
        assert resp.status_code == 400
