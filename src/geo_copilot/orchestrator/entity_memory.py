"""EntityMemory (Fase 3 / F3.2) — memoria de resolución de entidades por sesión.

El gap que cierra: cada turno, el ``_preflight_entities`` del GIS Agent re-valida
TODAS las entidades del usuario contra el DataAgent (A2A ``lookup_entity``). Para
una conversación que insiste en "los predios", "el predio", "predios del centro",
se re-resuelve "predios" una y otra vez (latencia + posible inconsistencia).

F3.2 cachea las resoluciones por sesión, pero con **control de confianza** — la
propiedad agéntica clave:

  - Una resolución de ALTA confianza (match exacto de nombre canónico/alias →
    ``exists=True``) se recuerda y se REUTILIZA sin re-verificar.
  - Una resolución de BAJA confianza (fuzzy guess: ``exists=False`` + sugerencias)
    NO se sirve como autoritativa: se re-verifica cada turno. Reutilizar una
    conjetura como si fuera un hecho compondría errores en silencio — justo lo
    que el principio "sin fallbacks adivinadores" del proyecto evita.

Memoria en proceso, por ``session_id``, con tope LRU. No persiste entre reinicios
(el schema introspectado es estable dentro de una sesión); ``invalidate`` permite
descartar una resolución que resultó equivocada.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

# Umbral de confianza para REUTILIZAR una resolución sin re-verificar.
HIGH_CONFIDENCE = 0.9


@dataclass
class EntityResolution:
    """Resolución de una mención de entidad ('predios') a su forma canónica."""

    query: str                       # lo que dijo el usuario
    exists: bool                     # ¿se resolvió a una entidad real?
    confidence: float                # 1.0 exacto · ~0.5 fuzzy · 0.0 desconocido
    canonical: str | None = None     # nombre canónico (o la mejor sugerencia)
    table: str | None = None
    srid: int | None = None
    geometry_type: str | None = None

    @property
    def is_high_confidence(self) -> bool:
        return self.exists and self.confidence >= HIGH_CONFIDENCE

    def to_dict(self) -> dict:
        return {
            "query": self.query, "exists": self.exists, "confidence": self.confidence,
            "canonical": self.canonical, "table": self.table,
            "srid": self.srid, "geometry_type": self.geometry_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EntityResolution:
        return cls(
            query=str(d.get("query", "")), exists=bool(d.get("exists")),
            confidence=float(d.get("confidence", 0.0)),
            canonical=d.get("canonical"), table=d.get("table"),
            srid=d.get("srid"), geometry_type=d.get("geometry_type"),
        )


def resolution_from_lookup(query: str, info: dict) -> EntityResolution:
    """Mapear el dict de ``lookup_entity`` a una ``EntityResolution`` con confianza.

    - ``exists=True`` (match exacto canónico/alias) → confianza 1.0.
    - ``exists=False`` con sugerencias (fuzzy) → 0.5: es una conjetura, no se
      reutiliza como hecho.
    - sin match ni sugerencias → 0.0.
    """
    if info.get("exists"):
        return EntityResolution(
            query=query,
            exists=True,
            confidence=1.0,
            canonical=info.get("canonical_name") or query,
            table=info.get("table"),
            srid=info.get("srid"),
            geometry_type=info.get("geometry_type"),
        )
    suggestions = info.get("suggestions") or []
    return EntityResolution(
        query=query,
        exists=False,
        confidence=0.5 if suggestions else 0.0,
        canonical=(suggestions[0] if suggestions else None),
    )


class EntityMemory:
    """Cache por sesión de resoluciones de entidad, con control de confianza."""

    def __init__(self, max_per_session: int = 256, persist_path: str | None = None):
        self._store: dict[str, OrderedDict[str, EntityResolution]] = {}
        self._max = max(1, int(max_per_session))
        self._persist_path = persist_path
        if persist_path:
            self._load()

    # -- persistencia durable (F49) -----------------------------------------
    def _load(self) -> None:
        from geo_copilot.core.json_store import load_json
        if not self._persist_path:  # solo se llama con ruta
            return
        raw = load_json(self._persist_path)
        for sid, entries in (raw.get("sessions") or {}).items():
            od: OrderedDict[str, EntityResolution] = OrderedDict()
            for key, d in (entries or {}).items():
                od[key] = EntityResolution.from_dict(d)
            self._store[sid] = od

    def _save(self) -> None:
        if not self._persist_path:
            return
        from geo_copilot.core.json_store import save_json
        data = {
            "sessions": {
                sid: {k: r.to_dict() for k, r in entries.items()}
                for sid, entries in self._store.items()
            }
        }
        save_json(self._persist_path, data)

    @staticmethod
    def _key(query: str | None) -> str:
        return (query or "").strip().lower()

    def get(self, session_id: str | None, query: str | None) -> EntityResolution | None:
        """Devuelve la resolución cacheada SOLO si es de alta confianza.

        Una conjetura de baja confianza nunca se sirve como autoritativa: el
        caller debe re-verificarla. ``None`` = no hay nada reutilizable.
        """
        if not session_id:
            return None
        sess = self._store.get(session_id)
        if not sess:
            return None
        res = sess.get(self._key(query))
        if res is None or not res.is_high_confidence:
            return None
        sess.move_to_end(self._key(query))  # LRU touch
        return res

    def remember(self, session_id: str | None, resolution: EntityResolution) -> None:
        """Guarda una resolución (cualquier confianza; ``get`` filtra al leer)."""
        key = self._key(resolution.query)
        if not session_id or not key:
            return
        sess = self._store.setdefault(session_id, OrderedDict())
        if key in sess:
            sess.move_to_end(key)
        sess[key] = resolution
        while len(sess) > self._max:
            sess.popitem(last=False)  # evicción LRU
        self._save()

    def invalidate(self, session_id: str | None, query: str | None) -> None:
        """Descarta una resolución (p. ej. si el SQL con ese canónico falló)."""
        sess = self._store.get(session_id or "")
        if sess:
            sess.pop(self._key(query), None)
            self._save()

    def clear(self, session_id: str | None = None) -> None:
        if session_id is None:
            self._store.clear()
        else:
            self._store.pop(session_id, None)
        self._save()
