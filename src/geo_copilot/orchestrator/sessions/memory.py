"""
In-memory ``SessionStore`` — comportamiento previo a Fase 6 #11.

Las sesiones viven en un ``dict`` local al proceso. Apto para
desarrollo, tests o despliegues single-worker. Para multi-worker o
persistencia entre reinicios usar :class:`RedisSessionStore`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.sessions.base import SessionStore

if TYPE_CHECKING:
    from geo_copilot.orchestrator.conversation import ConversationContext

logger = get_logger(__name__)


class InMemorySessionStore(SessionStore):
    """Backend basado en un dict del proceso. Same semantics as before."""

    def __init__(self, session_timeout_minutes: int = 60):
        self.session_timeout_minutes = session_timeout_minutes
        self._sessions: dict[str, ConversationContext] = {}

    def _is_expired(self, context: ConversationContext) -> bool:
        elapsed = (datetime.now() - context.updated_at).total_seconds() / 60
        return elapsed > self.session_timeout_minutes

    def get(self, session_id: str) -> ConversationContext | None:
        context = self._sessions.get(session_id)
        if context is None:
            return None
        if self._is_expired(context):
            logger.info(f"Session {session_id} expired")
            del self._sessions[session_id]
            return None
        return context

    def save(self, context: ConversationContext) -> None:
        self._sessions[context.session_id] = context

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"Deleted conversation session: {session_id}")
            return True
        return False

    def exists(self, session_id: str) -> bool:
        context = self._sessions.get(session_id)
        if context is None:
            return False
        if self._is_expired(context):
            del self._sessions[session_id]
            logger.debug(f"Session {session_id} expired during existence check")
            return False
        return True

    def list_summaries(self) -> list[dict[str, Any]]:
        self.cleanup_expired()
        return [ctx.get_summary() for ctx in self._sessions.values()]

    def count(self) -> int:
        self.cleanup_expired()
        return len(self._sessions)

    def cleanup_expired(self) -> int:
        now = datetime.now()
        expired = [
            sid for sid, ctx in self._sessions.items()
            if (now - ctx.updated_at).total_seconds() / 60 > self.session_timeout_minutes
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired sessions")
        return len(expired)
