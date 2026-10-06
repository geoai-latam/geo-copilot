"""FH.11 — proyectos: guardar y reabrir el mapa con su conversación.

    GET  /api/v1/proyectos                 los proyectos guardados (más recientes primero)
    POST /api/v1/proyectos                 guardar (o reemplazar, por nombre) el de una sesión
    POST /api/v1/proyectos/{id}/abrir      su estado + la sesión lista para seguir conversando

Un proyecto = capas (con estilo, filtro, selección, fecha), vistas, cámara, conversación y
registro, persistido en el workspace (`ws_meta.proyectos`); sus datasets dejan de vencer.
Reabrirlo reconstruye la sesión de conversación con ese id (si ya no existía) y su historial:
el agente recuerda. F6: cada proyecto es de quien lo guardó; para cualquier otro no existe.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request

from geo_copilot.api.auth import asegurar_sesion, require_principal
from geo_copilot.api.dependencies import get_conversation_manager
from geo_copilot.api.limiter import limiter
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.conversation import ConversationManager
from geo_copilot.platform.identidad.principal import Principal

logger = get_logger(__name__)

router = APIRouter(prefix="/proyectos", tags=["Proyectos"], dependencies=[Depends(require_principal)])

_SESION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PROYECTO = re.compile(r"^pr_[0-9a-f]{16}$")
_MAX_ESTADO = 5 * 1024 * 1024
_MAX_MENSAJES = 60


class GuardarProyecto(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    nombre: str = Field(min_length=1, max_length=120)
    estado: dict[str, Any]


def _store():
    from geo_copilot.api.dependencies import get_app_state

    store = get_app_state().dataset_store
    if store is None:
        raise HTTPException(status_code=503, detail="workspace no disponible")
    return store


@router.get("")
@limiter.limit("60/minute")
async def listar(request: Request, principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    return {"proyectos": await _store().listar_proyectos(principal.sub)}


@router.post("", status_code=201)
@limiter.limit("30/minute")
async def guardar(request: Request, body: GuardarProyecto,
                  principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    if len(json.dumps(body.estado, default=str)) > _MAX_ESTADO:
        raise HTTPException(status_code=413, detail="el proyecto es demasiado grande")
    await asegurar_sesion(principal, body.session_id)  # guardar el workspace de otro, 404
    guardado: dict[str, Any] = await _store().guardar_proyecto(
        body.session_id, body.nombre.strip(), body.estado, owner_sub=principal.sub, org_id=principal.org_id)
    return guardado


@router.post("/{proyecto_id}/abrir")
@limiter.limit("30/minute")
async def abrir(request: Request, proyecto_id: str,
                conversation_manager: ConversationManager = Depends(get_conversation_manager),
                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    if not _PROYECTO.match(proyecto_id):
        raise HTTPException(status_code=400, detail="identificador inválido")
    p = await _store().obtener_proyecto(proyecto_id)
    # F6 (E6.2): el proyecto de otro NO EXISTE para quien pregunta (404, no 403)
    if p is None or p.get("owner_sub") != principal.sub:
        raise HTTPException(status_code=404, detail="proyecto no encontrado")
    sesion = p["workspace_id"]
    if not _SESION.match(sesion):
        raise HTTPException(status_code=500, detail="proyecto con sesión inválida")
    # R0.11: una sesión que sigue viva NO se vacía; si ya no existe, se reconstruye con su historial
    contexto = conversation_manager.get_session(sesion)
    recuperada = contexto is not None
    if contexto is None:
        contexto = conversation_manager.create_session(sesion)
    if not contexto.history:
        for m in (p["estado"].get("chat") or [])[-_MAX_MENSAJES:]:
            texto = str((m or {}).get("content") or "").strip()
            if not texto:
                continue
            if m.get("role") == "user":
                contexto.add_user_message(texto)
            elif m.get("role") == "assistant":
                contexto.add_assistant_message(texto)
        conversation_manager.save_session(contexto)
    logger.info(f"[Proyectos] abierto {proyecto_id} «{p['nombre']}» sesión {sesion} (viva={recuperada})")
    return {"id": p["id"], "nombre": p["nombre"], "session_id": sesion, "sesion_viva": recuperada,
            "estado": p["estado"], "updated_at": p["updated_at"]}
