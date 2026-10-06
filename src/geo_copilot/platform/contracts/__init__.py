"""Contratos del núcleo (S1.1 del plan de plataforma).

Una sola definición de "qué es una capa" (`LayerRef`), "qué devuelve una tool
geo" (`GeoResult`) y "qué recibe el frontend" (`ArtifactBundle`), versionada con
`CONTRACT_VERSION`. Los JSON Schema se generan de aquí
(`python -m geo_copilot.platform.contracts.export`) y un test falla si los
archivos de `contracts/schema/` quedan desactualizados.
"""

from geo_copilot.platform.contracts.artifacts import (
    Artifact,
    ArtifactBundle,
    ChartOut,
    ChartSpec,
    LayerArtifact,
    MapCommandOut,
    ReportOut,
    StatsOut,
    TableOut,
)
from geo_copilot.platform.contracts.common import CONTRACT_VERSION, MAX_INLINE_FEATURES
from geo_copilot.platform.contracts.geo_result import (
    FeatureCollectionArtifact,
    FeatureRefArtifact,
    GeoArtifact,
    GeometryColumn,
    GeoResult,
    RasterTilesArtifact,
    Stat,
    StatsArtifact,
    TableArtifact,
)
from geo_copilot.platform.contracts.layers import (
    FieldInfo,
    InlineGeoJSON,
    LayerRef,
    Provenance,
    ProvenanceEdit,
    RasterTiles,
    RemoteRef,
    StyleSpec,
    TableRows,
    TimeInfo,
    WorkspaceTable,
)
from geo_copilot.platform.contracts.map_ops import (
    Compare,
    CompareArgs,
    EndCompare,
    MapAction,
    MapCommand,
    Predicado,
    Reorder,
    RequestInput,
    RequestInputArgs,
    SaveView,
    SaveViewArgs,
    Select,
    SetFilter,
    SetLabel,
    SetOpacity,
    SetStyle,
    SetStyleArgs,
    SetTime,
    SetTimeArgs,
    SetVisibility,
    ZoomTo,
)
from geo_copilot.platform.contracts.respuesta import CorrectionInfo, QueryResponse, TraceEntry

__all__ = [
    "CONTRACT_VERSION", "MAX_INLINE_FEATURES",
    "Artifact", "ArtifactBundle", "ChartOut", "ChartSpec", "LayerArtifact",
    "MapCommandOut", "ReportOut", "StatsOut", "TableOut",
    "FeatureCollectionArtifact", "FeatureRefArtifact", "GeoArtifact", "GeoResult",
    "GeometryColumn", "RasterTilesArtifact", "Stat", "StatsArtifact", "TableArtifact",
    "FieldInfo", "InlineGeoJSON", "LayerRef", "Provenance", "ProvenanceEdit", "RasterTiles", "RemoteRef",
    "StyleSpec", "TableRows", "TimeInfo", "WorkspaceTable",
    "MapAction", "MapCommand", "Predicado", "Reorder", "RequestInput", "RequestInputArgs", "SaveView", "SaveViewArgs", "Compare", "CompareArgs", "EndCompare", "SetTime", "SetTimeArgs", "Select", "SetFilter", "SetLabel", "SetOpacity", "SetStyle", "SetStyleArgs",
    "SetVisibility", "ZoomTo",
    "CorrectionInfo", "QueryResponse", "TraceEntry",
]
