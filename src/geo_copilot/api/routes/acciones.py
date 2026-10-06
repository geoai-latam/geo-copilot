"""FH.8 — acciones contextuales del mapa.

    GET  /api/v1/acciones?geometria=Polygon     las capacidades aplicables (con su formulario)
    POST /api/v1/acciones/{herramienta}/run      ejecutarla (la MISMA capacidad que usa el agente)

La lista sale de lo que cada capacidad declara (`geo_inputs`), sea del núcleo o de un MCP:
ver `platform/acciones.py`. La respuesta de `run` tiene la forma de `results` de /query.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request

from geo_copilot.api.auth import asegurar_sesion_actual, require_principal
from geo_copilot.api.limiter import limiter

router = APIRouter(prefix="/acciones", tags=["Acciones"], dependencies=[Depends(require_principal)])

_GEOMETRIAS = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
_MAX_CUERPO = 10 * 1024 * 1024  # un AOI dibujado o una capa pequeña, no un dataset


class EjecutarAccion(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    map_context: dict[str, Any] | None = None


@router.get("")
@limiter.limit("120/minute")
async def listar(request: Request, geometria: str | None = None) -> dict[str, Any]:
    from geo_copilot.orchestrator.capabilities_core import ensure_core
    from geo_copilot.platform.acciones import aplicables

    if geometria is not None and geometria not in _GEOMETRIAS:
        raise HTTPException(status_code=400, detail=f"tipo de geometría no válido: {geometria}")
    ensure_core()
    return {"acciones": aplicables(geometria)}


async def ejecutar_capacidad(cap: Any, body: EjecutarAccion) -> dict[str, Any]:
    """Ejecuta la capacidad como la ejecutaría el agente; devuelve la forma de `results` de /query."""
    from geo_copilot.api.routes.workspace import teselas_de

    await asegurar_sesion_actual(body.session_id)  # F6: sobre el workspace de otro, 404
    working = {"session_id": body.session_id, "map_context": body.map_context or {}}
    from geo_copilot.platform.capabilities import ejecutar

    out = await ejecutar(cap, None, working, body.arguments, origen="mapa")  # F6: permisos + auditoría
    d = out.delta or {}
    layer_ref = d.get("result_layer_ref")
    return {
        "success": out.success,
        "message": None if out.success else out.observation,
        # sin capa (p. ej. ws_measure) la observación ES el resultado: sus hechos
        "facts": out.facts or ({} if d else _hechos_de(out.observation)),
        # una herramienta que solo responde TEXTO (sin hechos ni capa): ese texto es su resultado
        "texto": out.observation if out.success and not out.facts and not _hechos_de(out.observation)
        and not any(d.get(k) for k in ("geojson", "result_layer_ref", "data", "external_imagery")) else None,
        "results": {
            "geojson": d.get("geojson"),
            "data": d.get("data"),
            "visualization": d.get("visualization"),
            "external_imagery": d.get("external_imagery"),
            "layer_name": d.get("layer_name"),
            "layer_ref": layer_ref,
            "tiles": teselas_de(body.session_id, layer_ref),
        },
    }


def _hechos_de(observacion: str) -> dict[str, Any]:
    try:
        obs = json.loads(observacion)
    except (TypeError, ValueError):
        return {}
    return (obs.get("hechos") or {}) if isinstance(obs, dict) else {}


@router.post("/{herramienta}/run")
@limiter.limit("30/minute")
async def ejecutar(request: Request, herramienta: str, body: EjecutarAccion) -> dict[str, Any]:
    from geo_copilot.orchestrator.capabilities_core import ensure_core
    from geo_copilot.platform.acciones import aplicables
    from geo_copilot.platform.capabilities import registry

    if int(request.headers.get("content-length") or 0) > _MAX_CUERPO:
        raise HTTPException(status_code=413, detail="petición demasiado grande")
    ensure_core()
    # solo lo que el menú ofrece (habilitada, sin escritura, con entrada del mapa)
    if herramienta not in {a["herramienta"] for a in aplicables(None)}:
        raise HTTPException(status_code=404, detail="acción no encontrada o no disponible")
    cap = registry().get(herramienta)
    if cap is None:
        raise HTTPException(status_code=404, detail="acción no encontrada o no disponible")
    return await ejecutar_capacidad(cap, body)
