/**
 * MAP-GEOJSON-SYNC — specs de source/layers y bounds para el render GeoJSON en
 * MapLibre, con paridad pixel-perfect vs el motor anterior (el motor anterior).
 */
import { describe, it, expect } from 'vitest'
import {
  geoJsonSourceSpec,
  geometryKindOf,
  baseLayerSpecs,
  layerGeometryKind,
  boundsOf,
  type BaseLayerSpec,
  type FillLayerSpec,
  type LineLayerSpec,
  type CircleLayerSpec,
} from './maplibreGeoJson'
import type { GeoJSONFeatureCollection, LayerSymbology } from '@/types'
import type { MapLayer } from '@/stores/mapStore'

// ── helpers ────────────────────────────────────────────────────────────────

function fc(...geoms: GeoJSONFeatureCollection['features']): GeoJSONFeatureCollection {
  return { type: 'FeatureCollection', features: geoms }
}

function feature(
  type: string,
  coordinates: unknown,
  properties: Record<string, unknown> = {},
): GeoJSONFeatureCollection['features'][number] {
  return {
    type: 'Feature',
    geometry: { type: type as never, coordinates: coordinates as never },
    properties,
  }
}

const pt = (lon: number, lat: number) => feature('Point', [lon, lat])
const line = () => feature('LineString', [[0, 0], [1, 1]])
const poly = () =>
  feature('Polygon', [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]])

function makeLayer(
  data: GeoJSONFeatureCollection,
  color = '#3b82f6',
  symbology?: LayerSymbology,
): MapLayer {
  return {
    id: 'layer-1',
    name: 'Test',
    kind: 'vector-geojson',
    data,
    visible: true,
    color,
    symbology,
    featureCount: data.features.length,
    addedAt: new Date(),
  }
}

function byType<T extends BaseLayerSpec['type']>(
  specs: BaseLayerSpec[],
  t: T,
): Extract<BaseLayerSpec, { type: T }> | undefined {
  return specs.find((s) => s.type === t) as Extract<BaseLayerSpec, { type: T }> | undefined
}

// ── geoJsonSourceSpec ────────────────────────────────────────────────────────

describe('geoJsonSourceSpec', () => {
  it('envuelve el GeoJSON en un source de tipo geojson (misma referencia de data)', () => {
    const g = fc(pt(0, 0))
    const spec = geoJsonSourceSpec(g)
    // generateId: el índice de cada elemento es su id (la selección de FH.2 lo usa)
    expect(spec).toEqual({ type: 'geojson', data: g, generateId: true })
    expect(spec.data).toBe(g) // no clona: pasa la referencia al mapa
  })

  it('si las features traen su id (fid del workspace) se usa ese, no el índice', () => {
    const g = fc({ ...pt(0, 0), id: 7 } as never, { ...pt(1, 1), id: 3 } as never)
    expect(geoJsonSourceSpec(g)).toEqual({ type: 'geojson', data: g })
  })
})

// ── geometryKindOf ───────────────────────────────────────────────────────────

describe('geometryKindOf', () => {
  it('Point/MultiPoint → point', () => {
    expect(geometryKindOf(fc(pt(0, 0)))).toBe('point')
    expect(geometryKindOf(fc(feature('MultiPoint', [[0, 0], [1, 1]])))).toBe('point')
  })

  it('LineString/MultiLineString → line', () => {
    expect(geometryKindOf(fc(line()))).toBe('line')
    expect(geometryKindOf(fc(feature('MultiLineString', [[[0, 0], [1, 1]]])))).toBe('line')
  })

  it('Polygon/MultiPolygon → polygon', () => {
    expect(geometryKindOf(fc(poly()))).toBe('polygon')
    expect(
      geometryKindOf(fc(feature('MultiPolygon', [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]))),
    ).toBe('polygon')
  })

  it('familias distintas conviviendo → mixed', () => {
    expect(geometryKindOf(fc(pt(0, 0), poly()))).toBe('mixed')
    expect(geometryKindOf(fc(line(), poly()))).toBe('mixed')
    expect(geometryKindOf(fc(pt(0, 0), line(), poly()))).toBe('mixed')
  })

  it('misma familia mono/multi NO es mixed', () => {
    expect(geometryKindOf(fc(pt(0, 0), feature('MultiPoint', [[1, 1]])))).toBe('point')
  })

  it('colección vacía → mixed', () => {
    expect(geometryKindOf(fc())).toBe('mixed')
  })

  it('features sin geometría reconocible → mixed', () => {
    expect(geometryKindOf(fc(feature('GeometryCollection', [])))).toBe('mixed')
  })
})

// ── baseLayerSpecs: POLÍGONOS ────────────────────────────────────────────────

describe('baseLayerSpecs — polígonos (fill + line de contorno)', () => {
  it('produce fill + line con los defaults exactos de el motor anterior', () => {
    const specs = baseLayerSpecs('src-A', makeLayer(fc(poly()), '#10b981'))
    expect(specs.map((s) => s.type)).toEqual(['fill', 'line'])

    const fill = byType(specs, 'fill') as FillLayerSpec
    expect(fill.id).toBe('layer-1-fill')
    expect(fill.source).toBe('src-A')
    expect(fill.paint['fill-color']).toBe('#10b981') // = layer.color
    expect(fill.paint['fill-opacity']).toBe(0.4) // el mapa anterior:594
    expect(fill.filter).toBeUndefined() // capa pura: sin filtro

    const ln = byType(specs, 'line') as LineLayerSpec
    expect(ln.id).toBe('layer-1-line')
    expect(ln.source).toBe('src-A')
    expect(ln.paint['line-color']).toBe('#10b981') // stroke.color ?? layer.color
    expect(ln.paint['line-width']).toBe(2) // el mapa anterior:595
    expect(ln.paint['line-opacity']).toBe(1) // el mapa anterior:596
    expect(ln.filter).toBeUndefined()
  })

  it('respeta overrides de symbology fill/stroke', () => {
    const symb: LayerSymbology = {
      fill: { color: '#123456', opacity: 0.75 },
      stroke: { color: '#abcdef', width: 4, opacity: 0.5 },
    }
    const specs = baseLayerSpecs('s', makeLayer(fc(poly()), '#123456', symb))
    const fill = byType(specs, 'fill') as FillLayerSpec
    const ln = byType(specs, 'line') as LineLayerSpec
    expect(fill.paint['fill-opacity']).toBe(0.75)
    expect(ln.paint['line-color']).toBe('#abcdef')
    expect(ln.paint['line-width']).toBe(4)
    expect(ln.paint['line-opacity']).toBe(0.5)
  })

  it('opacity 0 no se pisa con el default (nullish, no falsy)', () => {
    const symb: LayerSymbology = {
      fill: { color: '#000', opacity: 0 },
      stroke: { color: '#000', width: 0, opacity: 0 },
    }
    const specs = baseLayerSpecs('s', makeLayer(fc(poly()), '#000', symb))
    const fill = byType(specs, 'fill') as FillLayerSpec
    const ln = byType(specs, 'line') as LineLayerSpec
    expect(fill.paint['fill-opacity']).toBe(0)
    expect(ln.paint['line-width']).toBe(0)
    expect(ln.paint['line-opacity']).toBe(0)
  })
})

// ── baseLayerSpecs: LÍNEAS ───────────────────────────────────────────────────

describe('baseLayerSpecs — líneas (solo line)', () => {
  it('produce un único line layer, sin fill ni circle', () => {
    const specs = baseLayerSpecs('src', makeLayer(fc(line()), '#ef4444'))
    expect(specs.map((s) => s.type)).toEqual(['line'])
    const ln = byType(specs, 'line') as LineLayerSpec
    expect(ln.id).toBe('layer-1-line')
    expect(ln.paint['line-color']).toBe('#ef4444')
    expect(ln.paint['line-width']).toBe(2)
    expect(ln.paint['line-opacity']).toBe(1)
    expect(ln.filter).toBeUndefined()
  })
})

// ── baseLayerSpecs: PUNTOS ───────────────────────────────────────────────────

describe('baseLayerSpecs — puntos (solo circle)', () => {
  it('produce un único circle layer con defaults de marker de el motor anterior', () => {
    const specs = baseLayerSpecs('src-P', makeLayer(fc(pt(-74, 4)), '#8b5cf6'))
    expect(specs.map((s) => s.type)).toEqual(['circle'])

    const c = byType(specs, 'circle') as CircleLayerSpec
    expect(c.id).toBe('layer-1-circle')
    expect(c.source).toBe('src-P')
    expect(c.paint['circle-color']).toBe('#8b5cf6') // = layer.color
    // baseMarkerSize 10 (el mapa anterior:606); pixelSize=diámetro → radius=size/2.
    expect(c.paint['circle-radius']).toBe(5)
    expect(c.paint['circle-stroke-color']).toBe('#ffffff') // el mapa anterior:603
    expect(c.paint['circle-stroke-width']).toBe(1.5) // el mapa anterior:604
    expect(c.paint['circle-opacity']).toBe(1) // el mapa anterior:605
    expect(c.filter).toBeUndefined()
  })

  it('marker.size custom → radius = size/2', () => {
    const symb: LayerSymbology = { marker: { color: '#000', size: 24 } }
    const specs = baseLayerSpecs('s', makeLayer(fc(pt(0, 0)), '#000', symb))
    const c = byType(specs, 'circle') as CircleLayerSpec
    expect(c.paint['circle-radius']).toBe(12)
  })

  it('respeta stroke_color/stroke_width/opacity del marker', () => {
    const symb: LayerSymbology = {
      marker: {
        color: '#111',
        size: 8,
        stroke_color: '#00ff00',
        stroke_width: 3,
        opacity: 0.5,
      },
    }
    const specs = baseLayerSpecs('s', makeLayer(fc(pt(0, 0)), '#111', symb))
    const c = byType(specs, 'circle') as CircleLayerSpec
    expect(c.paint['circle-radius']).toBe(4)
    expect(c.paint['circle-stroke-color']).toBe('#00ff00')
    expect(c.paint['circle-stroke-width']).toBe(3)
    expect(c.paint['circle-opacity']).toBe(0.5)
  })

  it('marker.opacity 0 se conserva (nullish)', () => {
    const symb: LayerSymbology = { marker: { color: '#111', opacity: 0 } }
    const specs = baseLayerSpecs('s', makeLayer(fc(pt(0, 0)), '#111', symb))
    const c = byType(specs, 'circle') as CircleLayerSpec
    expect(c.paint['circle-opacity']).toBe(0)
  })
})

// ── baseLayerSpecs: MIXTO ────────────────────────────────────────────────────

describe('baseLayerSpecs — geometría mixta (fill + line + circle con filtros)', () => {
  const specs = baseLayerSpecs('src-M', makeLayer(fc(pt(0, 0), line(), poly()), '#f59e0b'))

  it('produce las tres capas en orden fill, line, circle', () => {
    expect(specs.map((s) => s.type)).toEqual(['fill', 'line', 'circle'])
  })

  it('fill filtra por Polygon', () => {
    const fill = byType(specs, 'fill') as FillLayerSpec
    expect(fill.filter).toEqual(['==', ['geometry-type'], 'Polygon'])
  })

  it('circle filtra por Point', () => {
    const c = byType(specs, 'circle') as CircleLayerSpec
    expect(c.filter).toEqual(['==', ['geometry-type'], 'Point'])
  })

  it('line NO lleva filtro (dibuja líneas + contorno de polígonos, ignora puntos)', () => {
    const ln = byType(specs, 'line') as LineLayerSpec
    expect(ln.filter).toBeUndefined()
  })

  it('conserva colores/defaults en el caso mixto', () => {
    const fill = byType(specs, 'fill') as FillLayerSpec
    const c = byType(specs, 'circle') as CircleLayerSpec
    expect(fill.paint['fill-color']).toBe('#f59e0b')
    expect(fill.paint['fill-opacity']).toBe(0.4)
    expect(c.paint['circle-color']).toBe('#f59e0b')
    expect(c.paint['circle-radius']).toBe(5)
  })
})

// ── boundsOf ─────────────────────────────────────────────────────────────────

describe('boundsOf', () => {
  it('FeatureCollection conocido → [minLon, minLat, maxLon, maxLat]', () => {
    const g = fc(pt(-74, 4), pt(-70, 8), pt(-72, 2))
    expect(boundsOf(g)).toEqual([-74, 2, -70, 8])
  })

  it('un polígono → bbox de sus vértices', () => {
    // Polígono 0,0 → 2,2
    expect(boundsOf(fc(poly()))).toEqual([0, 0, 2, 2])
  })

  it('un único punto → bbox degenerado (min == max)', () => {
    expect(boundsOf(fc(pt(-74.0721, 4.711)))).toEqual([-74.0721, 4.711, -74.0721, 4.711])
  })

  it('colección vacía → null', () => {
    expect(boundsOf(fc())).toBeNull()
  })
})

// ── S2.3: capas teseladas del workspace ─────────────────────────────────────

describe('layerGeometryKind / baseLayerSpecs con teselas', () => {
  const tiles = (geometryType: string | null) => ({
    url: '/api/v1/tiles/ws/s/ds_0123456789abcdef/{z}/{x}/{y}.pbf',
    sourceLayer: 'dataset', geometryType, bbox: null, featureCount: 60000, fields: [],
  })

  it('usa el tipo declarado: no hay features en memoria', () => {
    const vacio = fc()
    expect(layerGeometryKind({ data: vacio, tiles: tiles('MultiPolygon') })).toBe('polygon')
    expect(layerGeometryKind({ data: vacio, tiles: tiles('LineString') })).toBe('line')
    expect(layerGeometryKind({ data: vacio, tiles: tiles('Point') })).toBe('point')
    expect(layerGeometryKind({ data: vacio, tiles: tiles(null) })).toBe('mixed')
  })

  it('una capa teselada de polígonos da fill + contorno, no las tres familias', () => {
    const layer = { ...makeLayer(fc()), tiles: tiles('Polygon') }
    expect(baseLayerSpecs('src', layer).map((s) => s.type)).toEqual(['fill', 'line'])
  })
})
