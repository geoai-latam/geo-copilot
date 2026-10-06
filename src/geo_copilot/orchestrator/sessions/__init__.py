"""
Pluggable session storage para ``ConversationManager`` (Fase 6 #11).

Antes ``ConversationManager`` mantenía un ``dict[str, ConversationContext]``
en memoria — lo que rompía persistencia entre reinicios y consistencia
entre múltiples workers (Uvicorn/Gunicorn). Este paquete extrae el
backend a una abstracción intercambiable:

* :class:`InMemorySessionStore` — mantiene el comportamiento previo;
  default en desarrollo.
* :class:`RedisSessionStore` — backed por Redis con TTL nativo
  (``EXPIRE``); apto para multi-worker y reinicios.

La elección la hace la factoría en ``api/dependencies.py`` según
``settings.session_backend``.
"""

from geo_copilot.orchestrator.sessions.base import SessionStore
from geo_copilot.orchestrator.sessions.memory import InMemorySessionStore

# ``RedisSessionStore`` se importa perezosamente para que el paquete
# sea utilizable aunque ``redis`` no esté instalado.
__all__ = ["SessionStore", "InMemorySessionStore", "RedisSessionStore"]


def __getattr__(name: str):
    if name == "RedisSessionStore":
        from geo_copilot.orchestrator.sessions.redis_store import RedisSessionStore
        return RedisSessionStore
    raise AttributeError(name)
