/**
 * Respuestas de `/query` VÁLIDAS POR CONTRATO para tests (vitest y mocks E2E).
 *
 * Los mocks a mano se desalinean del backend sin que nadie se entere; aquí se
 * parte del ejemplo que exporta el propio backend y solo se sobreescribe lo que
 * cada test necesita. `contracts.test.ts` valida que lo que sale de aquí pasa
 * la misma validación que `runQuery`.
 */
import ejemplo from './examples/query_response.json' with { type: 'json' }
import type { LayerArtifact, QueryResponse, StyleSpec } from './generated'

type Artifact = QueryResponse['artifacts'][number]

type Capa = Extract<Artifact, { kind: 'layer' }>
const base = ejemplo as unknown as QueryResponse
const capaBase = base.artifacts.find((a) => a.kind === 'layer') as Capa

const ESTILO_VACIO: StyleSpec = {
  ...(Object.fromEntries(
    Object.entries(capaBase.layer.style ?? {}).map(([k, v]) => [k, Array.isArray(v) ? [] : null]),
  ) as unknown as StyleSpec),
  symbology_type: 'single_symbol',
}

/** Un `StyleSpec` COMPLETO (el backend siempre manda todos los campos) con lo que el test necesite. */
export function estilo(o: Partial<StyleSpec> = {}): StyleSpec {
  return { ...ESTILO_VACIO, ...o }
}

/** Sobre completo; `artifacts` vacío salvo que se pasen. */
export function respuesta(extra: Partial<QueryResponse> = {}): QueryResponse {
  return {
    ...base,
    query_id: 'q1',
    session_id: 's1',
    intent: 'query_data',
    message: 'listo',
    sql: null,
    reasoning_trace: [],
    artifacts: [],
    ...extra,
  }
}

export interface OpcionesCapa {
  id?: string
  name?: string
  inline?: Record<string, unknown> | null
  tiles?: LayerArtifact['tiles']
  style?: Partial<StyleSpec> | null
  replaces?: string | null
  featureCount?: number
  geometryType?: string | null
  bbox?: [number, number, number, number] | null
}

/** Una capa vectorial del workspace. */
export function capa(o: OpcionesCapa = {}): Capa {
  const features = (o.inline as { features?: unknown[] } | null | undefined)?.features
  return {
    ...capaBase,
    inline: o.inline === undefined ? null : o.inline,
    tiles: o.tiles ?? null,
    replaces: o.replaces ?? null,
    layer: {
      ...capaBase.layer,
      id: o.id ?? capaBase.layer.id,
      name: o.name ?? capaBase.layer.name,
      feature_count: o.featureCount ?? features?.length ?? 0,
      geometry_type: (o.geometryType ?? null) as Capa['layer']['geometry_type'],
      bbox: o.bbox ?? null,
      style: o.style === null || o.style === undefined ? null : { ...ESTILO_VACIO, ...o.style },
    },
  }
}

/** Una capa raster: XYZ (p. ej. NDVI de un MCP) o, con `arcgis`, un ImageServer. */
export function capaRaster(o: {
  id?: string; name?: string; url?: string; arcgis?: string; bbox?: [number, number, number, number]
  /** Procedencia (p. ej. `mcp.imagery.imagery_ndvi`) y sus argumentos. */
  capability?: string; arguments?: Record<string, unknown>
} = {}): Capa {
  return {
    kind: 'layer',
    inline: null,
    tiles: null,
    replaces: null,
    layer: {
      ...capaBase.layer,
      id: o.id ?? 'rs_ndvi',
      kind: 'raster',
      name: o.name ?? 'NDVI',
      feature_count: null,
      geometry_type: null,
      bbox: o.bbox ?? null,
      crs: 'EPSG:3857',
      provider: 'mcp:imagery',
      style: null,
      provenance: {
        ...capaBase.layer.provenance!,
        capability: o.capability ?? 'mcp.imagery.imagery_ndvi',
        arguments: o.arguments ?? {},
      },
      storage: o.arcgis
        ? { kind: 'arcgis-image', service_url: o.arcgis }
        : {
            kind: 'raster-tiles',
            url_template: o.url ?? '/api/v1/proxy/mcp/imagery/tiles/{z}/{x}/{y}.png',
            legend: null,
            minzoom: 0,
            maxzoom: 18,
            tile_size: 256,
          },
    } as Capa['layer'],
  }
}
