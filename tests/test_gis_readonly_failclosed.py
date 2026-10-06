"""R0.6 (auditoría 2026-07-26, AUD-04) — degradación de privilegios fail-closed.

`_gis_readonly_available` cacheaba `False` de forma PERMANENTE ante cualquier
excepción, incluido un fallo transitorio del pool. A partir de ahí se saltaba el
`SET LOCAL ROLE gis_readonly` y todo el SQL del LLM corría como el usuario de
login — que era SUPERUSUARIO. Verificado contra el contenedor antes del fix:
`rolsuper=t`, y `BEGIN READ ONLY; SELECT pg_read_file('/etc/passwd')` devolvía
el fichero (una transacción READ ONLY no contiene a un superusuario).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.gis_agent import agent as agent_mod
from geo_copilot.agents.gis_agent.agent import (
    GisReadonlyUnavailable,
    _gis_readonly_available,
)


def _pool(fetchval_side_effect=None, fetchval_return=None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect, return_value=fetchval_return)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


@pytest.fixture(autouse=True)
def _limpia_cache():
    agent_mod._GIS_READONLY_OK = None
    yield
    agent_mod._GIS_READONLY_OK = None


class TestFailClosed:
    @pytest.mark.asyncio
    async def test_un_fallo_transitorio_no_degrada_a_privilegios_plenos(self):
        """Antes: cacheaba False y seguía ejecutando como superusuario."""
        with pytest.raises(GisReadonlyUnavailable):
            await _gis_readonly_available(_pool(fetchval_side_effect=OSError("pool caído")))

    @pytest.mark.asyncio
    async def test_un_fallo_transitorio_no_se_cachea(self):
        """El siguiente intento debe poder recuperarse solo."""
        with pytest.raises(GisReadonlyUnavailable):
            await _gis_readonly_available(_pool(fetchval_side_effect=OSError("transitorio")))
        assert agent_mod._GIS_READONLY_OK is None, "un error transitorio NO debe quedar cacheado"

        assert await _gis_readonly_available(_pool(fetchval_return=True)) is True

    @pytest.mark.asyncio
    async def test_el_caso_normal_sigue_funcionando(self):
        assert await _gis_readonly_available(_pool(fetchval_return=True)) is True
        assert agent_mod._GIS_READONLY_OK is True

    @pytest.mark.asyncio
    async def test_rol_inexistente_se_cachea_como_false(self):
        """Es un hecho permanente de la BD, no un fallo transitorio."""
        assert await _gis_readonly_available(_pool(fetchval_return=False)) is False
        assert agent_mod._GIS_READONLY_OK is False
