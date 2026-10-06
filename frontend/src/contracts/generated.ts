
/**
 * GENERADO por scripts/gen-contracts.mjs desde contracts/schema/ — NO EDITAR.
 * Cambia el modelo pydantic, exporta (python -m geo_copilot.platform.contracts.export)
 * y regenera (npm run gen:contracts).
 */
/**
 * Raster de un MapServer/ImageServer ArcGIS, pedido por extensión (export/exportImage).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ArcGisImage".
 */
export interface ArcGisImage {
  kind: 'arcgis-image'
  service_url: string
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ChartOut".
 */
export interface ChartOut {
  /**
   * @maxItems 10000
   */
  data: {
    [k: string]: unknown
  }[]
  kind: 'chart'
  spec: ChartSpec
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ChartSpec".
 */
export interface ChartSpec {
  chart_type: 'bar' | 'line' | 'pie' | 'scatter' | 'histogram' | 'area'
  title: string | null
  x_key: string
  y_key: string | string[]
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ClassBreak".
 */
export interface ClassBreak {
  color: string
  count: number | null
  label: string
  max_value: number | null
  min_value: number | null
}
/**
 * Sin `layer_id`: limpia la selección de todas las capas.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ClearSelection".
 */
export interface ClearSelection {
  args: ClearSelectionArgs
  layer_id: string | null
  op: 'clear_selection'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ClearSelectionArgs".
 */
export interface ClearSelectionArgs {}
/**
 * FH.10: comparación con cortina (swipe): `left` a la izquierda, `right` a la derecha.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "Compare".
 */
export interface Compare {
  args: CompareArgs
  layer_id: string | null
  op: 'compare'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "CompareArgs".
 */
export interface CompareArgs {
  left: string
  right: string
}
/**
 * El SQL falló y se auto-corrigió: cuántas veces y por qué (categoría, no el error crudo).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "CorrectionInfo".
 */
export interface CorrectionInfo {
  corrections_applied: number
  final_success: boolean
  original_error: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "EndCompare".
 */
export interface EndCompare {
  args: EndCompareArgs
  layer_id: string | null
  op: 'end_compare'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "EndCompareArgs".
 */
export interface EndCompareArgs {}
/**
 * Un campo de la capa, con muestras: el contexto que el LLM usa para decidir
 * (Sprints A–F: el LLM juzga viendo esquema + muestras, sin heurísticas).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "FieldInfo".
 */
export interface FieldInfo {
  name: string
  nullable: boolean | null
  /**
   * @maxItems 5
   */
  sample:
    | []
    | [unknown]
    | [unknown, unknown]
    | [unknown, unknown, unknown]
    | [unknown, unknown, unknown, unknown]
    | [unknown, unknown, unknown, unknown, unknown]
  type: 'string' | 'integer' | 'number' | 'boolean' | 'date' | 'datetime' | 'geometry' | 'json' | 'unknown'
}
/**
 * GeoJSON en el propio documento. Transitorio (S1.4) y solo para capas chicas.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "InlineGeoJSON".
 */
export interface InlineGeoJSON {
  data: {
    [k: string]: unknown
  }
  kind: 'geojson-inline'
}
/**
 * Una capa para el mapa: su referencia + CÓMO se entrega ahora.
 *
 * `inline` (capas chicas: el GeoJSON viaja ya) o `tiles` (grandes: el navegador
 * pide solo lo visible). Un raster se dibuja por `layer.storage`. `replaces` =
 * id de la capa del mapa que esta sustituye (re-estilo: misma capa, nuevo
 * estilo; FRT-04 la elige por nombre).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "LayerArtifact".
 */
export interface LayerArtifact {
  inline: {
    [k: string]: unknown
  } | null
  kind: 'layer'
  layer: LayerRef
  replaces: string | null
  tiles: VectorTilesOut | null
}
/**
 * Una capa del núcleo.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "LayerRef".
 */
export interface LayerRef {
  bbox: [unknown, unknown, unknown, unknown] | null
  crs: string
  feature_count: number | null
  fields: FieldInfo[]
  geometry_type: string | null
  id: string
  kind: 'vector' | 'raster' | 'table'
  name: string
  provenance: Provenance
  provider: string
  storage: InlineGeoJSON | WorkspaceTable | RemoteRef | RasterTiles | ArcGisImage | WmsLayer | TableRows
  style: StyleSpec | null
  time: TimeInfo | null
}
/**
 * Cómo se obtuvo la capa: el panel "cómo se hizo" (FH.7) sale de aquí.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "Provenance".
 */
export interface Provenance {
  arguments: {
    [k: string]: unknown
  }
  capability: string
  code: string | null
  edits: ProvenanceEdit[]
  produced_at: string
  source_version: string | null
  sql: string | null
}
/**
 * Una operación que MODIFICÓ el dataset en su sitio (p. ej. añadir el área de cada
 * elemento): sin esto, "cómo se hizo" contaba solo cómo nació la capa (FH.7).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ProvenanceEdit".
 */
export interface ProvenanceEdit {
  arguments: {
    [k: string]: unknown
  }
  capability: string
  produced_at: string
}
/**
 * Tabla materializada en el workspace espacial (S2.1).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "WorkspaceTable".
 */
export interface WorkspaceTable {
  geometry_column: string | null
  kind: 'workspace-table'
  schema_name: string
  srid: number | null
  table: string
}
/**
 * Archivo remoto que el workspace puede ingerir (feature_ref de un MCP).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "RemoteRef".
 */
export interface RemoteRef {
  format: 'geoparquet' | 'flatgeobuf' | 'geojson'
  kind: 'remote-ref'
  uri: string
}
/**
 * Capa raster servida como teselas XYZ (imagery NDVI/RGB, ImageServer…).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "RasterTiles".
 */
export interface RasterTiles {
  kind: 'raster-tiles'
  legend: {
    [k: string]: unknown
  } | null
  maxzoom: number
  minzoom: number
  tile_size: 256 | 512
  url_template: string
}
/**
 * Capa WMS (GetMap por tesela con `{bbox-epsg-3857}`).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "WmsLayer".
 */
export interface WmsLayer {
  format: 'image/png' | 'image/jpeg'
  kind: 'wms'
  layers: string
  transparent: boolean
  url: string
}
/**
 * Resultado tabular sin geometría (conteos, estadísticas por grupo…).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "TableRows".
 */
export interface TableRows {
  columns: string[]
  kind: 'table-rows'
  /**
   * @maxItems 10000
   */
  rows: {
    [k: string]: unknown
  }[]
}
/**
 * Simbología de la capa: el mismo dialecto que produce el agente de
 * simbología y dibuja el frontend (tipos TS generados de este schema, F4).
 * FH.6 la vuelve editable a mano sobre este mismo modelo.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "StyleSpec".
 */
export interface StyleSpec {
  class_breaks: ClassBreak[]
  classification_field: string | null
  classification_method:
    ('equal_interval' | 'quantile' | 'natural_breaks' | 'std_deviation' | 'manual' | 'unique_values') | null
  color_scheme: string | null
  fill: {
    [k: string]: unknown
  } | null
  heatmap_intensity_field: string | null
  heatmap_radius: number | null
  label: {
    [k: string]: unknown
  } | null
  layer_title: string | null
  marker: {
    [k: string]: unknown
  } | null
  num_classes: number | null
  pinned: string[]
  reasoning: string | null
  stroke: {
    [k: string]: unknown
  } | null
  symbol_size_max: number | null
  symbol_size_min: number | null
  symbology_type: 'single_symbol' | 'unique_values' | 'graduated_colors' | 'graduated_symbols' | 'heatmap' | 'cluster'
}
/**
 * Dimensión temporal de la capa (control de tiempo, FH.10).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "TimeInfo".
 */
export interface TimeInfo {
  end: string | null
  field: string | null
  start: string | null
}
/**
 * Cómo pedir una capa grande del workspace como teselas MVT (S2.3).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "VectorTilesOut".
 */
export interface VectorTilesOut {
  fields: string[]
  source_layer: string
  url_template: string
}
/**
 * Una operación del agente sobre el mapa (re-estilar, filtrar, hacer zoom…).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "MapCommandOut".
 */
export interface MapCommandOut {
  command:
    | SetStyle
    | SetVisibility
    | SetOpacity
    | Reorder
    | SetLabel
    | ZoomTo
    | RemoveLayer
    | Select
    | ClearSelection
    | SetFilter
    | RequestInput
    | SaveView
    | Compare
    | EndCompare
    | SetTime
  kind: 'map_command'
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetStyle".
 */
export interface SetStyle {
  args: SetStyleArgs
  layer_id: string | null
  op: 'set_style'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetStyleArgs".
 */
export interface SetStyleArgs {
  style: StyleSpec
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetVisibility".
 */
export interface SetVisibility {
  args: SetVisibilityArgs
  layer_id: string | null
  op: 'set_visibility'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetVisibilityArgs".
 */
export interface SetVisibilityArgs {
  visible: boolean
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetOpacity".
 */
export interface SetOpacity {
  args: SetOpacityArgs
  layer_id: string | null
  op: 'set_opacity'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetOpacityArgs".
 */
export interface SetOpacityArgs {
  opacity: number
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "Reorder".
 */
export interface Reorder {
  args: ReorderArgs
  layer_id: string | null
  op: 'reorder'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ReorderArgs".
 */
export interface ReorderArgs {
  relative_to: string | null
  to: 'top' | 'bottom' | 'above' | 'below'
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetLabel".
 */
export interface SetLabel {
  args: SetLabelArgs
  layer_id: string | null
  op: 'set_label'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetLabelArgs".
 */
export interface SetLabelArgs {
  field: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ZoomTo".
 */
export interface ZoomTo {
  args: ZoomToArgs
  layer_id: string | null
  op: 'zoom_to'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ZoomToArgs".
 */
export interface ZoomToArgs {
  bbox: [unknown, unknown, unknown, unknown] | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "RemoveLayer".
 */
export interface RemoveLayer {
  args: RemoveLayerArgs
  layer_id: string | null
  op: 'remove_layer'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "RemoveLayerArgs".
 */
export interface RemoveLayerArgs {}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "Select".
 */
export interface Select {
  args: SelectArgs
  layer_id: string | null
  op: 'select'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SelectArgs".
 */
export interface SelectArgs {
  count: number | null
  ids: number[] | null
  mode: 'replace' | 'add' | 'toggle'
  origin: 'click' | 'box' | 'lasso' | 'table' | 'query' | 'agent' | 'link'
  where: Predicado | null
}
/**
 * Condición sobre un atributo: así viaja una selección grande (FH.2), sin ids
 * ni geometrías. El valor va como parámetro, nunca concatenado en SQL.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "Predicado".
 */
export interface Predicado {
  field: string
  op: '=' | '!=' | '>' | '>=' | '<' | '<=' | 'in' | 'contains'
  value: string | number | boolean | (string | number)[]
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetFilter".
 */
export interface SetFilter {
  args: SetFilterArgs
  layer_id: string | null
  op: 'set_filter'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetFilterArgs".
 */
export interface SetFilterArgs {
  count: number | null
  /**
   * @maxItems 10
   */
  where:
    | []
    | [Predicado]
    | [Predicado, Predicado]
    | [Predicado, Predicado, Predicado]
    | [Predicado, Predicado, Predicado, Predicado]
    | [Predicado, Predicado, Predicado, Predicado, Predicado]
    | [Predicado, Predicado, Predicado, Predicado, Predicado, Predicado]
    | [Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado]
    | [Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado]
    | [Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado]
    | [Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado, Predicado]
}
/**
 * FH.9: el agente suspende el turno y pide algo en el mapa; la respuesta del usuario
 * llega en el turno siguiente (`map_context.respuesta_mapa`), con la consulta original.
 * `layer_id` (opcional) acota pick_features a una capa.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "RequestInput".
 */
export interface RequestInput {
  args: RequestInputArgs
  layer_id: string | null
  op: 'request_input'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "RequestInputArgs".
 */
export interface RequestInputArgs {
  mode: 'pick_point' | 'draw_area' | 'pick_layer' | 'pick_features'
  prompt: string
}
/**
 * FH.10: guarda la vista actual como marcador (ir a una: `zoom_to` con su bbox).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SaveView".
 */
export interface SaveView {
  args: SaveViewArgs
  layer_id: string | null
  op: 'save_view'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SaveViewArgs".
 */
export interface SaveViewArgs {
  nombre: string
}
/**
 * FH.10: control de tiempo sobre las capas con fecha (una serie de imagery).
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetTime".
 */
export interface SetTime {
  args: SetTimeArgs
  layer_id: string | null
  op: 'set_time'
  reason: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "SetTimeArgs".
 */
export interface SetTimeArgs {
  play: boolean
  time: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ReportOut".
 */
export interface ReportOut {
  cites: string[]
  kind: 'report'
  markdown: string
}
/**
 * Un servicio encontrado en fuentes externas (discovery): se elige por número.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ServiceCard".
 */
export interface ServiceCard {
  credits: string | null
  description: string
  layer_count: number | null
  name: string
  source: string | null
  type: string
  url: string
  views: number | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ServicesOut".
 */
export interface ServicesOut {
  /**
   * @minItems 1
   */
  items: [ServiceCard, ...ServiceCard[]]
  kind: 'services'
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "Stat".
 */
export interface Stat {
  label: string
  unit?: string | null
  value: number | string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "StatsOut".
 */
export interface StatsOut {
  /**
   * @minItems 1
   */
  items: [Stat, ...Stat[]]
  kind: 'stats'
  title: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "TableOut".
 */
export interface TableOut {
  columns: string[]
  kind: 'table'
  /**
   * @maxItems 10000
   */
  preview: {
    [k: string]: unknown
  }[]
  rows_ref: string | null
  title: string | null
  total_rows: number | null
}
/**
 * Una decisión del agente (router, herramienta, reflexión…), ya saneada.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "TraceEntry".
 */
export interface TraceEntry {
  agent: string | null
  detail: string | null
  kind: string
  step: number | null
  success: boolean | null
  tool: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "QueryResponse".
 */
export interface QueryResponse {
  artifacts: (LayerArtifact | TableOut | ChartOut | StatsOut | ReportOut | ServicesOut | MapCommandOut)[]
  confidence: number | null
  contract_version: string
  correction: CorrectionInfo | null
  created_at: string
  intent: string | null
  message: string | null
  pending_approval_id: string | null
  query_id: string
  reasoning_trace: TraceEntry[] | null
  requires_approval: boolean
  session_id: string
  sql: string | null
  status: 'pending' | 'processing' | 'waiting_approval' | 'completed' | 'failed'
  /**
   * @maxItems 3
   */
  suggestions: [] | [string] | [string, string] | [string, string, string]
}
/**
 * El cuerpo de resultados de una respuesta del núcleo.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "ArtifactBundle".
 */
export interface ArtifactBundle {
  artifacts: (LayerArtifact | TableOut | ChartOut | StatsOut | ReportOut | ServicesOut | MapCommandOut)[]
  contract_version: string
  message: string
}
/**
 * Una operación del registro, como la ve el agente en el turno siguiente.
 *
 * No lleva el estado completo (la capa ya está descrita en `map_context.layers`):
 * lleva QUIÉN hizo QUÉ sobre CUÁL capa y si luego se deshizo.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "MapAction".
 */
export interface MapAction {
  args?: {
    [k: string]: unknown
  }
  at: string
  author?: 'user' | 'agent'
  layer_id?: string | null
  layer_name?: string | null
  op:
    | 'add_layer'
    | 'remove_layer'
    | 'set_style'
    | 'set_visibility'
    | 'set_opacity'
    | 'reorder'
    | 'set_label'
    | 'zoom_to'
    | 'select'
    | 'clear_selection'
    | 'rename_layer'
    | 'edit_geometry'
    | 'set_filter'
    | 'save_view'
    | 'compare'
    | 'end_compare'
    | 'set_time'
  undone?: boolean
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "FeatureCollectionArtifact".
 */
export interface FeatureCollectionArtifact {
  crs: string
  data: {
    [k: string]: unknown
  }
  kind?: 'feature_collection'
  name: string
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "FeatureRefArtifact".
 */
export interface FeatureRefArtifact {
  bbox?: [unknown, unknown, unknown, unknown] | null
  crs: string
  feature_count?: number | null
  format: 'geoparquet' | 'flatgeobuf' | 'geojson'
  kind?: 'feature_ref'
  name: string
  uri: string
}
/**
 * Cómo viene la geometría en una tabla (MCP tabulares: Snowflake, Postgres…).
 * Lo declara el servidor o el adaptador G0→G1 (§3.5); nunca se adivina.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "GeometryColumn".
 */
export interface GeometryColumn {
  column: string
  crs: string
  encoding: 'wkb' | 'wkb_hex' | 'wkt' | 'geojson' | 'latlon'
  lat_column?: string | null
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "RasterTilesArtifact".
 */
export interface RasterTilesArtifact {
  /**
   * @minItems 4
   * @maxItems 4
   */
  bounds: [unknown, unknown, unknown, unknown]
  crs: string
  datetime?: string | null
  kind?: 'raster_tiles'
  legend?: {
    [k: string]: unknown
  } | null
  maxzoom?: number
  minzoom?: number
  name: string
  tiles: string
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "StatsArtifact".
 */
export interface StatsArtifact {
  /**
   * @minItems 1
   */
  items: [Stat, ...Stat[]]
  kind?: 'stats'
}
/**
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "TableArtifact".
 */
export interface TableArtifact {
  columns: string[]
  geometry?: GeometryColumn | null
  kind?: 'table'
  name: string
  /**
   * @maxItems 10000
   */
  rows: {
    [k: string]: unknown
  }[]
}
/**
 * Lo que devuelve una tool geo-consciente.
 *
 * This interface was referenced by `Contratos`'s JSON-Schema
 * via the `definition` "GeoResult".
 */
export interface GeoResult {
  artifacts?: (FeatureCollectionArtifact | FeatureRefArtifact | RasterTilesArtifact | TableArtifact | StatsArtifact)[]
  facts?: {
    [k: string]: unknown
  }
  geo_result?: '1'
  style_hint?: {
    [k: string]: unknown
  } | null
}
