"""F6 (S6.1) — lo que el frontend necesita para iniciar sesión y saber quién es.

    GET /api/v1/auth/config   público: cómo se entra (oidc | api_key | ninguno) y, con OIDC,
                              el emisor y el cliente. Una sola fuente de verdad: la misma
                              configuración que valida los tokens.
    GET /api/v1/auth/yo       el usuario de la petición: nombre, organización y rol.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from geo_copilot.api.auth import require_principal
from geo_copilot.core.config import Settings, get_settings
from geo_copilot.platform.identidad.principal import Principal

router = APIRouter(prefix="/auth", tags=["Identidad"])


@router.get("/config")
async def config(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    if settings.oidc_issuer:
        return {"modo": "oidc", "issuer": settings.oidc_issuer.rstrip("/"), "client_id": settings.oidc_client_id}
    return {"modo": "api_key" if settings.api_key is not None else "ninguno"}


@router.get("/yo")
async def yo(principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    return principal.resumen()
