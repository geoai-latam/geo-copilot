"""`GeoResult`: el envoltorio que un servidor MCP geo-consciente (G1/G2) pone en
`structuredContent` (plan de plataforma §3.4).

Reglas duras que el modelo hace cumplir:
  1. El CRS se declara — ningún artefacto geográfico lo tiene por defecto.
  2. Por encima de `MAX_INLINE_FEATURES` se devuelve `feature_ref`, no el GeoJSON.
  3. `facts` son hechos para que el LLM narre; nunca instrucciones (el núcleo los
     delimita en el prompt como datos no confiables, §3.6).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from geo_copilot.platform.contracts.common import MAX_INLINE_FEATURES, BBox, Crs, Strict


class FeatureCollectionArtifact(Strict):
    kind: Literal["feature_collection"] = "feature_collection"
    name: str = Field(min_length=1)
    crs: Crs
    data: dict[str, Any]

    @model_validator(mode="after")
    def _chica_y_valida(self) -> FeatureCollectionArtifact:
        if self.data.get("type") != "FeatureCollection":
            raise ValueError("feature_collection exige data.type == 'FeatureCollection'")
        n = len(self.data.get("features") or [])
        if n > MAX_INLINE_FEATURES:
            raise ValueError(
                f"{n} features superan el tope inline ({MAX_INLINE_FEATURES}): "
                "devolver un artefacto feature_ref"
            )
        return self


class FeatureRefArtifact(Strict):
    kind: Literal["feature_ref"] = "feature_ref"
    name: str = Field(min_length=1)
    uri: str = Field(pattern=r"^(https?|s3|az|gs)://")
    format: Literal["geoparquet", "flatgeobuf", "geojson"]
    crs: Crs
    feature_count: int | None = Field(default=None, ge=0)
    bbox: BBox | None = None


class RasterTilesArtifact(Strict):
    kind: Literal["raster_tiles"] = "raster_tiles"
    name: str = Field(min_length=1)
    #: Plantilla XYZ relativa al servidor que la emite; el núcleo la proxifica (S3.5).
    tiles: str
    #: CRS de `bounds` (las teselas XYZ son Web Mercator; los límites se declaran aparte).
    crs: Crs
    bounds: BBox
    minzoom: int = Field(default=0, ge=0, le=24)
    maxzoom: int = Field(default=22, ge=0, le=24)
    legend: dict[str, Any] | None = None
    #: FH.10: el instante que retrata (fecha de la escena, ISO). Arma series temporales.
    datetime: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _plantilla_xyz(self) -> RasterTilesArtifact:
        faltan = [p for p in ("{z}", "{x}", "{y}") if p not in self.tiles]
        if faltan:
            raise ValueError(f"tiles sin {faltan}: no es una plantilla XYZ")
        return self


class GeometryColumn(Strict):
    """Cómo viene la geometría en una tabla (MCP tabulares: Snowflake, Postgres…).
    Lo declara el servidor o el adaptador G0→G1 (§3.5); nunca se adivina."""

    column: str = Field(min_length=1)
    encoding: Literal["wkb", "wkb_hex", "wkt", "geojson", "latlon"]
    crs: Crs
    #: Para `latlon`: la columna de latitud (`column` es la de longitud).
    lat_column: str | None = None

    @model_validator(mode="after")
    def _latlon_completo(self) -> GeometryColumn:
        if self.encoding == "latlon" and not self.lat_column:
            raise ValueError("encoding 'latlon' exige lat_column (column = longitud)")
        return self


class TableArtifact(Strict):
    kind: Literal["table"] = "table"
    name: str = Field(min_length=1)
    columns: list[str]
    rows: list[dict[str, Any]] = Field(max_length=MAX_INLINE_FEATURES)
    geometry: GeometryColumn | None = None


class Stat(Strict):
    label: str = Field(min_length=1)
    value: float | int | str | None
    unit: str | None = None


class StatsArtifact(Strict):
    kind: Literal["stats"] = "stats"
    items: list[Stat] = Field(min_length=1)


GeoArtifact = Annotated[
    FeatureCollectionArtifact | FeatureRefArtifact | RasterTilesArtifact
    | TableArtifact | StatsArtifact,
    Field(discriminator="kind"),
]


class GeoResult(Strict):
    """Lo que devuelve una tool geo-consciente."""

    geo_result: Literal["1"] = "1"
    artifacts: list[GeoArtifact] = Field(default_factory=list)
    #: Hechos para narrar (escena, % nubes, fecha…). Datos, no instrucciones.
    facts: dict[str, Any] = Field(default_factory=dict)
    #: Sugerencia de estilo; decide el agente de simbología con esquema + muestras.
    style_hint: dict[str, Any] | None = None
