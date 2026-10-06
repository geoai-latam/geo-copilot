/**
 * MAP-VECTOR-TILES — capas vectoriales TESELADAS desde el endpoint MVT del
 * backend (§5 vector tiling). En vez de traer todo el GeoJSON de una tabla
 * grande, MapLibre pide teselas `{z}/{x}/{y}.pbf` que PostGIS genera al vuelo
 * (ST_AsMVT). Solo se transfiere lo visible; ideal para tablas enormes (los
 * ~933k lotes catastrales).
 *
 * FUNCIONES PURAS: devuelven specs JSON de MapLibre; no importan 'maplibre-gl'
 * (sin WebGL en jsdom). El `source-layer` de cada layer DEBE coincidir con el
 * nombre de capa que el backend pasa a ST_AsMVT (`schema.table`).
 */

export type TileGeometryKind = 'polygon' | 'line' | 'point'

export interface VectorSourceSpec {
  type: 'vector'
  tiles: string[]
  minzoom: number
  maxzoom: number
}

export interface VectorLayerSpec {
  id: string
  type: 'fill' | 'line' | 'circle'
  source: string
  'source-layer': string
  paint: Record<string, unknown>
}

const TILE_BASE = '/api/v1/tiles'

/** Nombre de capa del vector tile = el que el backend pasa a ST_AsMVT. */
export function vectorSourceLayerName(schema: string, table: string): string {
  return `${schema}.${table}`
}

/** Source vectorial que apunta al endpoint MVT del backend. */
export function vectorTileSourceSpec(
  schema: string,
  table: string,
  tileBase: string = TILE_BASE,
): VectorSourceSpec {
  return {
    type: 'vector',
    tiles: [`${tileBase}/${schema}/${table}/{z}/{x}/{y}.pbf`],
    minzoom: 0,
    maxzoom: 22,
  }
}

/**
 * Layers para dibujar una tabla teselada según su tipo de geometría.
 * - polygon → fill + line (contorno)
 * - line    → line
 * - point   → circle
 * Todas llevan `source-layer` = schema.table.
 */
export function vectorTileLayerSpecs(
  sourceId: string,
  schema: string,
  table: string,
  geomType: TileGeometryKind,
  color: string,
): VectorLayerSpec[] {
  const sourceLayer = vectorSourceLayerName(schema, table)
  const base = (
    type: VectorLayerSpec['type'],
    suffix: string,
    paint: Record<string, unknown>,
  ): VectorLayerSpec => ({
    id: `${sourceId}-${suffix}`,
    type,
    source: sourceId,
    'source-layer': sourceLayer,
    paint,
  })

  if (geomType === 'point') {
    return [
      base('circle', 'circle', {
        'circle-color': color,
        'circle-radius': 4,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1,
      }),
    ]
  }
  if (geomType === 'line') {
    return [base('line', 'line', { 'line-color': color, 'line-width': 1.5 })]
  }
  // polygon
  return [
    base('fill', 'fill', { 'fill-color': color, 'fill-opacity': 0.4 }),
    base('line', 'outline', { 'line-color': color, 'line-width': 0.6 }),
  ]
}
