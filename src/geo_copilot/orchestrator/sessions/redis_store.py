"""
Redis-backed ``SessionStore`` — Fase 6 #11.

Permite que el ``ConversationManager`` sobreviva reinicios del proceso
y comparta estado entre múltiples workers (Uvicorn/Gunicorn).

Diseño
------

* **Serialización**: ``ConversationContext.to_dict()`` produce un dict
  con todo el historial, estado y preferencias. Lo guardamos como JSON.
  ``from_dict`` reconstruye el contexto en el otro lado.
* **TTL nativo**: ``EXPIRE session:<id> <ttl>`` — el cleanup es trabajo
  del propio Redis; no necesitamos sweeper de fondo. ``save()`` renueva
  el TTL en cada escritura.
* **Listado**: ``SCAN`` por el patrón ``session:*``. ``KEYS`` se evita
  porque bloquea Redis en bases grandes.
* **Dependencia opcional**: ``redis`` no está en las deps base; se
  añade en pyproject. Si el import falla la factoría debe caer a
  in-memory con un warning, no romper el arranque.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.sessions.base import SessionStore

if TYPE_CHECKING:
    from geo_copilot.orchestrator.conversation import ConversationContext

logger = get_logger(__name__)

_KEY_PREFIX = "geocopilot:session:"

# Errores de un payload corrupto al deserializar: JSON inválido
# (JSONDecodeError ⊂ ValueError) o un dict con forma inesperada para
# ``ConversationContext.from_dict``.
_CORRUPT_PAYLOAD_ERRORS: tuple[type[Exception], ...] = (
    ValueError, TypeError, KeyError, AttributeError,
)


class RedisSessionStore(SessionStore):
    """Backend basado en Redis con TTL nativo."""

    def __init__(self, redis_url: str, session_timeout_minutes: int = 60):
        # Import perezoso para que el módulo sea importable aunque
        # ``redis`` no esté instalado; la factoría decide qué hacer.
        try:
            import redis  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - guarded by factory
            raise ImportError(
                "El paquete 'redis' es necesario para RedisSessionStore. "
                "Instala con: pip install redis"
            ) from exc

        # Errores de red/protocolo de redis-py (ConnectionError, TimeoutError,
        # ResponseError… heredan de RedisError). Se guarda en la instancia
        # porque ``redis`` se importa perezosamente.
        # OSError además de RedisError: redis-py envuelve casi todo, pero un
        # socket roto no debe tumbar cada petición mientras Redis se recupera.
        self._redis_errors: tuple[type[Exception], ...] = (redis.RedisError, OSError)
        self.session_timeout_minutes = session_timeout_minutes
        self._ttl_seconds = session_timeout_minutes * 60
        self._client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{_KEY_PREFIX}{session_id}"

    def _serialize(self, context: ConversationContext) -> str:
        return json.dumps(context.to_dict(), default=str, ensure_ascii=False)

    def _deserialize(self, payload: str | bytes) -> ConversationContext:
        # `get()` se tipa `bytes | str` aunque con decode_responses=True llegue
        # str; json.loads acepta ambos (redis-py 7 tipó esto y mypy lo cazó en CI).
        # Import perezoso aquí también para no crear ciclos durante el
        # arranque del paquete.
        from geo_copilot.orchestrator.conversation import ConversationContext
        return ConversationContext.from_dict(json.loads(payload))

    # ------------------------------------------------------------------
    # SessionStore implementation
    # ------------------------------------------------------------------

    def get(self, session_id: str) -> ConversationContext | None:
        try:
            payload = self._client.get(self._key(session_id))
        except self._redis_errors as exc:
            logger.error(f"Redis GET failed for {session_id}: {exc}")
            return None
        if payload is None:
            return None
        try:
            return self._deserialize(payload)
        except _CORRUPT_PAYLOAD_ERRORS as exc:
            logger.error(f"Failed to deserialize session {session_id}: {exc}")
            # Datos corruptos: limpiar y devolver None — degradado pero
            # nunca rompemos al caller.
            try:
                self._client.delete(self._key(session_id))
            except self._redis_errors:
                # Limpieza oportunista: si Redis falla aquí, la key expira
                # sola por TTL; el caller ya recibe None.
                pass
            return None

    def save(self, context: ConversationContext) -> None:
        try:
            self._client.set(
                self._key(context.session_id),
                self._serialize(context),
                ex=self._ttl_seconds,
            )
        except self._redis_errors as exc:
            logger.error(f"Redis SET failed for {context.session_id}: {exc}")

    def delete(self, session_id: str) -> bool:
        try:
            removed = self._client.delete(self._key(session_id))
            if removed:
                logger.info(f"Deleted conversation session: {session_id}")
            return bool(removed)
        except self._redis_errors as exc:
            logger.error(f"Redis DEL failed for {session_id}: {exc}")
            return False

    def exists(self, session_id: str) -> bool:
        try:
            return bool(self._client.exists(self._key(session_id)))
        except self._redis_errors as exc:
            logger.error(f"Redis EXISTS failed for {session_id}: {exc}")
            return False

    def _iter_keys(self):
        """Iterar las keys ``session:*`` usando SCAN (no KEYS)."""
        try:
            yield from self._client.scan_iter(match=f"{_KEY_PREFIX}*", count=100)
        except self._redis_errors as exc:
            logger.error(f"Redis SCAN failed: {exc}")
            return

    def list_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for key in self._iter_keys():
            payload = self._client.get(key)
            if payload is None:
                continue
            try:
                ctx = self._deserialize(payload)
            except _CORRUPT_PAYLOAD_ERRORS:
                # Payload corrupto: se omite del listado (``get`` lo limpia).
                continue
            summaries.append(ctx.get_summary())
        return summaries

    def count(self) -> int:
        # Aproximación: usar SCAN. No es atómico, pero ``count`` es
        # informativo (lo consume principalmente ``/health``).
        return sum(1 for _ in self._iter_keys())

    def cleanup_expired(self) -> int:
        # Redis expira las keys automáticamente vía ``EX``. No tenemos
        # nada que hacer; devolvemos 0 por compatibilidad de contrato.
        return 0
