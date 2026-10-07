"""Descargar una capa del workspace como archivo (GeoPackage, Shapefile, KML, GeoJSON, CSV, DXF).

    GET /api/v1/workspace/{session_id}/datasets/{dataset_id}/exportar?formato=gpkg&crs=EPSG:9377

Con `filtro` / `ids` (los de la tabla de la capa) sale solo lo filtrado o lo seleccionado. Va
aparte de `workspace.py` (no pasa de 500 líneas); mismas validaciones: la sesión es de quien
pregunta y el dataset, de esa sesión.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import Request
from starlette.responses import Response

from geo_copilot.api.auth import require_principal
from geo_copilot.api.limiter import limiter
from geo_copilot.api.routes.workspace import _condiciones, _store, _validar
from geo_copilot.core.logging import get_logger
from geo_copilot.platform.workspace.exportar import FORMATOS, ExportacionInvalida, escribir

logger = get_logger(__name__)

router = APIRouter(prefix="/workspace", tags=["Workspace"], dependencies=[Depends(require_principal)])


@router.get("/formatos-exportacion")
async def formatos() -> list[dict[str, str]]:
    """Los formatos de descarga, para el selector del panel."""
    return [{"id": k, "nombre": f.nombre, "extension": f.extension} for k, f in FORMATOS.items()]


@router.get("/{session_id}/datasets/{dataset_id}/exportar")
@limiter.limit("30/minute")
async def exportar(request: Request, session_id: str, dataset_id: str, formato: str = "gpkg",
                   crs: str | None = None, filtro: str | None = None, ids: str | None = None) -> Response:
    """La capa (o lo filtrado / seleccionado de ella) como archivo descargable."""
    from geo_copilot.platform.workspace.ops import _props, _tabla

    await _validar(session_id, dataset_id)
    if formato not in FORMATOS:
        raise HTTPException(status_code=400, detail=f"formato desconocido; válidos: {', '.join(FORMATOS)}")
    store = _store()
    ref = await store.get(session_id, dataset_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="dataset no encontrado")
    campos = [f.name for f in ref.fields]
    cond, params = _condiciones(filtro, ids, campos)
    geom = "ST_AsBinary(s.geom) AS _wkb" if getattr(ref.storage, "geometry_column", None) else "NULL::bytea AS _wkb"
    filas = await store.filas(
        session_id, f"SELECT {', '.join([geom, *_props(ref, 's')])} FROM {_tabla(ref)} s WHERE {cond} ORDER BY s.fid",
        params)
    if not filas:
        raise HTTPException(status_code=404, detail="no hay elementos que exportar con ese filtro")
    try:
        contenido, archivo, mime = await asyncio.to_thread(escribir, filas, campos, formato, ref.name, crs)
    except ExportacionInvalida as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(f"[exportar] {dataset_id} → {archivo} ({len(filas)} elementos, {len(contenido)} B)")
    return Response(contenido, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename=\"{archivo}\"; filename*=UTF-8''{quote(archivo)}",
        "X-Elementos": str(len(filas)),
    })
