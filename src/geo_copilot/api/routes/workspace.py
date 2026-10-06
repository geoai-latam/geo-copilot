"""Endpoints del workspace espacial de la sesión (F2).

    GET   /api/v1/workspace/{session_id}/datasets
    GET   /api/v1/workspace/{session_id}/datasets/{dataset_id}/capa
    POST  /api/v1/workspace/{session_id}/sketches               (FH.3: un dibujo)
    PATCH /api/v1/workspace/{session_id}/datasets/{dataset_id}  (FH.3: nombre / vértices)

El segundo devuelve un dataset listo para el mapa con la MISMA forma que
`/query` en `results`: `layer_ref` y, según el tamaño, `geojson` inline o
`tiles` (MVT del workspace). Con él el cliente restaura sus capas tras recargar
la página (E2.4). Todo se resuelve dentro del workspace de ESA sesión: un
dataset ajeno o vencido da 404 (sin decir cuál de los dos).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request

from geo_copilot.api.auth import asegurar_sesion_actual, require_principal
from geo_copilot.api.dependencies import get_app_state
from geo_copilot.api.limiter import limiter
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/workspace",
    tags=["Workspace"],
    dependencies=[Depends(require_principal)],
)

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DATASET_RE = re.compile(r"^ds_[0-9a-f]{16}$")


def teselas_de(session_id: str, layer_ref: dict | None) -> dict | None:
    """Descriptor de teselas MVT si la capa supera el umbral inline, o None (S2.3)."""
    if not layer_ref or not layer_ref.get("id"):
        return None
    if (layer_ref.get("feature_count") or 0) <= get_settings().workspace_inline_max_features:
        return None
    storage = layer_ref.get("storage") or {}
    if not storage.get("geometry_column"):
        return None
    return {
        "url": f"/api/v1/tiles/ws/{session_id}/{layer_ref['id']}/{{z}}/{{x}}/{{y}}.pbf",
        "source_layer": "dataset",
        "geometry_type": layer_ref.get("geometry_type"),
        "bbox": layer_ref.get("bbox"),
        "feature_count": layer_ref.get("feature_count"),
        # sin GeoJSON el cliente no ve las properties: los campos van aquí
        "fields": [f["name"] for f in layer_ref.get("fields") or []],
    }


def _store():
    store = get_app_state().dataset_store
    if store is None:
        raise HTTPException(status_code=503, detail="workspace no disponible")
    return store


async def _validar(session_id: str, dataset_id: str | None = None) -> None:
    """Formato de los ids y (F6) que la sesión sea de quien pregunta: la de otro, 404."""
    if not _SESSION_RE.match(session_id) or (dataset_id is not None and not _DATASET_RE.match(dataset_id)):
        raise HTTPException(status_code=400, detail="identificador inválido")
    await asegurar_sesion_actual(session_id)


@router.get("/{session_id}/datasets")
@limiter.limit("120/minute")
async def listar(request: Request, session_id: str) -> dict[str, Any]:
    """Los datasets vivos del workspace de la sesión (LayerRef)."""
    await _validar(session_id)
    refs = await _store().list_datasets(session_id)
    return {"datasets": [r.model_dump(mode="json") for r in refs]}


@router.get("/{session_id}/datasets/{dataset_id}/capa")
@limiter.limit("120/minute")
async def capa(request: Request, session_id: str, dataset_id: str) -> dict[str, Any]:
    """Un dataset listo para el mapa: `layer_ref` + `geojson` o `tiles`."""
    from geo_copilot.platform.workspace import WorkspaceError

    await _validar(session_id, dataset_id)
    store = _store()
    ref = await store.get(session_id, dataset_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="dataset no encontrado")
    layer_ref = ref.model_dump(mode="json")
    tiles = teselas_de(session_id, layer_ref)
    geojson = None
    if tiles is None and ref.storage.kind == "workspace-table" and ref.storage.geometry_column:
        try:
            geojson = await store.to_geojson(
                session_id, dataset_id, limit=get_settings().workspace_inline_max_features,
            )
        except WorkspaceError as exc:  # venció entre el get y la lectura
            raise HTTPException(status_code=404, detail="dataset no encontrado") from exc
    return {"layer_ref": layer_ref, "geojson": geojson, "tiles": tiles}


#: Hasta dónde se remonta la cadena de "cómo se hizo" (un buffer de un cruce de una consulta…).
_MAX_PASOS_PROCEDENCIA = 12


def _datasets_citados(valor: Any) -> list[str]:
    """Los `ds_…` que aparecen en los argumentos de una operación (sus entradas)."""
    if isinstance(valor, str):
        return [valor] if valor.startswith("ds_") else []
    if isinstance(valor, dict):
        return [d for v in valor.values() for d in _datasets_citados(v)]
    if isinstance(valor, list):
        return [d for v in valor for d in _datasets_citados(v)]
    return []


@router.get("/{session_id}/datasets/{dataset_id}/procedencia")
@limiter.limit("120/minute")
async def procedencia(request: Request, session_id: str, dataset_id: str) -> dict[str, Any]:
    """FH.7 — "cómo se hizo": la procedencia del dataset y la de los datasets de los que
    sale (sus entradas, citadas en los argumentos), de la capa hacia sus fuentes."""
    await _validar(session_id, dataset_id)
    store = _store()
    pasos: list[dict[str, Any]] = []
    pendientes, vistos = [dataset_id], set()
    while pendientes and len(pasos) < _MAX_PASOS_PROCEDENCIA:
        ds = pendientes.pop(0)
        if ds in vistos:
            continue
        vistos.add(ds)
        ref = await store.get(session_id, ds)
        if ref is None:
            # una entrada que venció o no es de esta sesión: se dice, no se inventa
            pasos.append({"dataset_id": ds, "disponible": False})
            continue
        prov = ref.provenance.model_dump(mode="json")
        pasos.append({"dataset_id": ds, "nombre": ref.name, "disponible": True, "provenance": prov})
        pendientes.extend(d for d in _datasets_citados(prov.get("arguments")) if d not in vistos)
    if not pasos or not pasos[0].get("disponible"):
        raise HTTPException(status_code=404, detail="dataset no encontrado")
    return {"pasos": pasos}


class MedirIn(BaseModel):
    geometry: dict[str, Any]


_MEDIBLES = {"LineString", "MultiLineString", "Polygon", "MultiPolygon"}


@router.post("/{session_id}/medir")
@limiter.limit("120/minute")
async def medir(request: Request, session_id: str, body: MedirIn) -> dict[str, Any]:
    """FH.10 — la herramienta Medir del mapa: longitud o área GEODÉSICA exacta (PostGIS
    `geography`, la misma que usa el agente) de una figura trazada; no crea ningún dataset."""
    import json as _json

    await _validar(session_id)
    tipo = body.geometry.get("type")
    if tipo not in _MEDIBLES:
        raise HTTPException(status_code=400, detail="se mide una línea o un polígono")
    if _contar_vertices(body.geometry.get("coordinates")) > _MAX_VERTICES_DIBUJO:
        raise HTTPException(status_code=400, detail="la figura tiene demasiados vértices")
    try:
        h = await _store().hechos(session_id, (
            "SELECT round(ST_Length(g)::numeric, 2)::float AS longitud_m, "
            "round(ST_Area(g)::numeric, 2)::float AS area_m2, "
            "round(ST_Perimeter(g)::numeric, 2)::float AS perimetro_m "
            "FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON($1), 4326)::geography AS g) s"),
            (_json.dumps(body.geometry),))
    except Exception as exc:  # una geometría mal formada (asyncpg la rechaza) es un 400, no un 500
        raise HTTPException(status_code=400, detail="geometría no válida") from exc
    es_area = "Polygon" in str(tipo)
    return {"tipo": "area" if es_area else "longitud",
            **({"area_m2": h["area_m2"], "area_ha": round(h["area_m2"] / 10_000, 4), "perimetro_m": h["perimetro_m"]}
               if es_area else {"longitud_m": h["longitud_m"], "longitud_km": round(h["longitud_m"] / 1000, 4)})}


# ---------------------------------------------------------------------------
# FH.3 — dibujos del usuario
# ---------------------------------------------------------------------------

#: El proveedor que marca un dataset como dibujo del usuario (LayerRef.provider).
SKETCH = "sketch"
_GEOMETRIAS_DIBUJO = {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"}
_MAX_ELEMENTOS_DIBUJO = 50
_MAX_VERTICES_DIBUJO = 20_000


class DibujoIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    geojson: dict[str, Any]


class DatasetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    geojson: dict[str, Any] | None = None


def _contar_vertices(coords: Any) -> int:
    if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
        return 1
    return sum(_contar_vertices(c) for c in coords) if isinstance(coords, list) else 0


def _geometrias_de_dibujo(fc: dict[str, Any]) -> list[dict[str, Any]]:
    """Las features del dibujo, solo con su geometría (y su `id` si lo trae). 400 si no valen."""
    feats = fc.get("features") if fc.get("type") == "FeatureCollection" else None
    if not isinstance(feats, list) or not 1 <= len(feats) <= _MAX_ELEMENTOS_DIBUJO:
        raise HTTPException(status_code=400,
                            detail=f"un dibujo es una FeatureCollection de 1 a {_MAX_ELEMENTOS_DIBUJO} elementos")
    limpias, vertices = [], 0
    for f in feats:
        geom = (f or {}).get("geometry") if isinstance(f, dict) else None
        if not isinstance(geom, dict) or geom.get("type") not in _GEOMETRIAS_DIBUJO:
            raise HTTPException(status_code=400, detail="geometría de dibujo no válida")
        vertices += _contar_vertices(geom.get("coordinates"))
        limpia: dict[str, Any] = {"type": "Feature", "geometry": geom, "properties": {}}
        if isinstance(f.get("id"), int):
            limpia["id"] = f["id"]
        limpias.append(limpia)
    if vertices > _MAX_VERTICES_DIBUJO:
        raise HTTPException(status_code=400, detail=f"el dibujo tiene demasiados vértices ({vertices})")
    return limpias


async def _respuesta(store: Any, session_id: str, ref: Any) -> dict[str, Any]:
    geojson = await store.to_geojson(session_id, ref.id, limit=get_settings().workspace_inline_max_features)
    return {"layer_ref": ref.model_dump(mode="json"), "geojson": geojson, "tiles": None}


@router.post("/{session_id}/sketches", status_code=201)
@limiter.limit("60/minute")
async def crear_dibujo(request: Request, session_id: str, body: DibujoIn) -> dict[str, Any]:
    """Guarda un dibujo del usuario como dataset del workspace (usable como AOI por cualquier capacidad)."""
    from geo_copilot.platform.contracts import Provenance
    from geo_copilot.platform.workspace import WorkspaceError

    await _validar(session_id)
    feats = _geometrias_de_dibujo(body.geojson)
    tipos = sorted({f["geometry"]["type"] for f in feats})
    prov = Provenance(capability="user.sketch", produced_at=datetime.now(UTC),
                      arguments={"geometria": ", ".join(tipos)})
    store = _store()
    try:
        ref = await store.ingest_features(
            session_id, body.name.strip(),
            {"type": "FeatureCollection", "features": [{k: v for k, v in f.items() if k != "id"} for f in feats]},
            crs="EPSG:4326", provenance=prov, provider=SKETCH,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("[workspace] %s: dibujo «%s» (%s) → %s", session_id[:8], ref.name, ", ".join(tipos), ref.id)
    return await _respuesta(store, session_id, ref)


@router.patch("/{session_id}/datasets/{dataset_id}")
@limiter.limit("60/minute")
async def editar_dataset(request: Request, session_id: str, dataset_id: str, body: DatasetPatch) -> dict[str, Any]:
    """Renombra un dataset de la sesión o, si es un dibujo, guarda sus vértices editados."""
    from geo_copilot.platform.workspace import WorkspaceError

    await _validar(session_id, dataset_id)
    if body.name is None and body.geojson is None:
        raise HTTPException(status_code=400, detail="nada que cambiar")
    store = _store()
    ref = await store.get(session_id, dataset_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="dataset no encontrado")
    try:
        if body.geojson is not None:
            if ref.provider != SKETCH:
                raise HTTPException(status_code=409, detail="solo se editan los vértices de un dibujo")
            ref = await store.reemplazar_geometrias(
                session_id, dataset_id,
                {"type": "FeatureCollection", "features": _geometrias_de_dibujo(body.geojson)},
            )
        if body.name is not None:
            ref = await store.renombrar(session_id, dataset_id, body.name)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _respuesta(store, session_id, ref)


# ---------------------------------------------------------------------------
# FH.5 — la tabla vinculada de una capa grande: filas paginadas y estadística
# ---------------------------------------------------------------------------


def _condiciones(filtro: str | None, ids: str | None, campos: list[str]) -> tuple[str, tuple[Any, ...]]:
    """WHERE del filtro de la capa (JSON de predicados) y, opcional, de unos fid."""
    import json

    from geo_copilot.platform.seleccion import PredicadoInvalido, sql_filtro

    partes: list[str] = []
    params: list[Any] = []
    try:
        condiciones = json.loads(filtro) if filtro else []
        if not isinstance(condiciones, list) or len(condiciones) > 10:
            raise ValueError
        if condiciones:
            cond, ps = sql_filtro(condiciones, campos)
            partes.append(cond)
            params.extend(ps)
        if ids:
            lista = [int(x) for x in json.loads(ids)][:5000]
            partes.append(f"s.fid = ANY(${len(params) + 1}::int[])")
            params.append(lista)
    except (ValueError, TypeError, PredicadoInvalido) as exc:
        raise HTTPException(status_code=400, detail=f"filtro inválido: {exc}") from exc
    return (" AND ".join(partes) or "TRUE"), tuple(params)


@router.get("/{session_id}/datasets/{dataset_id}/filas")
@limiter.limit("240/minute")
async def filas(request: Request, session_id: str, dataset_id: str, offset: int = 0, limit: int = 50,
                orden: str | None = None, desc: bool = False, filtro: str | None = None,
                ids: str | None = None) -> dict[str, Any]:
    """Una página de filas (con su fid y su extensión) del dataset, con el filtro de la capa."""
    from geo_copilot.platform.workspace.ops import _props, _tabla
    from geo_copilot.platform.workspace.store import _qi

    await _validar(session_id, dataset_id)
    store = _store()
    ref = await store.get(session_id, dataset_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="dataset no encontrado")
    campos = [f.name for f in ref.fields]
    if orden is not None and orden not in campos and orden != "fid":
        raise HTTPException(status_code=400, detail=f"no se puede ordenar por «{orden}»")
    cond, params = _condiciones(filtro, ids, campos)
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    n = len(params)
    total = (await store.hechos(session_id, f"SELECT count(*) AS n FROM {_tabla(ref)} s WHERE {cond}", params)).get("n")
    geom = ref.storage.geometry_column if hasattr(ref.storage, "geometry_column") else None
    extension = (", ST_XMin(s.geom) AS _x0, ST_YMin(s.geom) AS _y0, ST_XMax(s.geom) AS _x1, ST_YMax(s.geom) AS _y1"
                 if geom else "")
    por = "s.fid" if orden is None or orden == "fid" else f"s.{_qi(orden)}"
    filas_ = await store.filas(
        session_id,
        f"SELECT s.fid AS _fid, {', '.join(_props(ref, 's')) or 's.fid AS _vacio'}{extension} FROM {_tabla(ref)} s "
        f"WHERE {cond} ORDER BY {por} {'DESC' if desc else 'ASC'} NULLS LAST, s.fid "
        f"LIMIT ${n + 1} OFFSET ${n + 2}",
        (*params, limit, offset),
    )
    salida = []
    for f in filas_:
        bbox = [f.pop(k) for k in ("_x0", "_y0", "_x1", "_y1")] if geom else None
        f.pop("_vacio", None)
        salida.append({"fid": f.pop("_fid"), "properties": f, "bbox": bbox})
    return {"total": int(total or 0), "offset": offset, "filas": salida}


@router.get("/{session_id}/datasets/{dataset_id}/estadistica")
@limiter.limit("240/minute")
async def estadistica(request: Request, session_id: str, dataset_id: str, campo: str,
                      filtro: str | None = None) -> dict[str, Any]:
    """Estadística de un campo (con el filtro de la capa): conteo, nulos, mín/máx/media si es
    numérico, y los valores más frecuentes."""
    from geo_copilot.platform.workspace.ops import _tabla
    from geo_copilot.platform.workspace.store import _qi

    await _validar(session_id, dataset_id)
    store = _store()
    ref = await store.get(session_id, dataset_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="dataset no encontrado")
    tipos = {f.name: f.type for f in ref.fields}
    if campo not in tipos:
        raise HTTPException(status_code=400, detail=f"el campo «{campo}» no existe")
    cond, params = _condiciones(filtro, None, list(tipos))
    col = f"s.{_qi(campo)}"
    numerico = tipos[campo] in ("number", "integer")
    agregados = (f", min({col})::float AS min, max({col})::float AS max, avg({col})::float AS media"
                 if numerico else "")
    h = await store.hechos(
        session_id,
        f"SELECT count(*) AS n, count({col}) AS con_valor, count(DISTINCT {col}) AS unicos{agregados} "
        f"FROM {_tabla(ref)} s WHERE {cond}", params)
    top = await store.filas(
        session_id,
        f"SELECT {col}::text AS valor, count(*) AS n FROM {_tabla(ref)} s WHERE {cond} AND {col} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10", params)
    return {"campo": campo, "numerico": numerico, **{k: h.get(k) for k in h}, "frecuentes": top}


# ---------------------------------------------------------------------------
# FH.6 — el editor manual de simbología: el mismo cálculo que usa el agente
# ---------------------------------------------------------------------------


class DisenoEstilo(BaseModel):
    symbology_type: str = Field(max_length=32)
    classification_field: str | None = Field(default=None, max_length=128)
    classification_method: str | None = Field(default=None, max_length=32)
    num_classes: int | None = Field(default=None, ge=2, le=12)
    color_scheme: str | None = Field(default=None, max_length=32)
    fill_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class EstiloIn(BaseModel):
    diseno: DisenoEstilo
    #: La capa: su dataset del workspace o, si no tiene, sus features (acotadas).
    dataset_id: str | None = Field(default=None, pattern=r"^ds_[0-9a-f]{16}$")
    geojson: dict[str, Any] | None = None
    titulo: str | None = Field(default=None, max_length=200)
    #: Lo que el usuario fijó a mano (viaja con el estilo; el agente lo ve como hecho).
    pinned: list[str] = Field(default_factory=list, max_length=10)


@router.get("/rampas")
@limiter.limit("60/minute")
async def rampas(request: Request) -> dict[str, Any]:
    """Las rampas de color disponibles (las mismas que usa el agente de simbología)."""
    from geo_copilot.agents.symbology_agent.manual import rampas as _rampas

    return {"rampas": _rampas()}


@router.post("/{session_id}/estilo")
@limiter.limit("120/minute")
async def estilo(request: Request, session_id: str, body: EstiloIn) -> dict[str, Any]:
    """Calcula el StyleSpec de un diseño elegido a mano (cortes, colores) sobre los datos de la capa."""
    from geo_copilot.agents.symbology_agent.manual import CAMPOS_EDITABLES, clasificar_manual
    from geo_copilot.platform.artefactos import estilo_de

    await _validar(session_id, body.dataset_id)
    limite = get_settings().workspace_inline_max_features
    if body.dataset_id:
        store = _store()
        ref = await store.get(session_id, body.dataset_id)
        if ref is None:
            raise HTTPException(status_code=404, detail="dataset no encontrado")
        grande = (ref.feature_count or 0) > limite
        # capa grande: los cortes salen de una muestra repartida (igual que el agente, H23)
        datos = await store.to_geojson(session_id, body.dataset_id, limit=limite, muestra=grande)
    elif isinstance(body.geojson, dict) and body.geojson.get("type") == "FeatureCollection":
        if len(body.geojson.get("features") or []) > limite:
            raise HTTPException(status_code=400, detail="demasiados elementos: la capa debe estar en el workspace")
        datos = body.geojson
    else:
        raise HTTPException(status_code=400, detail="falta dataset_id o geojson")
    calculado = await clasificar_manual(datos, body.diseno.model_dump(), titulo=body.titulo)
    spec = estilo_de(calculado)
    if spec is None:
        raise HTTPException(status_code=400, detail="ese diseño no produce un estilo válido para estos datos")
    fijados = [c for c in body.pinned if c in CAMPOS_EDITABLES]
    return {"style": {**spec.model_dump(mode="json"), "pinned": fijados}}
