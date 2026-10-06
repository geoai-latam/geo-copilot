"""Regresión S9: el arranque no debe quedar "sano" sin LLM.

Antes ``_do_initialize`` tragaba el fallo del cliente LLM, lo dejaba en
None y marcaba ``_initialized=True`` — la app booteaba sirviendo tráfico
roto y el lifespan nunca moría. Ahora, si el componente crítico (LLM)
no se construye, ``initialize`` lanza y ``_initialized`` queda False.
"""

import pytest

from geo_copilot.api import dependencies as deps


@pytest.mark.asyncio
async def test_initialize_raises_when_llm_fails(monkeypatch):
    state = deps.AppState()

    # DB falla rápido (sin red real).
    async def _fail_pool(*args, **kwargs):
        raise RuntimeError("db no disponible")

    monkeypatch.setattr(deps.asyncpg, "create_pool", _fail_pool)

    # LLM no se puede construir → componente crítico ausente.
    def _fail_llm(*args, **kwargs):
        raise RuntimeError("sin credenciales LLM")

    monkeypatch.setattr(deps.LLMClient, "from_settings", _fail_llm)

    with pytest.raises(RuntimeError, match="LLM"):
        await state.initialize()

    # No debe quedar marcado como inicializado (permite reintento).
    assert state.is_initialized is False


@pytest.mark.asyncio
async def test_initialize_raises_when_db_fails_in_production(monkeypatch):
    """En producción (debug=False) un pool de BD ausente aborta el arranque.

    Igual que el LLM (S9): preferimos NO botear a servir tráfico roto con
    db_pool=None y fallar pieza por pieza en runtime."""
    state = deps.AppState()

    # DB falla, pero el LLM se construye OK (objeto dummy) → el guard de BD
    # es el que debe disparar, no el del LLM.
    async def _fail_pool(*args, **kwargs):
        raise RuntimeError("db no disponible")

    monkeypatch.setattr(deps.asyncpg, "create_pool", _fail_pool)
    monkeypatch.setattr(deps.LLMClient, "from_settings", lambda *a, **k: object())

    # Producción.
    settings = deps.get_settings()
    monkeypatch.setattr(settings, "debug", False, raising=False)

    with pytest.raises(RuntimeError, match="base de datos"):
        await state.initialize()

    assert state.is_initialized is False


@pytest.mark.asyncio
async def test_initialize_tolerates_db_failure_in_dev(monkeypatch):
    """En desarrollo/test (debug=True) se permite arrancar sin BD real —
    la suite no levanta PostGIS. El guard de BD NO debe disparar."""
    state = deps.AppState()

    async def _fail_pool(*args, **kwargs):
        raise RuntimeError("db no disponible")

    monkeypatch.setattr(deps.asyncpg, "create_pool", _fail_pool)
    monkeypatch.setattr(deps.LLMClient, "from_settings", lambda *a, **k: object())

    # debug=True viene de conftest (DEBUG=true). Arranca pese a la BD caída.
    await state.initialize()

    assert state.is_initialized is True
    assert state._db_pool is None
