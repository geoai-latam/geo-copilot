"""F3.2: EntityMemory por sesión con control de confianza.

VALIDACIÓN CRÍTICA: solo las resoluciones de ALTA confianza (match exacto) se
reutilizan sin re-verificar; una conjetura fuzzy NUNCA se sirve como hecho
cacheado (se re-resuelve cada turno). Así una adivinanza no se compone en
silencio a lo largo de la conversación.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.orchestrator.entity_memory import (
    HIGH_CONFIDENCE,
    EntityMemory,
    EntityResolution,
    resolution_from_lookup,
)


# ---------------------------------------------------------------------------
# resolution_from_lookup: confianza derivada del tipo de match
# ---------------------------------------------------------------------------
def test_exact_match_is_high_confidence():
    r = resolution_from_lookup("predios", {
        "exists": True, "canonical_name": "lotes", "table": "public.lotes", "srid": 4326,
    })
    assert r.exists and r.confidence == 1.0 and r.is_high_confidence
    assert r.canonical == "lotes" and r.table == "public.lotes"


def test_fuzzy_suggestion_is_low_confidence():
    r = resolution_from_lookup("predois", {"exists": False, "suggestions": ["predios"]})
    assert not r.exists and r.confidence < HIGH_CONFIDENCE
    assert r.is_high_confidence is False
    assert r.canonical == "predios"  # mejor sugerencia, pero NO autoritativa


def test_no_match_zero_confidence():
    r = resolution_from_lookup("xyz", {"exists": False, "suggestions": []})
    assert r.confidence == 0.0 and r.is_high_confidence is False


# ---------------------------------------------------------------------------
# EntityMemory: get() solo sirve alta confianza
# ---------------------------------------------------------------------------
def test_high_confidence_is_reused():
    mem = EntityMemory()
    mem.remember("s1", resolution_from_lookup("predios", {
        "exists": True, "canonical_name": "lotes", "table": "public.lotes"}))
    got = mem.get("s1", "Predios")  # case-insensitive
    assert got is not None and got.canonical == "lotes"


def test_low_confidence_not_served_as_fact():
    mem = EntityMemory()
    mem.remember("s1", resolution_from_lookup("predois", {
        "exists": False, "suggestions": ["predios"]}))
    # Se guardó, pero get() NO la devuelve (debe re-verificarse cada turno).
    assert mem.get("s1", "predois") is None


def test_memory_is_session_scoped():
    mem = EntityMemory()
    mem.remember("s1", EntityResolution("predios", True, 1.0, canonical="lotes"))
    assert mem.get("s2", "predios") is None  # otra sesión no ve la resolución


def test_invalidate_drops_resolution():
    mem = EntityMemory()
    mem.remember("s1", EntityResolution("predios", True, 1.0, canonical="lotes"))
    mem.invalidate("s1", "predios")
    assert mem.get("s1", "predios") is None


def test_lru_eviction_caps_session():
    mem = EntityMemory(max_per_session=2)
    for i in range(3):
        mem.remember("s1", EntityResolution(f"e{i}", True, 1.0, canonical=f"c{i}"))
    assert mem.get("s1", "e0") is None        # evictada (LRU)
    assert mem.get("s1", "e2") is not None


def test_no_session_id_is_noop():
    mem = EntityMemory()
    mem.remember("", EntityResolution("predios", True, 1.0, canonical="lotes"))
    assert mem.get("", "predios") is None
    assert mem.get(None, "predios") is None


# ---------------------------------------------------------------------------
# Integración con _preflight_entities: cache-hit evita el A2A; fuzzy re-verifica
# ---------------------------------------------------------------------------
def _hub_returning(info: dict):
    hub = MagicMock()
    payload = MagicMock()
    payload.to_dict.return_value = info
    hub.call = AsyncMock(return_value=(True, payload))
    return hub


def _graph_with(hub, memory):
    g = MagicMock()
    g.agent_hub = hub
    g.entity_memory = memory
    return g


@pytest.mark.asyncio
async def test_preflight_uses_cache_and_skips_a2a_on_second_turn():
    from geo_copilot.orchestrator.nodes.gis_agent import _preflight_entities

    hub = _hub_returning({"exists": True, "canonical_name": "lotes", "table": "public.lotes"})
    mem = EntityMemory()
    graph = _graph_with(hub, mem)
    state = {"entities": ["predios"], "session_id": "s1"}

    # Turno 1: resuelve vía A2A y memoriza.
    a2a1: list = []
    block1 = await _preflight_entities(graph, state, a2a1)
    assert "lotes" in block1
    assert hub.call.await_count == 1
    assert a2a1[0].get("cached") is not True

    # Turno 2: misma entidad → servida desde memoria, sin nuevo A2A.
    a2a2: list = []
    block2 = await _preflight_entities(graph, state, a2a2)
    assert hub.call.await_count == 1  # NO incrementó
    assert a2a2[0].get("cached") is True
    assert "memoria de sesión" in block2


@pytest.mark.asyncio
async def test_preflight_reverifies_fuzzy_each_turn():
    from geo_copilot.orchestrator.nodes.gis_agent import _preflight_entities

    hub = _hub_returning({"exists": False, "suggestions": ["predios"]})
    mem = EntityMemory()
    graph = _graph_with(hub, mem)
    state = {"entities": ["predois"], "session_id": "s1"}

    await _preflight_entities(graph, state, [])
    await _preflight_entities(graph, state, [])
    # Una conjetura fuzzy NO se cachea como hecho → se re-llama al A2A cada turno.
    assert hub.call.await_count == 2
