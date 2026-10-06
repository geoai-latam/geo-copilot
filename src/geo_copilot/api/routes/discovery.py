"""
Endpoints REST para descubrimiento de datos vía ArcGIS Hub Open Data.

POST /api/v1/discovery/search → buscar datasets (DiscoveryAgent)
POST /api/v1/discovery/layers → capas con geometría de un servicio con varias (para elegir cuál)
POST /api/v1/discovery/load   → preparar capa para el mapa (geojson | imagery)
GET  /api/v1/discovery/health → smoke test
"""

from __future__ import annotations

import re
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from geo_copilot.agents.data_agent import servicio_arcgis
from geo_copilot.agents.data_agent.discovery import (
    DiscoveryAgent,
    DiscoveryHints,
)
from geo_copilot.agents.data_agent.servicio_arcgis import ArcGISNoDisponible
from geo_copilot.agents.symbology_agent import SymbologyAgent
from geo_copilot.api.auth import asegurar_sesion_actual, require_principal
from geo_copilot.api.dependencies import (
    get_agent_graph,
    get_conversation_manager,
    get_llm_client,
)
from geo_copilot.api.limiter import limiter
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger
from geo_copilot.core.security import URLValidator
from geo_copilot.orchestrator.conversation import ConversationManager
from geo_copilot.orchestrator.graph import GeoAgentGraph

logger = get_logger(__name__)

# F4: los modelos de la API viven en discovery_modelos; se reexportan porque se importan de aquí.
from geo_copilot.api.routes.discovery_modelos import (  # noqa: F401
    HintsModel,
    HubItemModel,
    LayerModel,
    LayersRequest,
    LayersResponse,
    LoadRequest,
    LoadResponse,
    SearchRequest,
    SearchResponse,
)

# Mismo formato que routes/session.py — un session_id sólo puede ser
# alfanumérico, '-' o '_', 1-100 chars. Evita inyección y ids absurdos.
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{1,100}$")

router = APIRouter(
    prefix="/discovery",
    tags=["Discovery"],
    dependencies=[Depends(require_principal)],
)


def _hay_workspace(session_id: str | None, conversation_manager: ConversationManager) -> bool:
    from geo_copilot.api.dependencies import get_app_state

    return bool(session_id and conversation_manager.get_session(session_id) is not None
                and get_app_state().dataset_store is not None)


async def _al_workspace(
    session_id: str | None, conversation_manager: ConversationManager, titulo: str, url: str, geojson: Any,
    total_en_servicio: int | None = None,
) -> tuple[dict, dict | None, dict | None, dict] | None:
    """La capa cargada como dataset del workspace de una sesión EXISTENTE (best-effort).

    Devuelve (layer_ref, teselas, GeoJSON para el mapa, muestra para diseñar el estilo). El
    GeoJSON es la versión TAL COMO QUEDÓ en el workspace (campos normalizados, p. ej. `SECTOR` →
    `sector`), la misma que ven las herramientas y el agente; con teselas va None: la capa
    entera no viaja al navegador y el estilo se diseña sobre una muestra repartida.
    """
    if not (session_id and isinstance(geojson, dict) and geojson.get("features")):
        return None
    if not _hay_workspace(session_id, conversation_manager):
        return None
    from datetime import UTC, datetime

    from geo_copilot.api.dependencies import get_app_state
    from geo_copilot.api.routes.workspace import teselas_de
    from geo_copilot.platform.contracts import Provenance

    store = get_app_state().dataset_store
    assert store is not None and session_id
    try:
        ref = await store.ingest_features(
            session_id, titulo[:120], geojson, crs="EPSG:4326",
            provenance=Provenance(capability="discovery.load", produced_at=datetime.now(UTC),
                                  arguments={"servicio": url[:500], "titulo": titulo[:200],
                                             "elementos_cargados": len(geojson["features"]),
                                             **({"total_en_servicio": total_en_servicio}
                                                if total_en_servicio is not None else {})}),
        )
        layer_ref = ref.model_dump(mode="json")
        tiles = teselas_de(session_id, layer_ref)
        if tiles is not None:
            muestra = await store.to_geojson(session_id, ref.id, limit=get_settings().workspace_inline_max_features,
                                             muestra=True)
            return layer_ref, tiles, None, muestra
        fc = await store.to_geojson(session_id, ref.id, limit=len(geojson["features"]) + 1)
    except Exception:  # el workspace es aditivo: la capa se devuelve igual, sin ds_
        logger.warning("[discovery/load] no se materializó la capa en el workspace", exc_info=True)
        return None
    return layer_ref, None, fc, fc


# =============================================================================
# Endpoints
# =============================================================================
@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Discover datasets in ArcGIS Hub Open Data",
)
@limiter.limit("20/minute")
async def search(
    request: Request,
    body: SearchRequest,
    llm_client=Depends(get_llm_client),
) -> SearchResponse:
    hints = DiscoveryHints(**body.hints.model_dump()) if body.hints else DiscoveryHints()

    agent = DiscoveryAgent(llm_client=llm_client)
    try:
        resp = await agent.discover(body.query, hints=hints)
    except ArcGISNoDisponible as exc:
        raise HTTPException(status_code=503, detail=f"Búsqueda en ArcGIS no disponible: {exc}") from exc

    return SearchResponse(
        items=[HubItemModel(**it.to_dict()) for it in resp.items],
        intent=resp.intent,
        authority_warning=resp.authority_warning,
        place_mismatch=resp.place_mismatch,
        place_queried=resp.place_queried,
        suggested_refinements=resp.suggested_refinements,
        criterio=resp.criterio,
        otras_busquedas=resp.otras_busquedas,
        relevantes=resp.relevantes,
    )


def _extent_del_hub(ext: list[float] | None) -> list[float] | None:
    """La extensión del item del Hub SOLO si está en lon/lat: algunos publicadores ponen metros
    ahí y el mapa volaba a otro continente. Mejor sin extensión que una falsa."""
    if not (isinstance(ext, list) and len(ext) == 4):
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in ext)
    except (TypeError, ValueError):
        return None
    ok = all(-180 <= x <= 180 for x in (x0, x1)) and all(-90 <= y <= 90 for y in (y0, y1))
    return [x0, y0, x1, y1] if ok else None


def _url_segura(url: str) -> None:
    # S3: la URL la controla el cliente y el backend la pide server-side → SSRF
    is_safe, reason = URLValidator.validate_url(url, allow_any_port=False)
    if not is_safe:
        logger.warning(f"[discovery] URL rechazada (SSRF guard): {url} — {reason}")
        raise HTTPException(status_code=400, detail=f"URL de servicio no permitida: {reason}")


def _es_raiz_feature_server(url: str) -> bool:
    return url.rstrip("/").split("/")[-1].lower() == "featureserver"


async def _capas_con_geometria(url: str) -> list[dict[str, Any]]:
    try:
        hechos = await servicio_arcgis.describir(url)
    except ArcGISNoDisponible as exc:
        raise HTTPException(status_code=502, detail=f"ArcGIS error: {exc}") from exc
    return [c for c in hechos.get("capas") or [] if isinstance(c, dict) and c.get("tipo_geometria")
            and isinstance(c.get("id"), int)]


@router.post(
    "/layers",
    response_model=LayersResponse,
    summary="Layers with geometry of a multi-layer ArcGIS service",
)
@limiter.limit("30/minute")
async def layers(request: Request, body: LayersRequest) -> LayersResponse:
    """V5 (rama arcgis-busqueda): el panel cargaba siempre la capa 0 — en la cartografía de Cota eran
    PUNTOS; el RUNAP no tiene capa 0 (su única capa es la 59). Quien carga elige viendo las capas."""
    url = body.service_url.rstrip("/")
    _url_segura(url)
    if not _es_raiz_feature_server(url):
        raise HTTPException(status_code=400, detail="Se esperaba la URL de un FeatureServer (sin capa)")
    return LayersResponse(layers=[LayerModel(id=c["id"], nombre=str(c.get("nombre") or f"Capa {c['id']}"),
                                             tipo_geometria=str(c["tipo_geometria"]))
                                  for c in await _capas_con_geometria(url)])


def _build_layer_url(item_url: str, service_type: str, layer_id: int | None) -> str:
    """Resolver la URL final del recurso (con índice de capa cuando aplica)."""
    url = item_url.rstrip("/")
    last = url.split("/")[-1]
    if service_type in ("FeatureServer", "MapServer"):
        if last.isdigit():
            return url
        if layer_id is not None:
            return f"{url}/{layer_id}"
        # Default layer 0
        return f"{url}/0"
    # ImageServer no usa índice
    return url


@router.post(
    "/load",
    response_model=LoadResponse,
    summary="Prepare an ArcGIS Hub item for the map viewer",
)
@limiter.limit("30/minute")
async def load(
    request: Request,
    body: LoadRequest,
    llm_client=Depends(get_llm_client),
    agent_graph: GeoAgentGraph | None = Depends(get_agent_graph),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> LoadResponse:
    item = body.item
    url, nombre = await _validar_y_url(body)

    if item.service_type == "FeatureServer":
        return await _cargar_feature_server(body, url, nombre, llm_client, agent_graph, conversation_manager)

    if item.service_type in ("MapServer", "ImageServer"):
        return await _cargar_imagen(item)

    raise HTTPException(
        status_code=400,
        detail=f"Unsupported service_type: {item.service_type}",
    )


async def _validar_y_url(body: LoadRequest) -> tuple[str, str]:
    """(URL de la capa a cargar, nombre servicio · capa), validando la sesión y la URL."""
    item = body.item
    # Ownership: si el cliente declara una sesión, debe tener formato válido.
    # El despacho de pending_operations (más abajo) sólo corre sobre una sesión
    # que YA EXISTE — un id desconocido/spoofeado es no-op, no crea sesión.
    if body.session_id and not _SESSION_ID_RE.match(body.session_id):
        raise HTTPException(status_code=400, detail="session_id inválido")
    if body.session_id:
        await asegurar_sesion_actual(body.session_id)  # F6: cargar en el workspace de otro, 404
    capa = body.layer_id if body.layer_id is not None else item.layer_id
    # S3: ``item.service_url`` lo controla el cliente y el backend lo
    # fetchea server-side → vector SSRF. Bloqueamos esquemas no-http(s),
    # puertos no estándar e IPs privadas/internas (169.254.169.254,
    # localhost, 10.x, …). No imponemos allowlist de dominio porque el Hub
    # devuelve hosts diversos (*.arcgis.com, orgs), pero el bloqueo de IPs
    # internas es lo que corta el acceso a servicios internos/metadata.
    _url_segura(item.service_url.rstrip("/"))
    if item.service_type == "FeatureServer" and capa is None and _es_raiz_feature_server(item.service_url):
        # Rama arcgis-busqueda: sin capa elegida ya no se adivina la 0. Una sola → esa; varias → que elija.
        capas = await _capas_con_geometria(item.service_url.rstrip("/"))
        if len(capas) > 1:
            raise HTTPException(status_code=409, detail=(
                f"«{item.title}» tiene {len(capas)} capas: elige cuál cargar (POST /discovery/layers)"))
        if capas:
            capa = capas[0]["id"]
    url = _build_layer_url(item.service_url, item.service_type, capa)
    _url_segura(url)
    # El nombre de lo que se carga: servicio · capa. V5: con solo el título del servicio, el agente de
    # simbología tituló «Red vial Guaduas» la capa de DRENAJE elegida de la cartografía de Guaduas.
    nombre = f"{item.title} · {body.layer_name}" if body.layer_name else item.title
    return url, nombre


async def _cargar_feature_server(body: LoadRequest, url: str, nombre: str, llm_client: Any,
                                 agent_graph: GeoAgentGraph | None,
                                 conversation_manager: ConversationManager) -> LoadResponse:
    """Una capa vectorial: al workspace, con simbología y, si había un plan pausado, reanudado."""
    geojson, hechos = await _traer(body, url, conversation_manager)

    # V5: al workspace de la sesión ANTES de todo lo demás: el estilo, el plan pendiente y
    # el mapa usan la versión del workspace (la misma que ven las herramientas ws_*).
    total = hechos.get("total_en_servicio")
    total = int(total) if isinstance(total, (int, float)) and total > 0 else None
    cargados = len(geojson.get("features") or [])
    en_ws = await _al_workspace(body.session_id, conversation_manager, nombre, url, geojson, total)
    dataset_id = None
    tiles: dict[str, Any] | None = None
    para_estilo = geojson
    if en_ws:
        layer_ref, tiles, inline, para_estilo = en_ws
        dataset_id = layer_ref.get("id")
        if inline is not None:
            geojson = inline

    symbology_dict = await _simbologia(llm_client, nombre, para_estilo)
    geojson_out: dict[str, Any] | None = None if tiles is not None else geojson
    if body.session_id and agent_graph and geojson:
        geojson_out, tiles, symbology_dict = await _reanudar_plan(
            body, agent_graph, conversation_manager, nombre, geojson, geojson_out, tiles, symbology_dict)

    if tiles is not None and dataset_id and body.session_id:
        # el estilo se diseñó sobre una muestra: la leyenda cuenta sobre la capa entera
        from geo_copilot.api.dependencies import get_app_state
        from geo_copilot.platform.workspace.clases import recontar_clases

        symbology_dict = await recontar_clases(get_app_state().dataset_store, body.session_id, dataset_id,
                                               symbology_dict)

    return LoadResponse(
        type="geojson",
        name=nombre,
        service_type="FeatureServer",
        service_url=url,
        dataset_id=dataset_id,
        # solo si NO vino todo (hecho del servidor): la malla vial trajo 136.956 de 136.957 porque
        # uno no tiene geometría, y el mensaje decía «es una muestra»
        total_available=total if hechos.get("completo") is False else None,
        geojson=geojson_out,
        tiles=tiles,
        feature_count=(len(geojson_out.get("features", [])) if isinstance(geojson_out, dict) else cargados),
        symbology=symbology_dict,
    )


async def _traer(body: LoadRequest, url: str, conversation_manager: ConversationManager) -> tuple[dict, dict]:
    """T5.2: la capa la trae el servidor MCP de ArcGIS (paginada, en EPSG:4326, con su total)."""
    try:
        ajustes = get_settings()
        # Completa (paginada por el servidor MCP) si hay workspace donde dejarla y servirla
        # por teselas; sin él, lo que cabe inline — y se dice que es una muestra.
        tope = body.limit or ajustes.max_external_features
        if not _hay_workspace(body.session_id, conversation_manager):
            tope = min(tope, ajustes.workspace_inline_max_features)
        return await servicio_arcgis.consultar_capa(url, max_features=tope)
    except ArcGISNoDisponible as exc:
        raise HTTPException(status_code=502, detail=f"ArcGIS error: {exc}") from exc


async def _simbologia(llm_client: Any, nombre: str, para_estilo: dict | None) -> dict[str, Any] | None:
    """Pasar la capa al SymbologyAgent para que genere estilos basados en el
    contenido (geometry type, distribución, campos detectados). Best-effort:
    si el agente falla, devolvemos la capa sin simbología y el frontend
    cae al color por defecto del MapStore."""
    if llm_client and para_estilo:
        try:
            agent = SymbologyAgent(llm_client=llm_client)
            resp = await agent.process(
                query=nombre,
                context={"geojson": para_estilo},
            )
            if resp.success and resp.data:
                return cast(dict[str, Any], resp.data)
        except Exception as exc:  # simbología best-effort (LLM); sin ella la capa carga con el color por defecto
            logger.warning(f"SymbologyAgent failed for {nombre}: {exc}", exc_info=True)
    return None


async def _reanudar_plan(body: LoadRequest, agent_graph: GeoAgentGraph, conversation_manager: ConversationManager,
                         nombre: str, geojson: dict, geojson_out: dict[str, Any] | None,
                         tiles: dict[str, Any] | None, symbology_dict: dict[str, Any] | None,
                         ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """#35 (audit Docker 2026-06): reanudar plan PAUSADO. Si esta carga viene
    de una cadena "busca X, cárgalas y píntalas de rojo", el plan se pausó
    en el paso de búsqueda y dejó las operaciones siguientes (ej. el
    pintado) en ``pending_operations`` de la sesión. El usuario carga
    clicando una card → resolvemos por IDENTIDAD (la URL del item, NO un
    ordinal contra found_services de sesión que podría ser de otra
    búsqueda — E3 intacto) y despachamos las pendientes POR EL GRAFO sobre
    la capa recién cargada. Best-effort: si falla, devolvemos la capa con
    la simbología por defecto."""
    try:
        # get_session (NO get_or_create): sólo reanudamos un plan sobre
        # una sesión existente. Sesión desconocida → ctx None → skip.
        ctx = conversation_manager.get_session(body.session_id or "")
        pending = ctx.get_variable("pending_operations") if ctx else None
        pending_queries = [
            op.get("query", "")
            for op in (pending or [])
            if isinstance(op, dict) and op.get("query")
        ]
        if pending_queries and ctx is not None:  # (sin sesión no hay pendientes)
            pending_query = ", ".join(pending_queries)
            logger.info(
                f"[discovery/load] Reanudando plan pausado (sesión "
                f"{body.session_id}) sobre '{nombre}': '{pending_query}'"
            )
            dispatched = await agent_graph.process(
                query=pending_query,
                session_id=body.session_id or "",  # solo se llama con sesión
                external_geojson=geojson,
                external_source_name=nombre,
                has_external_data=True,
                active_data_source="external",
                active_source_name=nombre,
                conversation_history=ctx.get_messages_for_llm(max_messages=10),
            )
            if dispatched.get("symbology"):
                symbology_dict = dispatched["symbology"]
            # Operaciones espaciales (buffer/centroide) cambian la geometría.
            nueva = dispatched.get("geojson") or dispatched.get("external_geojson")
            if nueva is not None and nueva is not geojson:
                geojson_out, tiles = nueva, None
            # Consumidas: limpiar pending + found_services para no
            # re-aplicarlas ni operar sobre cards viejas.
            ctx.set_variable("pending_operations", None)
            ctx.set_variable("found_services", None)
            conversation_manager.save_session(ctx)  # Redis no ve la mutación
    except Exception as exc:  # reanudar el plan pasa por el grafo completo; best-effort, la capa se devuelve igual
        logger.warning(
            f"[discovery/load] Falló el despacho de pending_operations: {exc}",
            exc_info=True,
        )
        geojson_out = None if tiles is not None else geojson
    return geojson_out, tiles, symbology_dict


async def _cargar_imagen(item: Any) -> LoadResponse:
    """Imagen servida por el propio ArcGIS: el servidor MCP la describe (extensión reproyectada
    a EPSG:4326 y el descriptor que el mapa monta)."""
    try:
        d = await servicio_arcgis.describir(item.service_url)
    except ArcGISNoDisponible as exc:
        raise HTTPException(status_code=502, detail=f"ArcGIS error: {exc}") from exc
    caja = d.get("extent_4326") or _extent_del_hub(item.extent)
    extent = ({"xmin": caja[0], "ymin": caja[1], "xmax": caja[2], "ymax": caja[3]} if caja else None)
    imagery = dict(d.get("imagen") or {"type": "imagery", "service_url": item.service_url.rstrip("/"),
                                       "export_url": None})
    imagery["extent"] = extent
    return LoadResponse(
        type="imagery",
        name=item.title,
        service_type=item.service_type,
        service_url=item.service_url,
        imagery=imagery,
        extent=extent,
    )


@router.get(
    "/regions",
    summary="List configured discovery regions",
)
async def regions() -> dict[str, Any]:
    """Devuelve las regiones disponibles en el catálogo (cargado de YAML).

    El frontend usa esto para poblar un selector de país/región. Cada
    región expone label, lista de entidades y lista de zonas; el cliente
    decide cómo presentarlas (chips por zona, dropdown de entidad…).
    """
    from geo_copilot.agents.data_agent.catalogo_regiones import (
        REGIONS,
        get_active_region,
    )
    return {
        "active": get_active_region(),
        "regions": [
            {
                "key": rk,
                "label": rv.label,
                "country_bbox": rv.country_bbox,
                "entities": [
                    {
                        "key": ek,
                        "aliases": ent.aliases,
                        "has_sources": bool(ent.sources),
                        "has_tags": bool(ent.tags),
                    }
                    for ek, ent in rv.entities.items()
                ],
                "zones": [
                    {
                        "key": zk,
                        "label": zone.label,
                        "bbox": zone.bbox,
                        "tags": zone.tags,
                    }
                    for zk, zone in rv.zones.items()
                ],
            }
            for rk, rv in REGIONS.items()
        ],
    }


@router.get(
    "/health",
    summary="Discovery health check",
)
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "discovery"}
