/* eslint-disable max-lines -- deuda congelada (F1 del plan de calidad): partir por responsabilidad, no crecer */
/**
 * API Client Service
 *
 * Solo expone los métodos que tienen caller real en la UI. Espejo de
 * los endpoints del backend (FastAPI) con su prefijo `/api/v1`.
 */
import type {
  QueryRequest,
  QueryResponse,
  ApprovalRequest,
  ApprovalResult,
  Session,
  SessionContext,
  Entity,
  HealthStatus,
  GeoJSONFeatureCollection,
} from '@/types';
import type {
  DiscoveryHints,
  DiscoveryLoadResponse,
  DiscoveryRegionsResponse,
  DiscoverySearchResponse,
  HubItem,
  DiscoveryLayer,
} from '@/types/discovery';
import type { Provenance } from '@/contracts';
import { logger } from '@/utils/logger';
import { cabecerasAuth, sesionRechazada } from '@/lib/auth';

const API_BASE = '/api/v1';

// F6: el frontend no lleva ninguna clave. Con OIDC cada petición lleva el token del usuario
// (Authorization: Bearer, lib/auth.ts); sin OIDC (desarrollo) el backend no exige nada. La API
// key queda para clientes de servicio, nunca en el navegador.

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  logger.debug(`[API] ${options.method || 'GET'} ${url}`);

  try {
    const enviar = () => fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...cabecerasAuth(),
        ...options.headers,
      },
    });
    let response = await enviar();
    // F6: token caducado o revocado → una renovación y un reintento; si no, a la pantalla de entrada
    if (response.status === 401 && (await sesionRechazada())) {
      response = await enviar();
    }

    logger.debug(`[API] Response: ${response.status}`);

    if (!response.ok) {
      // Auditoría 2026-09-08 §5 (5): el 413 lo devuelve nginx, no FastAPI, así
      // que llega como HTML y el `.catch(() => ({}))` de abajo lo dejaba en
      // "Error: Payload Too Large" — que no dice ni qué pasó ni qué hacer. Lo
      // que pasó es que el map_context llevaba demasiadas capas adjuntas.
      if (response.status === 413) {
        throw new ApiError(
          413,
          'La consulta llevaba demasiados datos del mapa adjuntos y el servidor la rechazó. Oculta o quita alguna capa y vuelve a intentar.',
        );
      }
      const error = await response.json().catch(() => ({}));
      logger.error(`[API] Error:`, error);
      throw new ApiError(
        response.status,
        error.detail || error.error || `Error: ${response.statusText}`,
        error.detail
      );
    }

    return response.json();
  } catch (err) {
    logger.error(`[API] Fetch failed:`, err);
    throw err;
  }
}

// POST /query/  — único endpoint de query implementado en backend.
// (Los stubs GET /query/{id} y POST /query/{id}/cancel devuelven 404;
// la cancelación real va por WebSocket con type:"cancel".)
export const queryApi = {
  // FE4: `signal` permite abortar la petición HTTP desde el cliente (botón
  // "Detener"). Antes el cancel solo iba por WS y no afectaba a esta llamada.
  process: async (data: QueryRequest, signal?: AbortSignal): Promise<QueryResponse> => {
    return request<QueryResponse>('/query/', {
      method: 'POST',
      body: JSON.stringify(data),
      signal,
    });
  },
};

// POST /approval/{id} — aprobar / rechazar / modificar.
export const approvalApi = {
  submit: async (
    approvalId: string,
    data: ApprovalRequest,
  ): Promise<ApprovalResult> => {
    return request<ApprovalResult>(`/approval/${approvalId}`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },
};

// POST /session/ y POST /session/{id}/reset.
export const sessionApi = {
  create: async (): Promise<Session> => {
    return request<Session>('/session/', {
      method: 'POST',
    });
  },

  /** ¿Sigue viva la sesión en el backend? (S0.3: el WebSocket ya no la recrea). */
  exists: async (sessionId: string): Promise<boolean> => {
    try {
      await request<Session>(`/session/${sessionId}`);
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return false;
      throw error;
    }
  },

  reset: async (sessionId: string): Promise<SessionContext> => {
    return request<SessionContext>(`/session/${sessionId}/reset`, {
      method: 'POST',
    });
  },
};

// E2.4: un dataset del workspace de la sesión listo para el mapa (misma forma
// que `results` de /query: geojson inline o teselas).
export interface WorkspaceCapa {
  layer_ref: { id: string; name: string; feature_count?: number | null } | null;
  geojson: GeoJSONFeatureCollection | null;
  tiles: {
    url: string;
    source_layer: string;
    geometry_type: string | null;
    bbox: [number, number, number, number] | null;
    feature_count: number | null;
    fields: string[];
  } | null;
}

/** FH.10: lo que mide la herramienta Medir. */
export interface Medicion {
  tipo: 'area' | 'longitud'
  area_m2?: number
  area_ha?: number
  perimetro_m?: number
  longitud_m?: number
  longitud_km?: number
}

/** FH.7: un paso de «cómo se hizo» (el dataset o una de sus entradas). */
export interface PasoProcedencia {
  dataset_id: string
  nombre?: string
  disponible: boolean
  provenance?: Provenance
}

/** FH.5: una página de filas de un dataset (tabla vinculada de una capa grande). */
export interface FilasPagina {
  total: number
  offset: number
  filas: Array<{ fid: number; properties: Record<string, unknown>; bbox: [number, number, number, number] | null }>
}

/** FH.5: estadística de un campo (con el filtro de la capa). */
export interface EstadisticaCampo {
  campo: string
  numerico: boolean
  n: number
  con_valor: number
  unicos: number
  min?: number | null
  max?: number | null
  media?: number | null
  frecuentes: Array<{ valor: string; n: number }>
}

/** FH.6: el diseño que el usuario elige en el editor de estilo. */
export interface DisenoEstilo {
  symbology_type: string
  classification_field?: string | null
  classification_method?: string | null
  num_classes?: number | null
  color_scheme?: string | null
  fill_color?: string | null
}

export const workspaceApi = {
  rampas: async (): Promise<Record<string, string[]>> =>
    (await request<{ rampas: Record<string, string[]> }>('/workspace/rampas')).rampas,
  /** El StyleSpec de un diseño manual, calculado con el mismo código que usa el agente. */
  estilo: async (
    sessionId: string,
    cuerpo: { diseno: DisenoEstilo; dataset_id?: string; geojson?: GeoJSONFeatureCollection; titulo?: string; pinned: string[] },
  ): Promise<{ style: Record<string, unknown> }> =>
    request(`/workspace/${encodeURIComponent(sessionId)}/estilo`, { method: 'POST', body: JSON.stringify(cuerpo) }),
  filas: async (
    sessionId: string, datasetId: string,
    q: { offset?: number; limit?: number; orden?: string | null; desc?: boolean; filtro?: unknown[] | null; ids?: number[] | null },
  ): Promise<FilasPagina> => {
    const p = new URLSearchParams()
    if (q.offset) p.set('offset', String(q.offset))
    if (q.limit) p.set('limit', String(q.limit))
    if (q.orden) p.set('orden', q.orden)
    if (q.desc) p.set('desc', 'true')
    if (q.filtro?.length) p.set('filtro', JSON.stringify(q.filtro))
    if (q.ids) p.set('ids', JSON.stringify(q.ids))
    return request<FilasPagina>(
      `/workspace/${encodeURIComponent(sessionId)}/datasets/${encodeURIComponent(datasetId)}/filas?${p}`)
  },
  estadistica: async (sessionId: string, datasetId: string, campo: string, filtro?: unknown[] | null) => {
    const p = new URLSearchParams({ campo })
    if (filtro?.length) p.set('filtro', JSON.stringify(filtro))
    return request<EstadisticaCampo>(
      `/workspace/${encodeURIComponent(sessionId)}/datasets/${encodeURIComponent(datasetId)}/estadistica?${p}`)
  },
  /** FH.7: «cómo se hizo» — la procedencia del dataset y la de sus entradas. */
  procedencia: async (sessionId: string, datasetId: string): Promise<{ pasos: PasoProcedencia[] }> => {
    return request<{ pasos: PasoProcedencia[] }>(
      `/workspace/${encodeURIComponent(sessionId)}/datasets/${encodeURIComponent(datasetId)}/procedencia`)
  },
  capa: async (sessionId: string, datasetId: string): Promise<WorkspaceCapa> => {
    return request<WorkspaceCapa>(
      `/workspace/${encodeURIComponent(sessionId)}/datasets/${encodeURIComponent(datasetId)}/capa`,
    );
  },
  /** FH.10: medir una figura trazada (geodésico exacto en PostGIS; no crea dataset). */
  medir: async (sessionId: string, geometry: unknown): Promise<Medicion> =>
    request<Medicion>(`/workspace/${encodeURIComponent(sessionId)}/medir`, {
      method: 'POST', body: JSON.stringify({ geometry }),
    }),
  /** FH.3: un dibujo del usuario → dataset del workspace (`provider: sketch`). */
  crearDibujo: async (sessionId: string, name: string, geojson: GeoJSONFeatureCollection): Promise<WorkspaceCapa> => {
    return request<WorkspaceCapa>(`/workspace/${encodeURIComponent(sessionId)}/sketches`, {
      method: 'POST',
      body: JSON.stringify({ name, geojson }),
    });
  },
  /** FH.3: renombrar un dataset o guardar los vértices editados de un dibujo (por fid). */
  editarDataset: async (
    sessionId: string, datasetId: string, cambio: { name?: string; geojson?: GeoJSONFeatureCollection },
  ): Promise<WorkspaceCapa> => {
    return request<WorkspaceCapa>(
      `/workspace/${encodeURIComponent(sessionId)}/datasets/${encodeURIComponent(datasetId)}`,
      { method: 'PATCH', body: JSON.stringify(cambio) },
    );
  },
};

// Schema completo de la BD descubierto en runtime (introspector).
// Usado por el DatabaseSchemaPanel del drawer "BD".
export interface DbColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  is_geometry: boolean;
  geometry_type: string | null;
  srid: number | null;
}
export interface DbTable {
  name: string;
  qualified_name: string;
  estimated_rows: number | null;
  geometry_column: string | null;
  geometry_type: string | null;
  srid: number | null;
  columns: DbColumn[];
}
export interface DbSchema {
  name: string;
  tables: DbTable[];
}
export interface TablesResponse {
  schemas: DbSchema[];
  total_tables: number;
  total_schemas: number;
  connected: boolean;
}

// GET /metadata/entities — listado dinámico desde la BD.
export const metadataApi = {
  listEntities: async (): Promise<{ entities: Entity[]; total: number }> => {
    return request<{ entities: Entity[]; total: number }>(
      '/metadata/entities',
    );
  },
  /**
   * Schema completo de la BD conectada — descubierto en runtime, sin
   * hardcoding. El DatabaseSchemaPanel lo consume para el árbol del
   * drawer "BD".
   */
  listTables: async (): Promise<TablesResponse> => {
    return request<TablesResponse>('/metadata/tables');
  },
};

// Discovery endpoints (ArcGIS Hub Open Data)
export const discoveryApi = {
  search: async (
    query: string,
    hints?: DiscoveryHints,
  ): Promise<DiscoverySearchResponse> => {
    return request<DiscoverySearchResponse>('/discovery/search', {
      method: 'POST',
      body: JSON.stringify({ query, hints }),
    });
  },

  load: async (
    item: HubItem,
    // sin `limit`: la capa completa (antes se pedían 2000 y el usuario veía una muestra)
    options?: { layerId?: number; layerName?: string; limit?: number; sessionId?: string },
  ): Promise<DiscoveryLoadResponse> => {
    return request<DiscoveryLoadResponse>('/discovery/load', {
      method: 'POST',
      body: JSON.stringify({
        item,
        layer_id: options?.layerId ?? item.layer_id ?? null,
        layer_name: options?.layerName ?? null,
        limit: options?.limit ?? null,
        // #35: si el plan quedó pausado (cadena buscar→cargar→pintar), el
        // backend despacha las operaciones pendientes sobre esta capa.
        session_id: options?.sessionId ?? null,
      }),
    });
  },

  health: async (): Promise<{ status: string }> => {
    return request<{ status: string }>('/discovery/health');
  },

  /** Capas con geometría de un FeatureServer con varias: quien carga elige (antes se cargaba la 0). */
  layers: async (serviceUrl: string): Promise<DiscoveryLayer[]> => {
    const r = await request<{ layers: DiscoveryLayer[] }>('/discovery/layers', {
      method: 'POST',
      body: JSON.stringify({ service_url: serviceUrl }),
    });
    return r.layers;
  },

  regions: async (): Promise<DiscoveryRegionsResponse> => {
    return request<DiscoveryRegionsResponse>('/discovery/regions');
  },
};

// GET /health
export const healthApi = {
  check: async (): Promise<HealthStatus> => {
    const response = await fetch('/health');
    return response.json();
  },
};


// E3.2: panel de herramientas GENÉRICO de los servidores MCP conectados. El
// formulario se genera desde el `input_schema` de cada tool; `run` ejecuta la
// MISMA capacidad que usa el agente y devuelve `results` con la forma de /query.
export interface JsonSchemaProp {
  type?: string | string[];
  anyOf?: JsonSchemaProp[];
  enum?: (string | number | null)[];
  title?: string;
  description?: string;
  default?: unknown;
  format?: string;
  minimum?: number;
  maximum?: number;
}

export interface McpToolInfo {
  server: string;
  tool: string;
  herramienta: string;
  description: string;
  input_schema: { properties?: Record<string, JsonSchemaProp>; required?: string[] };
  geo: { inputs?: Record<string, { accepts?: string[] }>; outputs?: string[] } | null;
  riesgo: string;
  estado: string;
}

export interface McpLegend {
  type?: string;
  field?: string;
  min?: number;
  max?: number;
  nota?: string;
  colores?: string[];
  clases?: { valor: number; etiqueta: string; color: string }[];
}

export interface McpRunResult {
  success: boolean;
  message: string | null;
  facts: Record<string, unknown>;
  results: {
    geojson?: GeoJSONFeatureCollection | null;
    data?: { results?: Record<string, unknown>[] } | null;
    visualization?: { type: string } | null;
    external_imagery?: {
      service_url: string;
      name?: string;
      extent?: { xmin: number; ymin: number; xmax: number; ymax: number } | null;
      legend?: McpLegend | null;
      /** Pintarla en el navegador desde sus COG (servidores de confianza). */
      cog?: import('@/lib/cogNavegador').CogSpec | null;
      /** S4.4: tool + argumentos que produjeron la capa. */
      provenance?: { capability: string; arguments?: Record<string, unknown> } | null;
    } | null;
    layer_name?: string | null;
    layer_ref?: { id: string; name?: string; feature_count?: number } | null;
    tiles?: {
      url: string;
      source_layer: string;
      geometry_type?: string;
      bbox?: [number, number, number, number];
      feature_count?: number;
      fields?: string[];
    } | null;
  };
}

/** Un servidor MCP tal como lo ve el hub (GET /connections, S4.5). */
export interface McpServerStatus {
  id: string;
  url: string;
  /** F6: de la plataforma (config del servidor) o dada de alta por tu organización. */
  de?: 'plataforma' | 'organizacion';
  /** Nivel de conformidad: G0 (MCP cualquiera) · G1 (GeoResult) · G2 (+ teselas). */
  conformance: string;
  /** disponible · no_disponible · desconocido. */
  estado: string;
  ultimo_error: string | null;
  servidor: { name?: string; version?: string; instructions?: string } | null;
  tools: {
    nombre: string;
    herramienta: string;
    habilitada: boolean;
    /** Por qué está deshabilitada (p. ej. su descripción cambió: pinning). */
    motivo: string | null;
    riesgo: string;
    geo: boolean;
  }[];
}

/** F6 (S6.2): una conexión de la organización tal como se puede mostrar (nunca su credencial). */
export interface ConexionOrg {
  id: string;
  url: string;
  description: string | null;
  adapter: string | null;
  trust: string;
  tiene_credencial: boolean;
  creada_por: string;
  creada: string;
}

export interface AltaConexion {
  id: string;
  url: string;
  description?: string;
  /** Se guarda cifrada y no vuelve nunca en una respuesta. */
  credencial?: string;
  adapter?: 'tabular_geo';
  trust?: 'trusted' | 'untrusted';
}

export const mcpApi = {
  connections: async (): Promise<{ servers: McpServerStatus[]; de_la_organizacion?: ConexionOrg[]; puede_administrar?: boolean }> =>
    request('/connections'),
  /** F6: alta de una conexión de tu organización (solo administración). */
  alta: async (body: AltaConexion): Promise<ConexionOrg & { estado: McpServerStatus | null }> =>
    request('/connections', { method: 'POST', body: JSON.stringify(body) }),
  /** F6: baja de una conexión de tu organización (solo administración). */
  baja: async (id: string): Promise<{ ok: boolean }> =>
    request(`/connections/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** Re-aprobar una tool deshabilitada por el pinning (§3.6.2). */
  approve: async (server: string, tool: string): Promise<{ ok: boolean }> =>
    request<{ ok: boolean }>(
      `/connections/${encodeURIComponent(server)}/tools/${encodeURIComponent(tool)}/approve`,
      { method: 'POST' },
    ),
  tools: async (): Promise<{ tools: McpToolInfo[] }> =>
    request<{ tools: McpToolInfo[] }>('/connections/tools'),
  run: async (
    server: string,
    tool: string,
    body: { session_id: string; arguments: Record<string, unknown>; map_context?: unknown },
  ): Promise<McpRunResult> =>
    request<McpRunResult>(
      `/connections/${encodeURIComponent(server)}/tools/${encodeURIComponent(tool)}/run`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
};

/** FH.8: una acción del menú contextual = una capacidad del registro aplicable a lo señalado. */
export interface Accion extends McpToolInfo {
  titulo: string;
  /** El argumento que recibe lo señalado (el primero que acepta algo del mapa). */
  objetivo: string;
  costo: string;
  geo: { inputs?: Record<string, { accepts?: string[]; geometry_types?: string[] }>; outputs?: string[] } | null;
}

export const accionesApi = {
  /** Las capacidades aplicables a algo de ese tipo de geometría (sale de lo que declaran). */
  listar: async (geometria?: string | null): Promise<{ acciones: Accion[] }> =>
    request<{ acciones: Accion[] }>(`/acciones${geometria ? `?geometria=${encodeURIComponent(geometria)}` : ''}`),
  run: async (
    herramienta: string,
    body: { session_id: string; arguments: Record<string, unknown>; map_context?: unknown },
  ): Promise<McpRunResult> =>
    request<McpRunResult>(`/acciones/${encodeURIComponent(herramienta)}/run`, {
      method: 'POST', body: JSON.stringify(body),
    }),
};

/** FH.11: un proyecto guardado (sin su estado, en el listado). */
export interface ProyectoResumen { id: string; nombre: string; workspace_id: string; updated_at: string; capas: number }

export const proyectosApi = {
  listar: async (): Promise<{ proyectos: ProyectoResumen[] }> => request<{ proyectos: ProyectoResumen[] }>('/proyectos'),
  guardar: async (sessionId: string, nombre: string, estado: unknown): Promise<{ id: string; nombre: string }> =>
    request<{ id: string; nombre: string }>('/proyectos', {
      method: 'POST', body: JSON.stringify({ session_id: sessionId, nombre, estado }),
    }),
  abrir: async (id: string): Promise<{ id: string; nombre: string; session_id: string; estado: Record<string, unknown> }> =>
    request(`/proyectos/${encodeURIComponent(id)}/abrir`, { method: 'POST' }),
};

// F6 (S6.4, E6.5): registro de auditoría de la organización (solo administración)
export interface EntradaAuditoria {
  id: number
  ts: string
  actor_sub: string
  actor_nombre: string
  actor_via: string
  accion: string
  recurso: string
  session_id: string | null
  resultado: string
  detalle: Record<string, unknown>
}

export const auditoriaApi = {
  listar: async (antesDe?: number): Promise<{ organizacion: string; entradas: EntradaAuditoria[]; siguiente: number | null }> =>
    request(`/auditoria?limite=50${antesDe ? `&antes_de=${antesDe}` : ''}`),
  verificar: async (): Promise<{ integra: boolean; encadenada: boolean }> => request('/auditoria/verificar'),
};
