"""Los MODELOS de la API de discovery: pistas, búsqueda y sus resultados, capas de un servicio y la
carga de una capa al mapa (petición y respuesta).

Salió de `api/routes/discovery.py` (F4 del plan de calidad: discovery.py tenía 573 líneas), tal cual.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# Modelos Pydantic
# =============================================================================
class HintsModel(BaseModel):
    service_types: list[str] | None = Field(
        default=None,
        description="Ej: ['Feature Service', 'Map Service', 'Image Service']",
    )
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    only_official_co: bool = False
    owners: list[str] | None = None
    tags_any: list[str] | None = Field(
        default=None,
        description="`filter[tags]=any(...)` para Hub — filtro de zona/topic curado por publicador.",
    )
    region: str | None = Field(
        default=None,
        description="Región del catálogo a usar para detección de entidades/zonas y sesgo. None = región activa del producto. 'global' = sin sesgo regional.",
    )
    global_mode: bool = Field(
        default=False,
        description="Atajo: True equivale a region='global' — desactiva el sesgo regional para búsqueda mundial.",
    )
    max_results: int = Field(default=50, ge=1, le=200)


class SearchRequest(BaseModel):
    query: str = Field(min_length=0, max_length=500)
    hints: HintsModel | None = None


class HubItemModel(BaseModel):
    id: str
    source: str
    org: str
    title: str
    description: str
    service_type: str
    service_url: str
    layer_id: int | None = None
    owner: str = ""
    source_field: str = ""
    tags: list[str] = []
    type_raw: str = ""
    modified: str | int | None = None
    created: str | int | None = None
    thumbnail_url: str | None = None
    extent: list[float] | None = None
    hub_url: str | None = None
    redirected_from: str | None = None
    rank_score: float = 0.0
    # hechos para que quien elige juzgue (rama arcgis-busqueda): créditos, vistas, completitud…
    credits: str = ""
    views: int | None = None
    completeness: int | None = None
    single_layer: bool | None = None
    sources: list[str] = []


class SearchResponse(BaseModel):
    items: list[HubItemModel]
    intent: str
    authority_warning: bool
    place_mismatch: bool = False
    place_queried: str | None = None
    suggested_refinements: list[dict[str, Any]] = []
    # El juicio del LLM sobre los candidatos (None = sin juicio: orden por hechos)
    criterio: str | None = None
    otras_busquedas: list[str] = []
    relevantes: int | None = None


class LoadRequest(BaseModel):
    item: HubItemModel
    layer_id: int | None = None
    layer_name: str | None = Field(default=None, max_length=200)
    # La capa COMPLETA por defecto (paginada en el servidor MCP): antes se pedían 2000 y el
    # usuario veía una muestra. Sin workspace de sesión no hay teselas: se trae lo que cabe inline.
    limit: int | None = Field(default=None, ge=1, le=500_000)
    session_id: str | None = Field(
        default=None,
        description=(
            "Sesión actual. Si el plan quedó PAUSADO (cadena 'busca X, "
            "cárgalas y píntalas de rojo'), las operaciones pendientes se "
            "despachan por el grafo sobre la capa recién cargada."
        ),
    )


class LayersRequest(BaseModel):
    service_url: str = Field(min_length=1, max_length=2000)


class LayerModel(BaseModel):
    id: int
    nombre: str
    tipo_geometria: str


class LayersResponse(BaseModel):
    layers: list[LayerModel]


class LoadResponse(BaseModel):
    type: str  # "geojson" | "imagery"
    name: str
    service_type: str
    service_url: str
    geojson: dict[str, Any] | None = None
    feature_count: int | None = None
    imagery: dict[str, Any] | None = None
    extent: list[float] | dict[str, Any] | None = None
    symbology: dict[str, Any] | None = None  # generado por SymbologyAgent para FeatureServer
    # V5 (otra temática): la capa cargada queda en el workspace de la sesión; sin su ds_ las
    # herramientas exactas (ws_*) no podían operar sobre ella y no volvía al recargar.
    dataset_id: str | None = None
    # Pendiente del acta FH: la carga trae como máximo `limit` elementos; si el servicio tiene
    # más, se dice (al usuario y, vía la procedencia del dataset, al agente).
    total_available: int | None = None
    # Capa grande (más de `workspace_inline_max_features`): teselas MVT del workspace, como desde
    # el chat; el navegador pide solo lo visible y `geojson` va vacío.
    tiles: dict[str, Any] | None = None
