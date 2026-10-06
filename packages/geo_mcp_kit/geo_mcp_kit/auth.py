"""Autenticación de un servidor GeoMCP: API keys con scopes y rate limit.

Extraído de imagery-mcp (S3.1). Genérico: cada servidor declara sus scopes y el
mapa tool → scope; el kit hace cumplir el contrato.

- Bearer sobre streamable HTTP. La clave se compara por sha256 en tiempo
  constante; tras la carga no queda ninguna clave en claro en memoria.
- Fail-closed: una tool ausente del mapa tool → scope se DENIEGA.
- Semántica HTTP: 401 sin/mala clave · 403 sin scope · 429 sobre el límite.

F6 (S6.3): además de las API keys, un servidor puede aceptar el token OIDC de una persona
(`oidc.VerificadorJwt`): mismo mapa tool → scope, con los scopes que den sus roles.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApiKey:
    name: str
    key_hash: str
    scopes: frozenset[str]
    rate_limit_per_min: int = 60
    #: Mapa tool → scope del servicio (lo fija el KeyRing).
    tool_scopes: Mapping[str, str] = field(default_factory=dict)
    #: F6: si la identidad es una PERSONA (token OIDC por token exchange), su sub y su organización.
    sub: str | None = None
    org: str | None = None

    def allows_tool(self, tool_name: str) -> bool:
        """¿Esta clave puede llamar a `tool_name`? Tool fuera del mapa → no (fail-closed)."""
        required = self.tool_scopes.get(tool_name)
        return required is not None and required in self.scopes


class RateBackend(Protocol):
    """Almacén de hits del rate limit (memoria por defecto; Redis en despliegue)."""

    def hits(self, key: str, since: float) -> float: ...
    def add(self, key: str, at: float, weight: float) -> None: ...


class MemoryRateBackend:
    """Ventana deslizante en memoria (un solo proceso)."""

    def __init__(self) -> None:
        self._hits: dict[str, list[tuple[float, float]]] = {}

    def hits(self, key: str, since: float) -> float:
        window = self._hits.setdefault(key, [])
        while window and window[0][0] < since:
            window.pop(0)
        return sum(w for _, w in window)

    def add(self, key: str, at: float, weight: float) -> None:
        self._hits.setdefault(key, []).append((at, weight))


class RateLimiter:
    """Ventana deslizante de 60 s por clave.

    ``weight``: costo fraccionario del hit. Las teselas suelen pesar 0.05 (20
    teselas = 1 petición): un mapa pide decenas por paneo y con peso 1 un solo
    zoom agotaba el presupuesto (visto en navegador con imagery).
    """

    def __init__(self, backend: RateBackend | None = None) -> None:
        self._backend = backend or MemoryRateBackend()

    def allow(self, key: ApiKey, weight: float = 1.0) -> bool:
        now = time.monotonic()
        if self._backend.hits(key.key_hash, now - 60.0) + weight > key.rate_limit_per_min:
            return False
        self._backend.add(key.key_hash, now, weight)
        return True


class KeyRing:
    """Claves válidas del servicio, validadas contra sus scopes conocidos."""

    def __init__(
        self, entries: Iterable[dict], *, known_scopes: Iterable[str], tool_scopes: Mapping[str, str],
    ) -> None:
        conocidos = frozenset(known_scopes)
        fuera = set(tool_scopes.values()) - conocidos
        if fuera:
            raise ValueError(f"el mapa tool→scope usa scopes no declarados: {sorted(fuera)}")
        mapa = dict(tool_scopes)
        self._keys: list[ApiKey] = []
        for e in entries:
            name = str(e.get("name") or "").strip()
            raw = str(e.get("key") or "")
            scopes = frozenset(str(s) for s in (e.get("scopes") or []))
            unknown = scopes - conocidos
            if not name or not raw:
                raise ValueError("Cada clave requiere 'name' y 'key'")
            if unknown:
                raise ValueError(f"Scopes desconocidos en '{name}': {sorted(unknown)}")
            self._keys.append(ApiKey(
                name=name, key_hash=_sha256(raw), scopes=scopes,
                rate_limit_per_min=int(e.get("rate_limit_per_min", 60)),
                tool_scopes=mapa,
            ))

    @classmethod
    def from_json(
        cls, keys_json: str, *, known_scopes: Iterable[str], tool_scopes: Mapping[str, str],
    ) -> KeyRing:
        return cls(json.loads(keys_json), known_scopes=known_scopes, tool_scopes=tool_scopes)

    def __len__(self) -> int:
        return len(self._keys)

    def verify(self, bearer: str | None) -> ApiKey | None:
        """Clave válida para este bearer, o None. Comparación en tiempo constante."""
        if not bearer:
            return None
        candidate = _sha256(bearer)
        for key in self._keys:
            if hmac.compare_digest(candidate, key.key_hash):
                return key
        return None


def extract_bearer(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
