"""`LayerRef`: qué es una capa para el núcleo (plan de plataforma §2.2, S1.1).

Una capa es una REFERENCIA con metadatos, no el GeoJSON. El GeoJSON es una de las
formas de almacenamiento (`storage`), útil mientras los datos son pocos; las
otras son una tabla del workspace, un archivo remoto o una plantilla de teselas.
Sustituye los ~8 slots paralelos de `GraphState` (`geojson`, `external_geojson`,
`external_imagery`, …) en S1.4.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from geo_copilot.platform.contracts.common import MAX_INLINE_FEATURES, BBox, Crs, Strict

FieldType = Literal[
    "string", "integer", "number", "boolean", "date", "datetime", "geometry", "json", "unknown",
]


class FieldInfo(Strict):
    """Un campo de la capa, con muestras: el contexto que el LLM usa para decidir
    (Sprints A–F: el LLM juzga viendo esquema + muestras, sin heurísticas)."""

    name: str = Field(min_length=1)
    type: FieldType
    nullable: bool | None = None
    sample: list[Any] = Field(default_factory=list, max_length=5)


# ---------------------------------------------------------------------------
# Almacenamiento: dónde están los datos de la capa (discriminado por `kind`)
# ---------------------------------------------------------------------------


class InlineGeoJSON(Strict):
    """GeoJSON en el propio documento. Transitorio (S1.4) y solo para capas chicas."""

    kind: Literal["geojson-inline"] = "geojson-inline"
    data: dict[str, Any]

    @model_validator(mode="after")
    def _feature_collection_acotada(self) -> InlineGeoJSON:
        if self.data.get("type") != "FeatureCollection":
            raise ValueError("storage geojson-inline exige un FeatureCollection")
        n = len(self.data.get("features") or [])
        if n > MAX_INLINE_FEATURES:
            raise ValueError(
                f"{n} features inline superan el tope de {MAX_INLINE_FEATURES}: "
                "usar una referencia (workspace-table / remote-ref)"
            )
        return self


class WorkspaceTable(Strict):
    """Tabla materializada en el workspace espacial (S2.1)."""

    kind: Literal["workspace-table"] = "workspace-table"
    schema_name: str = Field(pattern=r"^ws_[a-z0-9_]+$")
    table: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    geometry_column: str | None = "geom"
    srid: int | None = 4326


class RemoteRef(Strict):
    """Archivo remoto que el workspace puede ingerir (feature_ref de un MCP)."""

    kind: Literal["remote-ref"] = "remote-ref"
    uri: str = Field(pattern=r"^(https?|s3|az|gs)://")
    format: Literal["geoparquet", "flatgeobuf", "geojson"]


class RasterTiles(Strict):
    """Capa raster servida como teselas XYZ (imagery NDVI/RGB, ImageServer…)."""

    kind: Literal["raster-tiles"] = "raster-tiles"
    url_template: str
    minzoom: int = Field(default=0, ge=0, le=24)
    maxzoom: int = Field(default=22, ge=0, le=24)
    tile_size: Literal[256, 512] = 256
    #: Leyenda del raster tal como la da el productor (rampa, rango, unidades).
    legend: dict[str, Any] | None = None
    #: Cómo pintarla EN EL CLIENTE desde sus COG públicos (bandas, escala, rangos, rampa); solo de
    #: servidores de confianza y con URLs https. Sin él, o si falla, se usan las teselas.
    cog: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _plantilla_xyz(self) -> RasterTiles:
        faltan = [p for p in ("{z}", "{x}", "{y}") if p not in self.url_template]
        if faltan:
            raise ValueError(f"url_template sin {faltan}: no es una plantilla XYZ")
        if self.minzoom > self.maxzoom:
            raise ValueError("minzoom > maxzoom")
        return self


class ArcGisImage(Strict):
    """Raster de un MapServer/ImageServer ArcGIS, pedido por extensión (export/exportImage)."""

    kind: Literal["arcgis-image"] = "arcgis-image"
    #: URL base del servicio (…/MapServer o …/ImageServer), sin /export.
    service_url: str = Field(pattern=r"^https?://")


class WmsLayer(Strict):
    """Capa WMS (GetMap por tesela con `{bbox-epsg-3857}`)."""

    kind: Literal["wms"] = "wms"
    url: str = Field(pattern=r"^https?://")
    layers: str = Field(min_length=1)
    format: Literal["image/png", "image/jpeg"] = "image/png"
    transparent: bool = True


class TableRows(Strict):
    """Resultado tabular sin geometría (conteos, estadísticas por grupo…)."""

    kind: Literal["table-rows"] = "table-rows"
    columns: list[str]
    rows: list[dict[str, Any]] = Field(max_length=MAX_INLINE_FEATURES)


Storage = Annotated[
    InlineGeoJSON | WorkspaceTable | RemoteRef | RasterTiles | ArcGisImage | WmsLayer | TableRows,
    Field(discriminator="kind"),
]

_ALMACENES_POR_TIPO: dict[str, set[str]] = {
    "vector": {"geojson-inline", "workspace-table", "remote-ref"},
    "raster": {"raster-tiles", "arcgis-image", "wms"},
    "table": {"table-rows", "workspace-table", "remote-ref"},
}


# ---------------------------------------------------------------------------
# Estilo, tiempo y procedencia
# ---------------------------------------------------------------------------


class ClassBreak(Strict):
    label: str
    color: str
    min_value: float | None = None
    max_value: float | None = None
    count: int | None = None


class StyleSpec(Strict):
    """Simbología de la capa: el mismo dialecto que produce el agente de
    simbología y dibuja el frontend (tipos TS generados de este schema, F4).
    FH.6 la vuelve editable a mano sobre este mismo modelo."""

    layer_title: str | None = None
    symbology_type: Literal[
        "single_symbol", "unique_values", "graduated_colors",
        "graduated_symbols", "heatmap", "cluster",
    ] = "single_symbol"
    fill: dict[str, Any] | None = None
    stroke: dict[str, Any] | None = None
    marker: dict[str, Any] | None = None
    classification_field: str | None = None
    classification_method: Literal[
        "equal_interval", "quantile", "natural_breaks",
        "std_deviation", "manual", "unique_values",
    ] | None = None
    class_breaks: list[ClassBreak] = Field(default_factory=list)
    color_scheme: str | None = None
    num_classes: int | None = Field(default=None, ge=1, le=20)
    #: graduated_symbols (puntos): tamaño mínimo y máximo del marcador, en px.
    symbol_size_min: float | None = None
    symbol_size_max: float | None = None
    #: heatmap
    heatmap_radius: int | None = None
    heatmap_intensity_field: str | None = None
    label: dict[str, Any] | None = None
    #: Por qué el agente eligió este estilo (se muestra, no se interpreta).
    reasoning: str | None = None
    #: FH.6: ajustes que el usuario fijó a mano; el agente los respeta.
    pinned: list[str] = Field(default_factory=list)


class TimeInfo(Strict):
    """Dimensión temporal de la capa (control de tiempo, FH.10)."""

    #: Con zona horaria (V5 FH.10: una fecha sin zona no es date-time válido para el cliente
    #: y rechazaba la respuesta entera).
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    field: str | None = None


class ProvenanceEdit(Strict):
    """Una operación que MODIFICÓ el dataset en su sitio (p. ej. añadir el área de cada
    elemento): sin esto, "cómo se hizo" contaba solo cómo nació la capa (FH.7)."""

    capability: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    produced_at: datetime


class Provenance(Strict):
    """Cómo se obtuvo la capa: el panel "cómo se hizo" (FH.7) sale de aquí."""

    capability: str = Field(min_length=1)  # p. ej. "core.query_database", "mcp.imagery.imagery_ndvi"
    arguments: dict[str, Any] = Field(default_factory=dict)
    produced_at: datetime
    sql: str | None = None
    code: str | None = None
    source_version: str | None = None  # escena, versión del servicio, fecha del dataset
    edits: list[ProvenanceEdit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LayerRef
# ---------------------------------------------------------------------------


class LayerRef(Strict):
    """Una capa del núcleo."""

    id: str = Field(min_length=1)
    #: Nombre para humanos. ESTABLE: re-estilar no lo cambia (hallazgo H3 de F0).
    name: str = Field(min_length=1)
    kind: Literal["vector", "raster", "table"]
    provider: str = Field(min_length=1)
    crs: Crs
    storage: Storage
    provenance: Provenance
    geometry_type: str | None = None
    bbox: BBox | None = None
    feature_count: int | None = Field(default=None, ge=0)
    fields: list[FieldInfo] = Field(default_factory=list)
    style: StyleSpec | None = None
    time: TimeInfo | None = None

    @model_validator(mode="after")
    def _almacen_compatible(self) -> LayerRef:
        permitidos = _ALMACENES_POR_TIPO[self.kind]
        if self.storage.kind not in permitidos:
            raise ValueError(
                f"una capa {self.kind!r} no puede guardarse como {self.storage.kind!r} "
                f"(permitidos: {sorted(permitidos)})"
            )
        return self
