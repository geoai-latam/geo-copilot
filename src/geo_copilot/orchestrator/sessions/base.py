"""
Abstracción ``SessionStore`` — Fase 6 #11.

Encapsula CRUD + expiración de ``ConversationContext`` para que
``ConversationManager`` no sepa si los datos viven en memoria o en
Redis. Las implementaciones concretas viven en este mismo paquete.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from geo_copilot.orchestrator.conversation import ConversationContext


class SessionStore(ABC):
    """Contrato que cualquier backend de sesiones debe cumplir.

    Las implementaciones son síncronas por simplicidad — las operaciones
    son rápidas (GET/SET/DEL en Redis local < 1 ms) y mantenerlas
    síncronas evita teñir todo el ``ConversationManager`` de async.
    """

    @abstractmethod
    def get(self, session_id: str) -> ConversationContext | None:
        """Devolver la sesión si existe y no expiró; None en otro caso.

        La implementación es responsable de borrar (o ignorar) sesiones
        expiradas según su política de TTL.
        """

    @abstractmethod
    def save(self, context: ConversationContext) -> None:
        """Persistir/actualizar la sesión. Renueva el TTL si aplica."""

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        """Eliminar la sesión. Devuelve True si existía, False si no."""

    @abstractmethod
    def exists(self, session_id: str) -> bool:
        """¿Existe la sesión y sigue viva?"""

    @abstractmethod
    def list_summaries(self) -> list[dict[str, Any]]:
        """Listado de resúmenes de todas las sesiones activas.

        Equivalente al antiguo ``[ctx.get_summary() for ctx in sessions]``.
        Puede ser ``O(n)`` y, en el caso de Redis, hacer un ``SCAN``.
        """

    @abstractmethod
    def count(self) -> int:
        """Número de sesiones activas (no expiradas)."""

    @abstractmethod
    def cleanup_expired(self) -> int:
        """Forzar limpieza de expiradas. Devuelve cuántas se removieron."""
