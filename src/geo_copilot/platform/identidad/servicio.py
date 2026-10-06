"""Las piezas de identidad de la app, construidas una vez desde la configuración (F6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from geo_copilot.platform.identidad.oidc import ValidadorOIDC
from geo_copilot.platform.identidad.propiedad import (
    PropiedadEnMemoria,
    PropiedadEnPostgres,
    PropiedadStore,
)
from geo_copilot.platform.identidad.tickets import TicketsEnMemoria, TicketsEnRedis, TicketStore

__all__ = ["Identidad", "PropiedadEnMemoria", "PropiedadEnPostgres", "construir", "esquema_listo",
           "identidad_actual", "instalar"]

logger = logging.getLogger(__name__)


@dataclass
class Identidad:
    validador: ValidadorOIDC | None
    tickets: TicketStore
    propiedad: PropiedadStore
    ticket_ttl_s: int = 30


def construir(settings: Any, pool: Any = None) -> Identidad:
    validador = None
    if getattr(settings, "oidc_issuer", None):
        validador = ValidadorOIDC(
            issuer=settings.oidc_issuer, audience=settings.oidc_audience, jwks_url=settings.oidc_jwks_url,
            org_claim=settings.oidc_org_claim, roles_claim=settings.oidc_roles_claim,
        )
        logger.info("[identidad] OIDC activo: emisor %s, audiencia %s", settings.oidc_issuer, settings.oidc_audience)
    tickets: TicketStore = TicketsEnMemoria()
    if str(getattr(settings, "session_backend", "")).lower() == "redis":
        try:
            tickets = TicketsEnRedis(str(settings.redis_url))
        except Exception:  # sin el paquete redis: tickets en el proceso (un solo worker)
            logger.warning("[identidad] tickets del WS en memoria: Redis no disponible", exc_info=True)
    propiedad: PropiedadStore = PropiedadEnPostgres(pool) if pool is not None else PropiedadEnMemoria()
    return Identidad(validador=validador, tickets=tickets, propiedad=propiedad,
                     ticket_ttl_s=int(getattr(settings, "ws_ticket_ttl_s", 30)))


async def esquema_listo(pool: Any) -> bool:
    """¿Existe el esquema `plataforma` (docker/init-db/10_plataforma.sql) y la app puede usarlo?"""
    try:
        async with pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute("SET LOCAL ROLE geo_plataforma")
            return bool(await conn.fetchval("SELECT to_regclass('plataforma.sesiones') IS NOT NULL"))
    except Exception:  # rol o esquema ausentes: lo decide quien llama (prod aborta)
        logger.debug("[identidad] esquema plataforma no disponible", exc_info=True)
        return False


_instalada: Identidad | None = None


def instalar(identidad: Identidad | None) -> None:
    global _instalada
    _instalada = identidad


def identidad_actual() -> Identidad:
    """La instalada al arrancar; sin ella (tests con TestClient), una desde la configuración."""
    global _instalada
    if _instalada is None:
        from geo_copilot.core.config import get_settings

        _instalada = construir(get_settings())
    return _instalada
