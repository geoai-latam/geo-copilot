"""Conexiones MCP (S3.3; F6 S6.2): estado de cada servidor, alta/baja y re-aprobación de tools.

    GET    /api/v1/connections                              las de la plataforma y las de SU organización
    POST   /api/v1/connections                              alta de una conexión de su organización (admin)
    DELETE /api/v1/connections/{server}                     baja de una de su organización (admin)
    GET    /api/v1/connections/tools
    POST   /api/v1/connections/{server}/tools/{tool}/run
    POST   /api/v1/connections/{server}/tools/{tool}/approve  (admin)

F6: una conexión dada de alta por una organización solo existe para ella; su credencial se
guarda cifrada y NUNCA vuelve en una respuesta (solo `tiene_credencial`).

La re-aprobación existe por el pinning (§3.6.2): una tool cuya descripción o
esquema cambió queda deshabilitada hasta que un administrador la apruebe.

`tools` + `run` son el panel de herramientas (E3.2): el formulario se genera
desde el `input_schema` del servidor y `run` ejecuta la MISMA capacidad que usa
el agente (allowlist, límites, argumentos geo, materialización). El resultado
tiene la forma de `results` de `/query`, así el cliente lo pinta igual.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request

from geo_copilot.api.auth import requiere_rol, require_principal
from geo_copilot.api.limiter import limiter
from geo_copilot.platform.identidad.principal import Principal

router = APIRouter(prefix="/connections", tags=["Connections"], dependencies=[Depends(require_principal)])


def _hub():
    from geo_copilot.platform.mcp.hub import hub_actual

    return hub_actual()


@router.get("")
@limiter.limit("60/minute")
async def listar(request: Request, principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    from geo_copilot.platform.conexiones.hubs import hubs_actuales

    hub = _hub()
    hubs = hubs_actuales()
    propias = [c.publica() for c in await hubs.store.listar(principal.org_id)] if hubs is not None else []
    return {"servers": hub.estado() if hub is not None else [], "de_la_organizacion": propias,
            "puede_administrar": principal.puede("admin")}


class AltaConexion(BaseModel):
    """Lo que un administrador da de alta desde el panel. El resto, con valores prudentes."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    url: str = Field(pattern=r"^https?://", max_length=500)
    description: str | None = Field(default=None, max_length=300)
    #: Credencial (Bearer) del servidor: se guarda cifrada y no se devuelve nunca.
    credencial: str | None = Field(default=None, max_length=4096)
    #: `tabular_geo` para servicios que devuelven FILAS (tipo Snowflake).
    adapter: str | None = Field(default=None, pattern=r"^tabular_geo$")
    trust: str = Field(default="untrusted", pattern=r"^(trusted|untrusted)$")


@router.post("", status_code=201)
@limiter.limit("10/minute")
async def alta(request: Request, body: AltaConexion,
               principal: Principal = Depends(requiere_rol("admin"))) -> dict[str, Any]:
    from geo_copilot.platform import auditoria
    from geo_copilot.platform.conexiones.cifrado import CifradoNoConfigurado
    from geo_copilot.platform.conexiones.hubs import hubs_actuales
    from geo_copilot.platform.conexiones.red import UrlNoPermitida, validar_url
    from geo_copilot.platform.conexiones.store import ConexionExiste
    from geo_copilot.platform.mcp.config import ServerConfig
    from geo_copilot.platform.mcp.hub import hub_actual

    hubs = hubs_actuales()
    if hubs is None:
        raise HTTPException(status_code=503, detail="las conexiones de organización no están disponibles")
    plataforma = hub_actual()
    if plataforma is not None and body.id in plataforma.conexiones:
        raise HTTPException(status_code=409, detail=f"«{body.id}» ya es un servicio conectado; elige otro id")
    try:
        await validar_url(body.url)
    except UrlNoPermitida as exc:
        await auditoria.registrar("conexion.alta", body.id, "denegado", detalle={"url": body.url, "motivo": str(exc)})
        raise HTTPException(status_code=400, detail=f"URL no permitida: {exc}") from None
    cfg = ServerConfig.model_validate({
        "id": body.id, "url": body.url, "description": body.description, "trust": body.trust,
        "auth": {"type": "bearer" if body.credencial else "none"}, "conformance": "G0",
        "tools": {"allow": ["*"]}, "adapter": body.adapter, "red_restringida": True,
        # De un tercero no confiable, una tool sin anotar (o que se dice «solo lectura») podía ser
        # un «delete_x» y correr sola como `compute`: por defecto pide aprobación.
        **({"policy": {"default_risk": "external_egress"}} if body.trust == "untrusted" else {}),
    })
    try:
        conexion = await hubs.store.crear(principal.org_id, cfg, body.credencial, principal.sub)
    except ConexionExiste:
        raise HTTPException(status_code=409, detail=f"ya hay una conexión «{body.id}» en tu organización") from None
    except CifradoNoConfigurado as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    await auditoria.registrar("conexion.alta", body.id, "ok",
                              detalle={"url": body.url, "adapter": body.adapter, "trust": body.trust,
                                       "con_credencial": bool(body.credencial)})
    await hubs.recargar(principal.org_id)
    hub = _hub()
    estado = next((e for e in (hub.estado() if hub is not None else []) if e["id"] == body.id), None)
    return {**conexion.publica(), "estado": estado}


@router.delete("/{server}", dependencies=[Depends(requiere_rol("admin"))])
@limiter.limit("10/minute")
async def baja(request: Request, server: str, principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    from geo_copilot.platform import auditoria
    from geo_copilot.platform.conexiones.hubs import hubs_actuales

    hubs = hubs_actuales()
    # solo las de SU organización: las de la plataforma (YAML) y las de otra no existen aquí
    if hubs is None or not await hubs.store.borrar(principal.org_id, server):
        raise HTTPException(status_code=404, detail="conexión no encontrada")
    await auditoria.registrar("conexion.baja", server, "ok")
    await hubs.recargar(principal.org_id)
    return {"ok": True, "id": server}


@router.get("/tools")
@limiter.limit("60/minute")
async def herramientas(request: Request) -> dict[str, Any]:
    hub = _hub()
    return {"tools": hub.herramientas() if hub is not None else []}


_MAX_CUERPO = 10 * 1024 * 1024  # un AOI dibujado o una capa pequeña, no un dataset


class EjecutarTool(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    map_context: dict[str, Any] | None = None


@router.post("/{server}/tools/{tool}/run")
@limiter.limit("30/minute")
async def ejecutar(request: Request, server: str, tool: str, body: EjecutarTool) -> dict[str, Any]:
    from geo_copilot.platform.capabilities import registry
    from geo_copilot.platform.mcp.hub import nombre_llm

    if int(request.headers.get("content-length") or 0) > _MAX_CUERPO:
        raise HTTPException(status_code=413, detail="petición demasiado grande")
    hub = _hub()
    est = hub.tools.get(nombre_llm(server, tool)) if hub is not None else None
    if est is not None and (est.servidor, est.tool) != (server, tool):
        est = None  # `a`+`b__c` y `a__b`+`c` comparten nombre para el LLM: no se confunden aquí
    cap = registry().get(est.nombre_llm) if est is not None and est.habilitada else None
    if est is None or cap is None:
        raise HTTPException(status_code=404, detail="tool no encontrada o deshabilitada")
    if est.riesgo == "write":
        # escribir en un sistema externo exige la aprobación del flujo del agente (HITL)
        raise HTTPException(status_code=403, detail="las herramientas de escritura se piden por el chat")

    from geo_copilot.api.routes.acciones import EjecutarAccion, ejecutar_capacidad

    return await ejecutar_capacidad(cap, EjecutarAccion(**body.model_dump()))


@router.post("/{server}/tools/{tool}/approve", dependencies=[Depends(requiere_rol("admin"))])
@limiter.limit("20/minute")
async def aprobar(request: Request, server: str, tool: str,
                  principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    """Re-aprobar una tool cuya descripción o esquema cambió (F6: solo un administrador)."""
    from geo_copilot.core.config import get_settings
    from geo_copilot.platform import auditoria
    from geo_copilot.platform.mcp.hub import hubs_org

    hub = _hub()
    propio = hubs_org().get(principal.org_id)
    de_la_plataforma = not (propio is not None and server in propio.conexiones)
    org_plataforma = get_settings().org_plataforma
    if de_la_plataforma and org_plataforma and principal.org_id != org_plataforma and principal.via != "api_key":
        # un servidor de la plataforma es de TODAS las organizaciones: no lo decide una de ellas
        await auditoria.registrar("tool.reaprobar", f"mcp.{server}.{tool}", "denegado",
                                  detalle={"motivo": "herramienta de la plataforma"})
        raise HTTPException(status_code=403, detail="Las herramientas de la plataforma las re-aprueba "
                                                    "la administración de la plataforma.")
    if hub is None or not await hub.aprobar(server, tool, por=principal.sub):
        raise HTTPException(status_code=404, detail="tool no encontrada")
    await auditoria.registrar("tool.reaprobar", f"mcp.{server}.{tool}", "ok")
    await hub.refrescar()
    return {"ok": True, "server": server, "tool": tool}
