"""Identidad (F6): quién hace cada petición, de quién es cada sesión y cómo se abre el WS."""

from geo_copilot.platform.identidad.principal import (
    PRINCIPAL_DEV,
    ROLES,
    Principal,
    fijar_principal,
    principal_actual,
    restaurar_principal,
)

__all__ = ["PRINCIPAL_DEV", "ROLES", "Principal", "fijar_principal", "principal_actual", "restaurar_principal"]
