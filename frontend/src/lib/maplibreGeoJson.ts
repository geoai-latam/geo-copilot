/**
 * MAP-GEOJSON-SYNC — renderiza una capa GeoJSON del store como source + layers
 * de MapLibre GL, replicando la simbología single-symbol del motor anterior
 * (el motor anterior: el mapa anterior L593-724 + motor anteriorSymbology.applyPointEntityStyle).
 *
 * Funciones PURAS: devuelven objetos plano (specs JSON de MapLibre) o parsean
 * GeoJSON. NO se importa 'maplibre-gl' en runtime — jsdom no tiene WebGL — así
 * que estos specs corren en tests sin instanciar el mapa. Los tipos de layer se
 * declaran localmente (loose) para no acoplar a la lib.
 *
 * PARIDAD PIXEL-PERFECT con el motor anterior (el motor anterior):
 *   - fillOpacity   = symbology?.fill?.opacity   ?? 0.4   (el mapa anterior:594)
 *   - strokeWidth   = symbology?.stroke?.width    ?? 2     (el mapa anterior:595)
 *   - strokeOpacity = symbology?.stroke?.opacity  ?? 1.0   (el mapa anterior:596)
 *   - markerStrokeColor = symbology?.marker?.stroke_color ?? '#ffffff' (el mapa anterior:603)
 *   - markerStrokeWidth = symbology?.marker?.stroke_width ?? 1.5        (el mapa anterior:604)
 *   - markerOpacity     = symbology?.marker?.opacity      ?? 1          (el mapa anterior:605)
 *   - baseMarkerSize    = symbology?.marker?.size         ?? 10         (el mapa anterior:606)
 *
 * El pixelSize de el motor anterior (applyPointEntityStyle, la simbología anterior:88) es el
 * DIÁMETRO del punto; MapLibre 'circle-radius' es el RADIO, de modo que para
 * paridad exacta radius = size / 2 (default 10 → 5).
 */
import bbox from '@turf/bbox'
import type { GeoJSONFeatureCollection } from '@/types'
import type { MapLayer } from '@/stores/mapStore'

// ──────────────────────────────────────────────────────────────────────────
// Tipos loose de MapLibre (no importamos la lib; specs de estilo son JSON).
// ──────────────────────────────────────────────────────────────────────────

export interface GeoJsonSourceSpec {
  type: 'geojson'
  data: GeoJSONFeatureCollection
  generateId?: boolean
}

/** Expresión/filtro de MapLibre — árbol JSON arbitrario. */
export type MapLibreExpression = unknown

export interface FillLayerSpec {
  id: string
  type: 'fill'
  source: string
  filter?: MapLibreExpression
  paint: {
    'fill-color': string
    'fill-opacity': number
  }
}

export interface LineLayerSpec {
  id: string
  type: 'line'
  source: string
  filter?: MapLibreExpression
  paint: {
    'line-color': string
    'line-width': number
    'line-opacity': number
  }
}

export interface CircleLayerSpec {
  id: string
  type: 'circle'
  source: string
  filter?: MapLibreExpression
  paint: {
    'circle-color': string
    'circle-radius': number
    'circle-stroke-color': string
    'circle-stroke-width': number
    'circle-opacity': number
  }
}

export type BaseLayerSpec = FillLayerSpec | LineLayerSpec | CircleLayerSpec

export type GeometryKind = 'point' | 'line' | 'polygon' | 'mixed'

// ──────────────────────────────────────────────────────────────────────────
// SOURCE
// ──────────────────────────────────────────────────────────────────────────

/** Source MapLibre para el GeoJSON de una capa. */
export function geoJsonSourceSpec(geojson: GeoJSONFeatureCollection): GeoJsonSourceSpec {
  // FH.2: cada elemento necesita un id para seleccionarlo y resaltarlo con ['id'].
  // El GeoJSON de un dataset del workspace ya lo trae (su fid, el mismo del
  // backend y de las teselas); si no, el índice (generateId).
  if (tieneIds(geojson)) return { type: 'geojson', data: geojson }
  return { type: 'geojson', data: geojson, generateId: true }
}

/** ¿Todas las features traen un `id` numérico propio? */
export function tieneIds(geojson: GeoJSONFeatureCollection): boolean {
  const fs = geojson.features ?? []
  return fs.length > 0 && fs.every((f) => typeof (f as { id?: unknown }).id === 'number')
}

// ──────────────────────────────────────────────────────────────────────────
// GEOMETRÍA
// ──────────────────────────────────────────────────────────────────────────

/**
 * Clasifica la geometría dominante de un FeatureCollection inspeccionando
 * features[].geometry.type. Point/MultiPoint → 'point', LineString/Multi →
 * 'line', Polygon/Multi → 'polygon'. Si conviven varias familias → 'mixed'.
 * Colección vacía o sin geometrías reconocibles → 'mixed' (sin familia única).
 */
export function geometryKindOf(geojson: GeoJSONFeatureCollection): GeometryKind {
  const kinds = new Set<'point' | 'line' | 'polygon'>()
  for (const feature of geojson?.features ?? []) {
    const t = feature?.geometry?.type
    switch (t) {
      case 'Point':
      case 'MultiPoint':
        kinds.add('point')
        break
      case 'LineString':
      case 'MultiLineString':
        kinds.add('line')
        break
      case 'Polygon':
      case 'MultiPolygon':
        kinds.add('polygon')
        break
      default:
        break
    }
  }
  if (kinds.size === 1) {
    // El único elemento del set.
    return kinds.values().next().value as GeometryKind
  }
  return 'mixed'
}

// ──────────────────────────────────────────────────────────────────────────
// SIMBOLOGÍA BASE (single-symbol)
// ──────────────────────────────────────────────────────────────────────────

// Filtros por geometry-type — solo se aplican en 'mixed' para separar familias
// dentro de un único source (en capas puras no hacen falta y se omiten).
const POLYGON_FILTER: MapLibreExpression = ['==', ['geometry-type'], 'Polygon']
const POINT_FILTER: MapLibreExpression = ['==', ['geometry-type'], 'Point']

/**
 * Specs de layer MapLibre para la simbología BASE (single-symbol) de una capa,
 * según su geometría. Replica el render single-symbol del motor anterior:
 *   - polígonos → fill (relleno) + line (contorno)
 *   - líneas    → line
 *   - puntos    → circle
 *   - mixta     → las tres, filtradas por geometry-type (un line layer sin
 *                 filtro dibuja además el contorno de los polígonos, igual que
 *                 el motor anterior estiliza polygon.outline y polyline por separado).
 */
/**
 * S2.3: familia de geometría de una capa. Una capa teselada no trae features
 * en memoria: su tipo viene declarado por el backend (LayerRef.geometry_type).
 */
export function layerGeometryKind(layer: Pick<MapLayer, 'data' | 'tiles'>): GeometryKind {
  const t = layer.tiles?.geometryType
  if (t) {
    if (/Point/.test(t)) return 'point'
    if (/LineString/.test(t)) return 'line'
    if (/Polygon/.test(t)) return 'polygon'
    return 'mixed'
  }
  return geometryKindOf(layer.data)
}

export function baseLayerSpecs(sourceId: string, layer: MapLayer): BaseLayerSpec[] { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const symb = layer.symbology
  const color = layer.color

  const fillOpacity = symb?.fill?.opacity ?? 0.4
  const strokeColor = symb?.stroke?.color ?? color
  const strokeWidth = symb?.stroke?.width ?? 2
  const strokeOpacity = symb?.stroke?.opacity ?? 1

  const markerSize = symb?.marker?.size ?? 10
  const markerStrokeColor = symb?.marker?.stroke_color ?? '#ffffff'
  const markerStrokeWidth = symb?.marker?.stroke_width ?? 1.5
  const markerOpacity = symb?.marker?.opacity ?? 1

  const kind = layerGeometryKind(layer)
  const mixed = kind === 'mixed'
  const specs: BaseLayerSpec[] = []

  // FILL — polígonos.
  if (kind === 'polygon' || mixed) {
    const fill: FillLayerSpec = {
      id: `${layer.id}-fill`,
      type: 'fill',
      source: sourceId,
      paint: {
        'fill-color': color,
        'fill-opacity': fillOpacity,
      },
    }
    if (mixed) fill.filter = POLYGON_FILTER
    specs.push(fill)
  }

  // LINE — contorno de polígonos y/o stroke de líneas. En 'mixed' va SIN filtro
  // porque un line layer dibuja el borde de los polígonos y las líneas, e ignora
  // los puntos por sí solo.
  if (kind === 'polygon' || kind === 'line' || mixed) {
    specs.push({
      id: `${layer.id}-line`,
      type: 'line',
      source: sourceId,
      paint: {
        'line-color': strokeColor,
        'line-width': strokeWidth,
        'line-opacity': strokeOpacity,
      },
    })
  }

  // CIRCLE — puntos. pixelSize (diámetro) del motor anterior → radius = size/2.
  if (kind === 'point' || mixed) {
    const circle: CircleLayerSpec = {
      id: `${layer.id}-circle`,
      type: 'circle',
      source: sourceId,
      paint: {
        'circle-color': color,
        'circle-radius': markerSize / 2,
        'circle-stroke-color': markerStrokeColor,
        'circle-stroke-width': markerStrokeWidth,
        'circle-opacity': markerOpacity,
      },
    }
    if (mixed) circle.filter = POINT_FILTER
    specs.push(circle)
  }

  return specs
}

// ──────────────────────────────────────────────────────────────────────────
// BOUNDS
// ──────────────────────────────────────────────────────────────────────────

/**
 * bbox [minLon, minLat, maxLon, maxLat] del GeoJSON vía @turf/bbox, o null si
 * no hay features / no hay coordenadas finitas (turf devuelve ±Infinity para
 * colecciones vacías).
 */
export function boundsOf(
  geojson: GeoJSONFeatureCollection,
): [number, number, number, number] | null {
  if (!geojson?.features?.length) return null
  const box = bbox(geojson as unknown as Parameters<typeof bbox>[0])
  const [minLon, minLat, maxLon, maxLat] = box
  if (
    !Number.isFinite(minLon) ||
    !Number.isFinite(minLat) ||
    !Number.isFinite(maxLon) ||
    !Number.isFinite(maxLat)
  ) {
    return null
  }
  return [minLon, minLat, maxLon, maxLat]
}
