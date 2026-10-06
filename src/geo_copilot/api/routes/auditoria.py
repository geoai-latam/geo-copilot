"""F6 (S6.4, E6.5) — el registro de auditoría de la organización (solo administración).

    GET /api/v1/auditoria            las últimas entradas de SU organización (paginado por id)
    GET /api/v1/auditoria/verificar  ¿la cadena de hashes está íntegra?

Un administrador ve lo de su organización, nunca lo de otra.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.requests import Request

from geo_copilot.api.auth import requiere_rol
from geo_copilot.api.limiter import limiter
from geo_copilot.platform import auditoria
from geo_copilot.platform.identidad.principal import Principal

router = APIRouter(prefix="/auditoria", tags=["Auditoría"])

_SESION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@router.get("")
@limiter.limit("60/minute")
async def listar(request: Request, limite: int = Query(100, ge=1, le=500),
                 antes_de: int | None = Query(None, ge=1), session_id: str | None = None,
                 principal: Principal = Depends(requiere_rol("admin"))) -> dict[str, Any]:
    if session_id is not None and not _SESION.match(session_id):
        raise HTTPException(status_code=400, detail="session_id inválido")
    filas = await auditoria.auditoria_actual().listar(principal.org_id, limite=limite, antes_de=antes_de,
                                                      session_id=session_id)
    return {"organizacion": principal.org_id, "entradas": filas,
            "siguiente": filas[-1]["id"] if len(filas) == limite else None}


@router.get("/verificar")
@limiter.limit("10/minute")
async def verificar(request: Request, principal: Principal = Depends(requiere_rol("admin"))) -> dict[str, Any]:
    r = await auditoria.auditoria_actual().verificar()
    # la cadena es de toda la instalación: a un admin de una organización solo se le dice si está
    # íntegra (no cuántas filas tienen las demás organizaciones ni cuál se rompió)
    return {"integra": r["integra"], "encadenada": r["encadenada"]}
