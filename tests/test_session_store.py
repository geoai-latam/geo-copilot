"""
Tests for the pluggable session store layer (Fase 6 #11).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from geo_copilot.orchestrator.conversation import (
    ConversationContext,
    ConversationManager,
    MessageRole,
)
from geo_copilot.orchestrator.sessions import InMemorySessionStore, SessionStore

# ---------------------------------------------------------------------------
# InMemorySessionStore — comportamiento histórico preservado
# ---------------------------------------------------------------------------


class TestInMemorySessionStore:
    def test_save_and_get_roundtrip(self) -> None:
        store = InMemorySessionStore(session_timeout_minutes=60)
        ctx = ConversationContext(session_id="s1")
        ctx.add_user_message("hola")
        store.save(ctx)

        loaded = store.get("s1")
        assert loaded is ctx  # in-memory: misma referencia
        assert loaded is not None
        assert loaded.history[0].content == "hola"

    def test_get_missing_returns_none(self) -> None:
        assert InMemorySessionStore().get("does-not-exist") is None

    def test_delete_returns_true_only_when_existed(self) -> None:
        store = InMemorySessionStore()
        store.save(ConversationContext(session_id="s2"))
        assert store.delete("s2") is True
        assert store.delete("s2") is False

    def test_exists_reflects_state(self) -> None:
        store = InMemorySessionStore()
        assert store.exists("s3") is False
        store.save(ConversationContext(session_id="s3"))
        assert store.exists("s3") is True

    def test_count_and_list_summaries(self) -> None:
        store = InMemorySessionStore()
        store.save(ConversationContext(session_id="a"))
        store.save(ConversationContext(session_id="b"))
        assert store.count() == 2
        summaries = store.list_summaries()
        ids = {s["session_id"] for s in summaries}
        assert ids == {"a", "b"}

    def test_expired_sessions_are_pruned_on_get(self) -> None:
        # 0 minutos de timeout efectivo: cualquier sesión cuenta como expirada
        # apenas pasa un instante. Para evitar acoplar al wall-clock, mutamos
        # ``updated_at`` directamente.
        from datetime import datetime, timedelta

        store = InMemorySessionStore(session_timeout_minutes=10)
        ctx = ConversationContext(session_id="old")
        store.save(ctx)
        ctx.updated_at = datetime.now() - timedelta(minutes=15)

        assert store.get("old") is None
        assert store.exists("old") is False
        assert "old" not in {s["session_id"] for s in store.list_summaries()}


# ---------------------------------------------------------------------------
# ConversationManager delega correctamente al store
# ---------------------------------------------------------------------------


class TestConversationManagerWithStore:
    def test_default_backend_is_in_memory(self) -> None:
        mgr = ConversationManager()
        assert isinstance(mgr.store, InMemorySessionStore)

    def test_custom_store_is_honored(self) -> None:
        mock_store = MagicMock(spec=SessionStore)
        mock_store.count.return_value = 0
        mgr = ConversationManager(store=mock_store)

        ctx = mgr.create_session("sid-1")

        mock_store.save.assert_called_once()
        saved_ctx = mock_store.save.call_args[0][0]
        assert saved_ctx is ctx
        assert ctx.session_id == "sid-1"

    def test_get_or_create_uses_store(self) -> None:
        mock_store = MagicMock(spec=SessionStore)
        mock_store.get.return_value = None
        mock_store.count.return_value = 0
        mgr = ConversationManager(store=mock_store)

        ctx = mgr.get_or_create_session("sid-2")

        assert mock_store.get.called
        assert mock_store.save.called
        assert ctx.session_id == "sid-2"

    def test_delete_and_exists_propagate(self) -> None:
        mock_store = MagicMock(spec=SessionStore)
        mock_store.delete.return_value = True
        mock_store.exists.return_value = True
        mgr = ConversationManager(store=mock_store)

        assert mgr.delete_session("x") is True
        assert mgr.session_exists("x") is True
        mock_store.delete.assert_called_once_with("x")
        mock_store.exists.assert_called_once_with("x")


# ---------------------------------------------------------------------------
# Serialización roundtrip (necesaria para Redis)
# ---------------------------------------------------------------------------


class TestConversationContextSerialization:
    def test_to_dict_from_dict_roundtrip(self) -> None:
        ctx = ConversationContext(session_id="roundtrip")
        ctx.add_user_message("primero", origin="ws")
        ctx.add_assistant_message("respuesta")
        ctx.update_state(analysis_type="search", last_sql="SELECT 1")
        ctx.set_variable("external_geojson", {"type": "FeatureCollection", "features": []})

        payload = ctx.to_dict()
        restored = ConversationContext.from_dict(payload)

        assert restored.session_id == "roundtrip"
        assert len(restored.history) == 2
        assert restored.history[0].role == MessageRole.USER
        assert restored.history[0].content == "primero"
        assert restored.history[0].metadata == {"origin": "ws"}
        assert restored.history[1].content == "respuesta"
        assert restored.state.analysis_type == "search"
        assert restored.state.last_sql == "SELECT 1"
        assert restored.get_variable("external_geojson") == {
            "type": "FeatureCollection",
            "features": [],
        }

    def test_from_dict_tolerates_partial_payload(self) -> None:
        ctx = ConversationContext.from_dict({"session_id": "partial"})
        assert ctx.session_id == "partial"
        assert ctx.history == []
        assert ctx.state.analysis_type is None


# ---------------------------------------------------------------------------
# RedisSessionStore — mocked, no Redis real disponible
# ---------------------------------------------------------------------------


class TestRedisSessionStoreMocked:
    @pytest.fixture
    def fake_redis(self):
        """Cliente Redis sintético soportado por ``RedisSessionStore``."""
        client = MagicMock()
        store_dict: dict[str, str] = {}

        def _get(key: str):
            return store_dict.get(key)

        def _set(key: str, value: str, ex: int | None = None):
            store_dict[key] = value
            return True

        def _delete(*keys: str):
            removed = 0
            for k in keys:
                if k in store_dict:
                    del store_dict[k]
                    removed += 1
            return removed

        def _exists(key: str):
            return 1 if key in store_dict else 0

        def _scan_iter(match: str, count: int = 100):
            import fnmatch
            for k in list(store_dict.keys()):
                if fnmatch.fnmatch(k, match):
                    yield k

        client.get.side_effect = _get
        client.set.side_effect = _set
        client.delete.side_effect = _delete
        client.exists.side_effect = _exists
        client.scan_iter.side_effect = _scan_iter
        client.ping.return_value = True
        return client, store_dict

    def test_save_get_roundtrip_via_redis(self, fake_redis) -> None:
        client, _ = fake_redis
        with patch("redis.Redis.from_url", return_value=client):
            from geo_copilot.orchestrator.sessions import RedisSessionStore
            store = RedisSessionStore("redis://localhost:6379/0", session_timeout_minutes=30)

        ctx = ConversationContext(session_id="r1")
        ctx.add_user_message("hello")
        ctx.update_state(last_sql="SELECT 1")
        store.save(ctx)

        # SET fue llamado con un TTL en segundos = minutos * 60
        kwargs = client.set.call_args.kwargs
        assert kwargs.get("ex") == 30 * 60

        loaded = store.get("r1")
        assert loaded is not None
        # No es la misma referencia (deserializado), pero el contenido sí.
        assert loaded is not ctx
        assert loaded.session_id == "r1"
        assert loaded.history[0].content == "hello"
        assert loaded.state.last_sql == "SELECT 1"

    def test_get_missing_returns_none(self, fake_redis) -> None:
        client, _ = fake_redis
        with patch("redis.Redis.from_url", return_value=client):
            from geo_copilot.orchestrator.sessions import RedisSessionStore
            store = RedisSessionStore("redis://x", session_timeout_minutes=10)

        assert store.get("nope") is None

    def test_corrupt_payload_is_swept(self, fake_redis) -> None:
        """Datos rotos en Redis no deben tumbar a callers."""
        client, store_dict = fake_redis
        with patch("redis.Redis.from_url", return_value=client):
            from geo_copilot.orchestrator.sessions import RedisSessionStore
            store = RedisSessionStore("redis://x", session_timeout_minutes=10)

        store_dict["geocopilot:session:bad"] = "{ not json"
        assert store.get("bad") is None
        # La key corrupta se borra automáticamente.
        assert "geocopilot:session:bad" not in store_dict

    def test_list_summaries_via_scan(self, fake_redis) -> None:
        client, _ = fake_redis
        with patch("redis.Redis.from_url", return_value=client):
            from geo_copilot.orchestrator.sessions import RedisSessionStore
            store = RedisSessionStore("redis://x", session_timeout_minutes=10)

        store.save(ConversationContext(session_id="a"))
        store.save(ConversationContext(session_id="b"))

        summaries = store.list_summaries()
        ids = {s["session_id"] for s in summaries}
        assert ids == {"a", "b"}

    def test_redis_unavailable_falls_back_to_memory(self) -> None:
        """La factoría de dependencies.py cae a in-memory si ping falla."""
        from geo_copilot.api.dependencies import _build_session_store
        from geo_copilot.core.config import get_settings

        config = get_settings().model_copy(update={
            "session_backend": "redis",
            "redis_url": "redis://nonexistent-host:1/0",
        })

        # No mockeamos: simplemente confiamos en que el ping falla
        # contra un host inexistente. La factoría debe devolver
        # in-memory en vez de propagar el error.
        store = _build_session_store(config)
        assert isinstance(store, InMemorySessionStore)
