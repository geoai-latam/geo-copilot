/**
 * Types for GEO_COPILOT Frontend
 */

// Query types
export interface QueryRequest {
  query: string;
  session_id?: string;
  parameters?: Record<string, unknown>;
  // Fase A: estado del mapa/UI (capas activas, feature seleccionada, viewport).
  map_context?: import('@/utils/mapContext').MapContext;
}

/**
 * F4 (S4.1): la respuesta de /query es la del CONTRATO, generada del modelo del
 * backend (src/contracts/generated.ts). Nada de tipos a mano para resultados.
 */
import type { QueryResponse } from '@/contracts'
export type { QueryResponse }

export type QueryStatus =
  | 'pending'
  | 'processing'
  | 'waiting_approval'
  | 'completed'
  | 'failed';


export interface FoundService {
  name: string;
  description: string;
  url: string;
  type: string; // "MapServer" | "FeatureServer" | "ImageServer" | otro
  layer_count?: number | null;
  /** De quién es (créditos u organización) y cuántas vistas tiene: hechos para elegir. */
  credits?: string | null;
  views?: number | null;
}

/**
 * Estimated cost of executing a SQL/Python action. All fields optional;
 * UI renders dashes for missing values.
 */
export interface ImpactEstimate {
  /** Approximate row count the action will touch/return. */
  rows?: number;
  /** Raw planner cost (PostgreSQL EXPLAIN cost). */
  cost?: number;
  /** Wall-clock estimate in milliseconds. */
  time_ms?: number;
  /** Qualitative bucket: 'low' | 'medium' | 'high'. */
  risk?: 'low' | 'medium' | 'high';
}

// Symbology from backend
/**
 * Configuración de simbología emitida por el SymbologyAgent.
 *
 * El frontend usa `symbology_type` para decidir CÓMO renderizar:
 * - `single_symbol`: aplica fill/stroke/marker a todos los features
 * - `unique_values`: colorea cada feature según `classification_field` ←→ class_breaks[i].label
 * - `graduated_colors`: colorea cada feature según en qué break cae su valor numérico
 * - `graduated_symbols`: tamaño de marker proporcional al break (solo puntos)
 * - `heatmap`: densidad por kernel; `heatmap_intensity_field` pondera
 * - `cluster`: agrupa puntos cercanos
 */
export type SymbologyType =
  | 'single_symbol'
  | 'unique_values'
  | 'graduated_colors'
  | 'graduated_symbols'
  | 'heatmap'
  | 'cluster';

export interface ClassBreak {
  min_value?: number | null;
  max_value?: number | null;
  label: string;
  color: string;
  count?: number;
}

export interface LayerSymbology {
  layer_title?: string;
  symbology_type?: SymbologyType;
  fill?: { color: string; opacity?: number };
  stroke?: { color: string; width?: number; opacity?: number };
  marker?: {
    type?: 'circle' | 'square' | 'diamond' | 'triangle' | 'star' | 'marker';
    color: string;
    size?: number;
    stroke_color?: string;
    stroke_width?: number;
    opacity?: number;
  };
  // Clasificación temática (solo si symbology_type usa breaks)
  classification_field?: string | null;
  classification_method?:
    | 'equal_interval' | 'quantile' | 'natural_breaks'
    | 'std_deviation' | 'manual' | 'unique_values' | null;
  class_breaks?: ClassBreak[];
  num_classes?: number;
  // graduated_symbols (solo puntos)
  symbol_size_min?: number;
  symbol_size_max?: number;
  // heatmap
  heatmap_radius?: number;
  heatmap_intensity_field?: string | null;
  // metadata
  reasoning?: string;
  /** FH.6: rampa de color (la misma que eligen el agente o el editor). */
  color_scheme?: string | null;
  /** FH.6: lo que el usuario fijó a mano (campos del StyleSpec); el agente lo ve como hecho. */
  pinned?: string[];
}

// GeoJSON types
export interface GeoJSONFeatureCollection {
  type: 'FeatureCollection';
  features: GeoJSONFeature[];
}

export interface GeoJSONFeature {
  type: 'Feature';
  geometry: GeoJSONGeometry;
  properties: Record<string, unknown>;
}

export interface GeoJSONGeometry {
  type: 'Point' | 'LineString' | 'Polygon' | 'MultiPoint' | 'MultiLineString' | 'MultiPolygon';
  coordinates: number[] | number[][] | number[][][] | number[][][][];
}

// Approval types
export interface ApprovalRequest {
  action: ApprovalAction;
  // Fase 1 SEC-3: el backend ata la aprobación a la sesión que originó
  // la solicitud HITL. Sin este campo el endpoint responde 422.
  session_id: string;
  reason?: string;
  modified_content?: string;
}

export type ApprovalAction = 'approve' | 'reject' | 'modify';

export interface ApprovalStatus {
  approval_id: string;
  content_type: string;  // 'sql' | 'python' | 'data' | 'api'
  content: string;       // The SQL or code to approve
  status?: string;
  risk_level?: string;   // 'low' | 'medium' | 'high'
  warnings?: string[];   // List of identified risks
  title?: string;        // Title of the approval request
  description?: string;  // Description of what will be executed
  action_type?: string;  // 'sql_execution' | 'code_execution' etc.
  created_at: string;
  /**
   * Server-side estimate so the HITL modal can show real numbers
   * instead of "—". See docs/design/frontend-backend-contract.md §3.
   */
  impact_estimate?: ImpactEstimate;
  /** Policy name applied to gate this approval (e.g. "readonly_buffer_500m"). */
  policy?: string;
  /** Sandbox the action will run in (e.g. "gis_readonly"). */
  sandbox?: string;
  /** F7: turno del agente que espera esta aprobación (null si ya no hay turno que retomar). */
  turno_id?: string | null;
}

export interface ApprovalResult {
  approval_id: string;
  action: ApprovalAction;
  success: boolean;
  message: string;
}

// Session types
export interface Session {
  session_id: string;
  created_at: string;
  message_count: number;
  last_active: string;
}

export interface SessionContext {
  session_id: string;
  entities: string[];
  analysis_type?: string;
  last_query?: string;
}

// Visualization types
export interface Visualization {
  type: VisualizationType;
  title?: string;
  config: VisualizationConfig;
  data?: VisualizationData;
}

export interface VisualizationConfig {
  chart_type?: 'bar' | 'line' | 'pie' | 'scatter';
  x_key?: string;
  y_key?: string;
  columns?: string[];
  feature_count?: number;
  // Extra properties from backend
  extras?: Record<string, unknown>;
}

export type VisualizationData = Record<string, unknown>[] | unknown;

export type VisualizationType =
  | 'map'
  | 'chart'
  | 'table'
  | 'stat_card'
  | 'geojson_layer'
  | 'empty';

// Entity types
export interface Entity {
  name: string;
  display_name: string;
  description: string;
  category: string;
  geometry_type?: string;
  aliases?: string[];
  field_count?: number;
  has_geometry?: boolean;
}

// Chat types
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  status?: 'sending' | 'sent' | 'error';
  response?: QueryResponse;
  approval?: ApprovalStatus;
  /** FH.7: capas que dejó esta respuesta en el mapa (su `activa` en los enlaces). */
  capas?: string[];
  /** F7: la página se recargó con este turno en marcha; su resultado (por WS) va a este mensaje. */
  turnoPendiente?: string;
}

// WebSocket types
export interface WSMessage {
  type: WSMessageType;
  session_id: string;
  data: unknown;
}

// Espejo de geo_copilot.api.websocket.WSMessageType (backend).
//
// Cliente → Servidor: query, approval, cancel, ping
// Servidor → Cliente: status, progress, result, error, approval_request,
// pong, retry_*, plan_created, step_started, step_completed,
// execution_cancelled.
export type WSMessageType =
  // Cliente → Servidor
  | 'query'
  | 'approval'
  | 'cancel'
  | 'ping'
  // Servidor → Cliente
  | 'status'
  | 'progress'
  | 'result'
  | 'error'
  | 'approval_request'
  | 'pong'
  // Autonomía / progreso
  | 'retry_started'
  | 'retry_correction'
  | 'retry_success'
  | 'retry_failed'
  | 'plan_created'
  | 'step_started'
  | 'step_completed'
  | 'execution_cancelled';

// Health types
export interface HealthStatus {
  status: string;
  version: string;
  components: Record<string, string>;
  timestamp: string;
}
