"""Aprobaciones HITL que sobreviven a un reinicio y cruzan procesos (F7, S7.1 · E7.1).

Qué se guarda:

- la **solicitud** (lo que el humano ve: SQL, código, llamada) con su sesión y el **turno** que la
  pidió — dura lo que `hitl_timeout` más un margen. Borrarla es **reclamarla**: solo quien la borra
  sigue (el proceso que la espera, o quien la trata como huérfana), así nada se ejecuta dos veces;
- una **concesión** («alguien la está esperando»): el proceso cuyo turno espera la renueva cada
  pocos segundos. Si el proceso muere (reinicio), caduca sola: la aprobación queda **huérfana**;
- la **respuesta** del humano, durable y UNA por solicitud (la primera): el aviso por el bus es
  solo el camino rápido (pub/sub no guarda nada); el que espera la lee también de aquí, y al
  reclamar la solicitud se lleva la respuesta a la vez (ese es el acuse para quien respondió);
- la **pre-aprobación** de un turno: la respuesta que el humano dio cuando ya nadie la esperaba,
  con su tipo, su ordinal dentro del turno y la huella de lo que VIO. Solo la consume el turno
  reanudado (mismo id de turno) en la MISMA solicitud (mismo tipo y ordinal), y solo vale si
  pide exactamente eso; si pide otra cosa, se le vuelve a preguntar. Vale una sola vez;
- cada **turno en curso** por su id (la consulta y quién la hizo), para poder reanudarlo, y el
  índice de turnos en curso de cada sesión.
"""
from __future__ import annotations

import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Cada cuánto renueva la concesión el proceso que espera, y cuándo caduca sin renovar.
RENOVAR_S = 5.0
CONCESION_S = 15
#: Lo que se conserva una solicitud o un turno más allá del tiempo de espera HITL.
MARGEN_S = 3600


# --------------------------------------------------------------------------- turno en curso
@dataclass
class TurnoActivo:
    """El turno que corre en este contexto (y en las tareas que lanza). F7 (auditoría): la
    pre-aprobación se liga a ÉL, no a la sesión — otra consulta de la sesión no la consume."""

    id: str
    _cuenta: dict[str, int] = field(default_factory=dict)

    def siguiente(self, tipo: str) -> int:
        """Ordinal (1, 2…) de la próxima solicitud de ese tipo en el turno."""
        self._cuenta[tipo] = self._cuenta.get(tipo, 0) + 1
        return self._cuenta[tipo]


_turno: ContextVar[TurnoActivo | None] = ContextVar("geo_turno", default=None)


def fijar_turno(turno_id: str | None) -> Any:
    """Marca el turno del contexto actual (las tareas creadas después lo heredan)."""
    return _turno.set(TurnoActivo(turno_id) if turno_id else None)


def soltar_turno(token: Any) -> None:
    _turno.reset(token)


def turno_actual() -> TurnoActivo | None:
    return _turno.get()


class AlmacenAprobaciones(Protocol):
    async def guardar(self, solicitud: dict[str, Any], ttl_s: int) -> None: ...
    async def obtener(self, id_: str) -> dict[str, Any] | None: ...
    async def pendientes(self, session_id: str) -> list[dict[str, Any]]: ...
    async def resolver(self, id_: str) -> bool: ...
    async def renovar(self, id_: str) -> None: ...
    async def soltar(self, id_: str) -> None: ...
    async def esperada(self, id_: str) -> bool: ...
    async def responder(self, id_: str, respuesta: dict[str, Any], ttl_s: int) -> bool: ...
    async def respuesta(self, id_: str) -> dict[str, Any] | None: ...
    async def descartar_respuesta(self, id_: str) -> None: ...
    async def reclamar(self, id_: str) -> tuple[bool, dict[str, Any] | None]: ...
    async def preaprobar(self, turno_id: str, dato: dict[str, Any], ttl_s: int) -> None: ...
    async def tomar_preaprobacion(self, turno_id: str, tipo: str, ordinal: int) -> dict[str, Any] | None: ...
    async def descartar_preaprobaciones(self, turno_id: str) -> None: ...
    async def guardar_turno(self, turno_id: str, session_id: str, turno: dict[str, Any], ttl_s: int) -> None: ...
    async def tomar_turno(self, turno_id: str) -> dict[str, Any] | None: ...
    async def cerrar_turno(self, turno_id: str) -> None: ...
    async def turnos_en_curso(self, session_id: str) -> list[str]: ...


def _clave_pre(tipo: str, ordinal: int) -> str:
    return f"{tipo}:{ordinal}"


class AprobacionesEnMemoria:
    """Un solo proceso (desarrollo, tests): nada sobrevive a un reinicio."""

    def __init__(self) -> None:
        self._sol: dict[str, tuple[dict[str, Any], float]] = {}
        self._concesion: dict[str, float] = {}
        self._resp: dict[str, tuple[dict[str, Any], float]] = {}
        self._pre: dict[str, tuple[dict[str, dict[str, Any]], float]] = {}
        self._turnos: dict[str, tuple[dict[str, Any], str, float]] = {}

    @staticmethod
    def _vigente(hasta: float) -> bool:
        return hasta > time.monotonic()

    async def guardar(self, solicitud: dict[str, Any], ttl_s: int) -> None:
        self._sol[solicitud["id"]] = (solicitud, time.monotonic() + ttl_s)

    async def obtener(self, id_: str) -> dict[str, Any] | None:
        dato = self._sol.get(id_)
        return dato[0] if dato and self._vigente(dato[1]) else None

    async def pendientes(self, session_id: str) -> list[dict[str, Any]]:
        return [s for s, hasta in self._sol.values() if s.get("session_id") == session_id and self._vigente(hasta)]

    async def resolver(self, id_: str) -> bool:
        dato = self._sol.pop(id_, None)
        self._concesion.pop(id_, None)
        return dato is not None and self._vigente(dato[1])

    async def renovar(self, id_: str) -> None:
        self._concesion[id_] = time.monotonic() + CONCESION_S

    async def soltar(self, id_: str) -> None:
        self._concesion.pop(id_, None)

    async def esperada(self, id_: str) -> bool:
        return self._vigente(self._concesion.get(id_, 0.0))

    async def responder(self, id_: str, respuesta: dict[str, Any], ttl_s: int) -> bool:
        if await self.respuesta(id_) is not None:
            return False
        self._resp[id_] = (respuesta, time.monotonic() + ttl_s)
        return True

    async def respuesta(self, id_: str) -> dict[str, Any] | None:
        dato = self._resp.get(id_)
        return dato[0] if dato and self._vigente(dato[1]) else None

    async def descartar_respuesta(self, id_: str) -> None:
        self._resp.pop(id_, None)

    async def reclamar(self, id_: str) -> tuple[bool, dict[str, Any] | None]:
        respuesta = await self.respuesta(id_)
        self._resp.pop(id_, None)
        return await self.resolver(id_), respuesta

    async def preaprobar(self, turno_id: str, dato: dict[str, Any], ttl_s: int) -> None:
        previas = self._pre.get(turno_id, ({}, 0.0))[0]
        previas[_clave_pre(dato["tipo"], int(dato["ordinal"]))] = dato
        self._pre[turno_id] = (previas, time.monotonic() + ttl_s)

    async def tomar_preaprobacion(self, turno_id: str, tipo: str, ordinal: int) -> dict[str, Any] | None:
        dato = self._pre.get(turno_id)
        if not dato or not self._vigente(dato[1]):
            return None
        return dato[0].pop(_clave_pre(tipo, ordinal), None)

    async def descartar_preaprobaciones(self, turno_id: str) -> None:
        self._pre.pop(turno_id, None)

    async def guardar_turno(self, turno_id: str, session_id: str, turno: dict[str, Any], ttl_s: int) -> None:
        self._turnos[turno_id] = (turno, session_id, time.monotonic() + ttl_s)

    async def tomar_turno(self, turno_id: str) -> dict[str, Any] | None:
        dato = self._turnos.pop(turno_id, None)
        return dato[0] if dato and self._vigente(dato[2]) else None

    async def cerrar_turno(self, turno_id: str) -> None:
        self._turnos.pop(turno_id, None)

    async def turnos_en_curso(self, session_id: str) -> list[str]:
        return [t for t, (_d, s, hasta) in self._turnos.items() if s == session_id and self._vigente(hasta)]


class AprobacionesEnRedis:
    """Varios procesos y reinicios: Redis (claves con TTL; nada queda para siempre)."""

    P = "geo:hitl:"

    def __init__(self, cliente: Any) -> None:
        self._r = cliente

    async def guardar(self, solicitud: dict[str, Any], ttl_s: int) -> None:
        id_, sesion = solicitud["id"], solicitud.get("session_id") or ""
        pipe = self._r.pipeline()
        pipe.set(f"{self.P}sol:{id_}", json.dumps(solicitud, default=str), ex=ttl_s)
        if sesion:
            pipe.sadd(f"{self.P}ses:{sesion}", id_)
            pipe.expire(f"{self.P}ses:{sesion}", ttl_s)
        await pipe.execute()

    async def obtener(self, id_: str) -> dict[str, Any] | None:
        crudo = await self._r.get(f"{self.P}sol:{id_}")
        return json.loads(crudo) if crudo else None

    async def pendientes(self, session_id: str) -> list[dict[str, Any]]:
        ids = await self._r.smembers(f"{self.P}ses:{session_id}")
        salida = []
        for id_ in ids or []:
            id_ = id_.decode() if isinstance(id_, bytes) else id_
            if (sol := await self.obtener(id_)) is not None:
                salida.append(sol)
            else:
                await self._r.srem(f"{self.P}ses:{session_id}", id_)
        return sorted(salida, key=lambda s: str(s.get("created_at") or ""))

    async def resolver(self, id_: str) -> bool:
        sol = await self.obtener(id_)
        pipe = self._r.pipeline()
        # F7 (auditoría): el DEL de la solicitud es la reclamación — devuelve 1 a UN solo proceso
        pipe.delete(f"{self.P}sol:{id_}")
        pipe.delete(f"{self.P}vivo:{id_}")
        if sol and sol.get("session_id"):
            pipe.srem(f"{self.P}ses:{sol['session_id']}", id_)
        return bool((await pipe.execute())[0])

    async def renovar(self, id_: str) -> None:
        await self._r.set(f"{self.P}vivo:{id_}", "1", ex=CONCESION_S)

    async def soltar(self, id_: str) -> None:
        await self._r.delete(f"{self.P}vivo:{id_}")

    async def esperada(self, id_: str) -> bool:
        return bool(await self._r.exists(f"{self.P}vivo:{id_}"))

    async def responder(self, id_: str, respuesta: dict[str, Any], ttl_s: int) -> bool:
        # NX: solo cuenta la primera respuesta (doble clic, o dos réplicas a la vez)
        return bool(await self._r.set(f"{self.P}resp:{id_}", json.dumps(respuesta, default=str), ex=ttl_s, nx=True))

    async def respuesta(self, id_: str) -> dict[str, Any] | None:
        crudo = await self._r.get(f"{self.P}resp:{id_}")
        return json.loads(crudo) if crudo else None

    async def descartar_respuesta(self, id_: str) -> None:
        await self._r.delete(f"{self.P}resp:{id_}")

    async def reclamar(self, id_: str) -> tuple[bool, dict[str, Any] | None]:
        """El que espera toma la solicitud y su respuesta guardada a la vez (MULTI/EXEC): quien
        respondió desde otro proceso ve desaparecer las dos — o solo la solicitud, si el que
        esperaba terminó sin aplicarla."""
        sol = await self.obtener(id_)
        pipe = self._r.pipeline(transaction=True)
        pipe.get(f"{self.P}resp:{id_}")
        pipe.delete(f"{self.P}sol:{id_}")
        pipe.delete(f"{self.P}vivo:{id_}")
        pipe.delete(f"{self.P}resp:{id_}")
        if sol and sol.get("session_id"):
            pipe.srem(f"{self.P}ses:{sol['session_id']}", id_)
        crudo, borradas = (await pipe.execute())[:2]
        return bool(borradas), (json.loads(crudo) if crudo else None)

    async def preaprobar(self, turno_id: str, dato: dict[str, Any], ttl_s: int) -> None:
        clave = f"{self.P}pre:{turno_id}"
        pipe = self._r.pipeline()
        pipe.hset(clave, _clave_pre(dato["tipo"], int(dato["ordinal"])), json.dumps(dato, default=str))
        pipe.expire(clave, ttl_s)
        await pipe.execute()

    async def tomar_preaprobacion(self, turno_id: str, tipo: str, ordinal: int) -> dict[str, Any] | None:
        clave, campo = f"{self.P}pre:{turno_id}", _clave_pre(tipo, ordinal)
        crudo = await self._r.hget(clave, campo)
        if not crudo:
            return None
        # HDEL devuelve cuántos borró: si otro proceso la tomó antes, no vale dos veces
        return json.loads(crudo) if await self._r.hdel(clave, campo) else None

    async def descartar_preaprobaciones(self, turno_id: str) -> None:
        await self._r.delete(f"{self.P}pre:{turno_id}")

    async def guardar_turno(self, turno_id: str, session_id: str, turno: dict[str, Any], ttl_s: int) -> None:
        pipe = self._r.pipeline()
        pipe.set(f"{self.P}turno:{turno_id}", json.dumps(turno, default=str), ex=ttl_s)
        pipe.sadd(f"{self.P}turnos:{session_id}", turno_id)
        pipe.expire(f"{self.P}turnos:{session_id}", ttl_s)
        await pipe.execute()

    async def tomar_turno(self, turno_id: str) -> dict[str, Any] | None:
        # GETDEL: lo lee y lo borra a la vez — se reanuda UNA vez aunque respondan dos réplicas
        crudo = await self._r.getdel(f"{self.P}turno:{turno_id}")
        return json.loads(crudo) if crudo else None

    async def cerrar_turno(self, turno_id: str) -> None:
        await self._r.delete(f"{self.P}turno:{turno_id}")  # el índice de la sesión se limpia al leerlo

    async def turnos_en_curso(self, session_id: str) -> list[str]:
        indice = f"{self.P}turnos:{session_id}"
        vivos = []
        for id_ in await self._r.smembers(indice) or []:
            id_ = id_.decode() if isinstance(id_, bytes) else id_
            if await self._r.exists(f"{self.P}turno:{id_}"):
                vivos.append(id_)
            else:
                await self._r.srem(indice, id_)
        return sorted(vivos)


_almacen: AlmacenAprobaciones = AprobacionesEnMemoria()


def instalar(almacen: AlmacenAprobaciones) -> None:
    global _almacen
    _almacen = almacen


def almacen() -> AlmacenAprobaciones:
    return _almacen
