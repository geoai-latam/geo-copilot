"""Artefactos: lo que el núcleo le entrega al frontend (plan de plataforma §2.4).

Una respuesta es una lista de artefactos tipados, no un `dict[str, Any]` con
campos por fuente (`geojson`, `external_imagery`, `found_services`…). El
frontend los dibuja con un registro de renderers (S4.2): una capa se dibuja según
`layer.storage.kind`, así que no hace falta un segundo "descriptor de capa".
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from geo_copilot.platform.contracts.common import CONTRACT_VERSION, MAX_INLINE_FEATURES, Strict
from geo_copilot.platform.contracts.geo_result import Stat
from geo_copilot.platform.contracts.layers import LayerRef
from geo_copilot.platform.contracts.map_ops import MapCommand


class VectorTilesOut(Strict):
    """Cómo pedir una capa grande del workspace como teselas MVT (S2.3)."""

    url_template: str
    source_layer: str = "dataset"
    fields: list[str] = Field(default_factory=list)


class LayerArtifact(Strict):
    """Una capa para el mapa: su referencia + CÓMO se entrega ahora.

    `inline` (capas chicas: el GeoJSON viaja ya) o `tiles` (grandes: el navegador
    pide solo lo visible). Un raster se dibuja por `layer.storage`. `replaces` =
    id de la capa del mapa que esta sustituye (re-estilo: misma capa, nuevo
    estilo; FRT-04 la elige por nombre).
    """

    kind: Literal["layer"] = "layer"
    layer: LayerRef
    inline: dict[str, Any] | None = None
    tiles: VectorTilesOut | None = None
    replaces: str | None = None


class TableOut(Strict):
    kind: Literal["table"] = "table"
    title: str | None = None
    columns: list[str]
    #: Primeras filas para pintar ya; el resto se pagina desde el workspace (FH.5).
    preview: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_INLINE_FEATURES)
    total_rows: int | None = Field(default=None, ge=0)
    #: Dataset del que salen las filas (paginación contra el workspace).
    rows_ref: str | None = None


class ChartSpec(Strict):
    chart_type: Literal["bar", "line", "pie", "scatter", "histogram", "area"]
    x_key: str
    y_key: str | list[str]
    title: str | None = None


class ChartOut(Strict):
    kind: Literal["chart"] = "chart"
    spec: ChartSpec
    data: list[dict[str, Any]] = Field(max_length=MAX_INLINE_FEATURES)


class StatsOut(Strict):
    kind: Literal["stats"] = "stats"
    title: str | None = None
    items: list[Stat] = Field(min_length=1)


class ReportOut(Strict):
    kind: Literal["report"] = "report"
    markdown: str
    #: Referencias a capas/features citadas (`layer:<id>` o `layer:<id>#<feature>`, FH.7).
    cites: list[str] = Field(default_factory=list)


class ServiceCard(Strict):
    """Un servicio encontrado en fuentes externas (discovery): se elige por número."""

    name: str
    url: str = ""
    #: "FeatureServer" | "MapServer" | "ImageServer" | dataset de un portal…
    type: str = "desconocido"
    description: str = ""
    layer_count: int | None = Field(default=None, ge=0)
    source: str | None = None
    #: De quién es (créditos declarados u organización) y cuánto se usa: la tarjeta los muestra para
    #: que el usuario juzgue (antes mostraba un trozo del dominio, p. ej. «services7»).
    credits: str | None = None
    views: int | None = Field(default=None, ge=0)


class ServicesOut(Strict):
    kind: Literal["services"] = "services"
    items: list[ServiceCard] = Field(min_length=1)


class MapCommandOut(Strict):
    """Una operación del agente sobre el mapa (re-estilar, filtrar, hacer zoom…)."""

    kind: Literal["map_command"] = "map_command"
    command: MapCommand


Artifact = Annotated[
    LayerArtifact | TableOut | ChartOut | StatsOut | ReportOut | ServicesOut | MapCommandOut,
    Field(discriminator="kind"),
]


class ArtifactBundle(Strict):
    """El cuerpo de resultados de una respuesta del núcleo."""

    contract_version: str = CONTRACT_VERSION
    message: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
