"""Persistencia durable de EntityMemory + AgentMetrics (sobrevive reinicios).

VALIDACIÓN CRÍTICA: el aprendizaje (resoluciones de entidad + métricas cost-aware)
persiste entre INSTANCIAS (simula reinicio del proceso); sin path configurado se
queda en memoria (comportamiento previo intacto); un archivo corrupto no crashea.
"""

from geo_copilot.core.agent_metrics import AgentMetrics
from geo_copilot.core.json_store import load_json, save_json
from geo_copilot.orchestrator.entity_memory import EntityMemory, EntityResolution


# ---------------------------------------------------------------------------
# json_store
# ---------------------------------------------------------------------------
def test_json_store_roundtrip(tmp_path):
    p = tmp_path / "x.json"
    assert save_json(p, {"a": 1, "b": [1, 2]}) is True
    assert load_json(p) == {"a": 1, "b": [1, 2]}


def test_json_store_missing_returns_empty(tmp_path):
    assert load_json(tmp_path / "nope.json") == {}


def test_json_store_corrupt_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_json(p) == {}  # no crashea


def test_json_store_creates_parent_dirs(tmp_path):
    p = tmp_path / "sub" / "deep" / "x.json"
    assert save_json(p, {"ok": True}) is True
    assert load_json(p) == {"ok": True}


# ---------------------------------------------------------------------------
# EntityMemory durable
# ---------------------------------------------------------------------------
def test_entity_memory_persists_across_instances(tmp_path):
    path = str(tmp_path / "em.json")
    m1 = EntityMemory(persist_path=path)
    m1.remember("s1", EntityResolution("predios", True, 1.0, canonical="lotes", table="public.lotes"))

    # "reinicio": nueva instancia lee del disco.
    m2 = EntityMemory(persist_path=path)
    got = m2.get("s1", "predios")
    assert got is not None and got.canonical == "lotes" and got.table == "public.lotes"


def test_entity_memory_invalidate_persists(tmp_path):
    path = str(tmp_path / "em.json")
    m1 = EntityMemory(persist_path=path)
    m1.remember("s1", EntityResolution("predios", True, 1.0, canonical="lotes"))
    m1.invalidate("s1", "predios")
    assert EntityMemory(persist_path=path).get("s1", "predios") is None


def test_entity_memory_no_path_is_in_memory(tmp_path):
    m = EntityMemory()  # sin path
    m.remember("s1", EntityResolution("predios", True, 1.0, canonical="lotes"))
    assert not any(tmp_path.iterdir())  # no escribió ningún archivo


# ---------------------------------------------------------------------------
# AgentMetrics durable
# ---------------------------------------------------------------------------
def test_agent_metrics_persists_across_instances(tmp_path):
    path = str(tmp_path / "am.json")
    a1 = AgentMetrics(persist_path=path)
    a1.record_tool("query_database", True)
    a1.record_tool("query_database", False)
    a1.record_turn(success=True, tokens=1234, n_tools=2)

    a2 = AgentMetrics(persist_path=path)  # "reinicio"
    assert a2.tool_stats()["query_database"] == {"calls": 2, "successes": 1, "success_rate": 0.5}
    s = a2.session_summary()
    assert s["turns"] == 1 and s["total_tokens"] == 1234


def test_agent_metrics_no_path_in_memory(tmp_path):
    a = AgentMetrics()
    a.record_turn(success=True, tokens=10)
    assert not any(tmp_path.iterdir())
