"""Quién hace la petición (F6, S6.1).

Un `Principal` es una persona que entró por OIDC, un cliente de servicio con la API key o, en
desarrollo sin autenticación, el principal `dev`. Todo lo que tiene dueño (sesiones, workspace,
proyectos, conexiones) y todo lo que se audita se decide con él.

El principal de la petición en curso vive en una contextvar: el orquestador, el hub MCP y la
auditoría lo leen sin que cada firma tenga que pasarlo.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field

#: Roles de la aplicación, de menos a más. Un rol incluye lo que permiten los anteriores.
ROLES = ("viewer", "analyst", "admin")


@dataclass(frozen=True)
class Principal:
    sub: str
    org_id: str
    roles: frozenset[str] = frozenset()
    nombre: str = ""
    via: str = "oidc"  # oidc | api_key | dev
    # El token de acceso de la persona (para el token exchange hacia MCP). Nunca se registra.
    token: str | None = field(default=None, repr=False, compare=False)

    @property
    def rol(self) -> str | None:
        """El rol más alto que tiene entre los de la aplicación (None: ninguno)."""
        tiene = [r for r in ROLES if r in self.roles]
        return tiene[-1] if tiene else None

    def puede(self, minimo: str) -> bool:
        """¿Su rol alcanza `minimo` (viewer < analyst < admin)?"""
        rol = self.rol
        return rol is not None and ROLES.index(rol) >= ROLES.index(minimo)

    def resumen(self) -> dict[str, object]:
        """Lo que se puede mostrar o auditar de él (sin el token)."""
        return {"sub": self.sub, "org_id": self.org_id, "rol": self.rol, "nombre": self.nombre, "via": self.via}


#: Desarrollo sin autenticación: un único usuario administrador de la organización `dev`.
PRINCIPAL_DEV = Principal(sub="dev", org_id="dev", roles=frozenset({"admin"}), nombre="Desarrollo", via="dev")

_actual: ContextVar[Principal | None] = ContextVar("principal_actual", default=None)


def principal_actual() -> Principal | None:
    return _actual.get()


def fijar_principal(p: Principal | None) -> Token[Principal | None]:
    return _actual.set(p)


def restaurar_principal(token: Token[Principal | None]) -> None:
    _actual.reset(token)
