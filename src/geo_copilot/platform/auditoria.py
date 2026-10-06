"""Auditoría: quién ejecutó qué y quién aprobó qué (F6, S6.4).

Cada ejecución de una capacidad (por el agente o desde el mapa), cada consulta, cada decisión
HITL y cada acción de administración queda en `plataforma.auditoria`: una tabla de SOLO AÑADIR
(la app no tiene UPDATE/DELETE/TRUNCATE y un trigger lo impide incluso al dueño del esquema)
cuyas filas se encadenan por hash — borrar o cambiar una fila directamente en la BD rompe la
cadena y `verificar()` lo detecta.

Los argumentos se guardan RESUMIDOS: los textos largos se cortan, las geometrías y estructuras
grandes quedan como su tipo y tamaño, y todo lo que parezca un secreto sale como «***».

Si escribir la auditoría falla, la acción NO se bloquea: se registra un ERROR en el log (el
fallo queda visible). Es una decisión explícita para el piloto; ver docs/SECURITY.md.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from geo_copilot.platform.identidad.principal import Principal, principal_actual

logger = logging.getLogger(__name__)

ROL_PLATAFORMA = "geo_plataforma"
_SECRETO = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|credencial|clave|bearer)", re.I)
_MAX_TEXTO = 300


def resumir(valor: Any, *, profundidad: int = 0) -> Any:
    """Lo auditable de un argumento: corto, sin secretos, sin geometrías enteras."""
    if isinstance(valor, str):
        return valor if len(valor) <= _MAX_TEXTO else valor[:_MAX_TEXTO] + f"… ({len(valor)} caracteres)"
    if valor is None or isinstance(valor, (bool, int, float)):
        return valor
    if isinstance(valor, dict):
        if valor.get("type") in ("FeatureCollection", "Feature", "Polygon", "MultiPolygon", "Point",
                                 "LineString", "MultiPoint", "MultiLineString", "GeometryCollection"):
            n = len(valor.get("features") or []) if valor.get("type") == "FeatureCollection" else None
            return {"geometria": valor["type"], **({"elementos": n} if n is not None else {})}
        if profundidad >= 2:
            return f"{{… {len(valor)} claves}}"
        return {str(k): ("***" if _SECRETO.search(str(k)) else resumir(v, profundidad=profundidad + 1))
                for k, v in list(valor.items())[:30]}
    if isinstance(valor, (list, tuple)):
        if len(valor) > 20 or profundidad >= 2:
            return f"[… {len(valor)} elementos]"
        return [resumir(v, profundidad=profundidad + 1) for v in valor]
    return str(valor)[:_MAX_TEXTO]


@dataclass
class Evento:
    accion: str            # capacidad.ejecutar | consulta | hitl.aprobar | hitl.rechazar | conexion.alta | …
    recurso: str           # la capacidad, la aprobación, la conexión…
    resultado: str         # ok | error | denegado
    session_id: str | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


class AuditoriaStore(Protocol):
    async def escribir(self, principal: Principal, evento: Evento) -> None: ...
    async def listar(self, org_id: str, *, limite: int = 100, antes_de: int | None = None,
                     session_id: str | None = None) -> list[dict[str, Any]]: ...
    async def verificar(self) -> dict[str, Any]: ...


class AuditoriaEnMemoria:
    """Desarrollo sin el esquema y tests (misma interfaz; sin cadena de hash)."""

    def __init__(self) -> None:
        self.filas: list[dict[str, Any]] = []

    async def escribir(self, principal: Principal, evento: Evento) -> None:
        self.filas.append({
            "id": len(self.filas) + 1, "ts": datetime.now(UTC).isoformat(), "org_id": principal.org_id,
            "actor_sub": principal.sub, "actor_nombre": principal.nombre, "actor_via": principal.via,
            "accion": evento.accion, "recurso": evento.recurso, "session_id": evento.session_id,
            "resultado": evento.resultado, "detalle": evento.detalle,
        })

    async def listar(self, org_id: str, *, limite: int = 100, antes_de: int | None = None,
                     session_id: str | None = None) -> list[dict[str, Any]]:
        filas = [f for f in reversed(self.filas) if f["org_id"] == org_id
                 and (antes_de is None or f["id"] < antes_de) and (session_id is None or f["session_id"] == session_id)]
        return filas[:limite]

    async def verificar(self) -> dict[str, Any]:
        return {"integra": True, "filas": len(self.filas), "encadenada": False}


class AuditoriaEnPostgres:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def escribir(self, principal: Principal, evento: Evento) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            await conn.execute(
                "INSERT INTO plataforma.auditoria (org_id, actor_sub, actor_nombre, actor_via, accion, recurso, "
                "session_id, resultado, detalle) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)",
                principal.org_id, principal.sub, principal.nombre[:200], principal.via, evento.accion[:80],
                evento.recurso[:300], evento.session_id, evento.resultado[:40],
                json.dumps(evento.detalle, ensure_ascii=False, default=str),
            )

    async def listar(self, org_id: str, *, limite: int = 100, antes_de: int | None = None,
                     session_id: str | None = None) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            filas = await conn.fetch(
                "SELECT id, ts, actor_sub, actor_nombre, actor_via, accion, recurso, session_id, resultado, detalle "
                "FROM plataforma.auditoria WHERE org_id = $1 AND ($2::bigint IS NULL OR id < $2) "
                "AND ($3::text IS NULL OR session_id = $3) ORDER BY id DESC LIMIT $4",
                org_id, antes_de, session_id, max(1, min(limite, 500)),
            )
        return [{**dict(f), "ts": f["ts"].isoformat(),
                 "detalle": json.loads(f["detalle"]) if isinstance(f["detalle"], str) else f["detalle"]}
                for f in filas]

    async def verificar(self) -> dict[str, Any]:
        """Recalcula la cadena en la BD (misma expresión que el trigger) y dice si está íntegra."""
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL ROLE {ROL_PLATAFORMA}")
            fila = await conn.fetchrow(
                "WITH c AS (SELECT id, hash, hash_prev, coalesce(lag(hash) OVER (ORDER BY id), '') AS prev_real, "
                "encode(sha256(convert_to(hash_prev || '|' || id::text || '|' || "
                "to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '|' || org_id || '|' || "
                "actor_sub || '|' || actor_via || '|' || accion || '|' || recurso || '|' || "
                "coalesce(session_id, '') || '|' || resultado || '|' || detalle::text, 'UTF8')), 'hex') AS calc "
                "FROM plataforma.auditoria) "
                "SELECT count(*) AS filas, count(*) FILTER (WHERE calc <> hash OR prev_real <> hash_prev) AS rotas, "
                "min(id) FILTER (WHERE calc <> hash OR prev_real <> hash_prev) AS primera_rota FROM c")
        return {"integra": fila["rotas"] == 0, "filas": fila["filas"], "encadenada": True,
                **({"primera_rota": fila["primera_rota"]} if fila["rotas"] else {})}


_instalada: AuditoriaStore | None = None
_SISTEMA = Principal(sub="sistema", org_id="sistema", nombre="Sistema", via="sistema")


def instalar(store: AuditoriaStore | None) -> None:
    global _instalada
    _instalada = store


def auditoria_actual() -> AuditoriaStore:
    global _instalada
    if _instalada is None:
        _instalada = AuditoriaEnMemoria()
    return _instalada


async def registrar(accion: str, recurso: str, resultado: str, *, session_id: str | None = None,
                    detalle: dict[str, Any] | None = None, principal: Principal | None = None) -> None:
    """Deja constancia (con el principal de la petición en curso). Nunca lanza."""
    quien = principal or principal_actual() or _SISTEMA
    evento = Evento(accion=accion, recurso=recurso, resultado=resultado, session_id=session_id,
                    detalle=resumir(detalle or {}))
    try:
        await auditoria_actual().escribir(quien, evento)
    except Exception:
        logger.error("[auditoria] NO se pudo registrar %s %s de %s", accion, recurso, quien.sub, exc_info=True)
