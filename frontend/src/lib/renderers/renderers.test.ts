/**
 * F4 (S4.2) — un renderer por tipo de capa: specs puros, opacidad, extensión,
 * leyenda y fila del panel. Y el store único: orden de dibujo común a todos los
 * tipos (un raster puede ir encima de un vector).
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { EMPTY_FC, useMapStore, type MapLayer } from '@/stores/mapStore'
import { RENDERERS, firmaDeCapa, rendererDe } from './index'
import { wmsTileUrl } from './raster'

const CTX = { origin: 'http://app', arcgisProxyBase: '/api/v1/proxy/imagery' }

const fc = (n: number, geom: 'Point' | 'Polygon' = 'Point') => ({
  type: 'FeatureCollection' as const,
  features: Array.from({ length: n }, (_, i) => ({
    type: 'Feature' as const,
    geometry:
      geom === 'Point'
        ? { type: 'Point' as const, coordinates: [-74 + i * 0.01, 4.6] }
        : { type: 'Polygon' as const, coordinates: [[[-74, 4.6], [-73.9, 4.6], [-73.9, 4.7], [-74, 4.6]]] },
    properties: { uso: i % 2 ? 'com' : 'res', area: i },
  })),
})

function capa(p: Partial<MapLayer>): MapLayer {
  return {
    id: 'c1', name: 'Capa', kind: 'vector-geojson', data: EMPTY_FC, visible: true, color: '#3b82f6',
    featureCount: 0, addedAt: new Date(0), opacity: 1, ...p,
  }
}

describe('registro', () => {
  it('hay un renderer por cada tipo de capa del store', () => {
    expect(Object.keys(RENDERERS).sort()).toEqual(['arcgis-image', 'raster-xyz', 'vector-geojson', 'vector-mvt', 'wms'])
    for (const [kind, r] of Object.entries(RENDERERS)) expect(r.kind).toBe(kind)
  })
})

describe('vector-geojson', () => {
  const r = rendererDe('vector-geojson')

  it('specs: source geojson + capas de estilo con la clasificación', () => {
    const l = capa({
      data: fc(3, 'Polygon'),
      symbology: {
        symbology_type: 'unique_values', classification_field: 'uso',
        class_breaks: [{ label: 'res', color: '#111111' }, { label: 'com', color: '#222222' }],
      },
    })
    const { source, layers } = r.buildSpecs(l, CTX)
    expect(source.type).toBe('geojson')
    // estilo (fill + line) y resaltado de la selección (sel-fill + sel-line), vacío por ahora
    expect(layers.map((s) => s.id)).toEqual(['c1-fill', 'c1-line', 'c1-sel-fill', 'c1-sel-line'])
    expect(layers.slice(2).every((s) => JSON.stringify(s.filter) === '["boolean",false]')).toBe(true)
    expect(layers.every((s) => s.source === 'c1')).toBe(true)
    expect(JSON.stringify(layers[0].paint?.['fill-color'])).toContain('#222222')
  })

  it('cluster sobre polígonos usa centroides; etiquetas si hay campo', () => {
    const l = capa({ data: fc(2, 'Polygon'), symbology: { symbology_type: 'cluster' } })
    const { source, layers } = r.buildSpecs(l, CTX)
    expect((source as { cluster?: boolean }).cluster).toBe(true)
    expect(layers.some((s) => s.id === 'c1-unclustered')).toBe(true)
    const conEtiqueta = r.buildSpecs(capa({ data: fc(2), labelField: 'uso' }), CTX)
    expect(conEtiqueta.layers[conEtiqueta.layers.length - 1]?.type).toBe("symbol")
  })

  it('opacidad = factor de la capa × la opacidad propia del estilo', () => {
    const op = r.opacityPaint(capa({ opacity: 0.5, symbology: { fill: { color: '#fff', opacity: 0.8 } } }))
    expect(op).toContainEqual(['fill', 'fill-opacity', 0.4])
  })

  it('extensión, leyenda de clases y fila del panel', () => {
    const l = capa({ data: fc(3), symbology: { layer_title: 'Por uso', class_breaks: [{ label: 'res', color: '#1', count: 2 }] } })
    expect(r.bounds(l)?.[0]).toBeCloseTo(-74)
    expect(r.legend(l)).toEqual({ title: 'Por uso', items: [{ label: 'res', color: '#1', count: 2 }] })
    expect(r.panelRow(l)).toMatchObject({ sub: '3 features', labelFields: ['uso', 'area'] })
    expect(r.featureCount(l)).toBe(3)
  })
})

describe('vector-mvt', () => {
  const r = rendererDe('vector-mvt')
  const l = capa({
    kind: 'vector-mvt',
    tiles: { url: '/api/v1/tiles/ws/s/ds_1/{z}/{x}/{y}.pbf', sourceLayer: 'dataset', geometryType: 'MultiPolygon',
             bbox: [-75, 4, -74, 5], featureCount: 41033, fields: ['estado'] },
  })

  it('source vectorial con URL absoluta (el worker no resuelve relativas) y source-layer', () => {
    const { source, layers } = r.buildSpecs(l, CTX)
    expect(source).toMatchObject({ type: 'vector', tiles: ['http://app/api/v1/tiles/ws/s/ds_1/{z}/{x}/{y}.pbf'] })
    expect(layers.every((s) => s['source-layer'] === 'dataset')).toBe(true)
  })

  it('cuenta, extensión y campos salen de la descripción de las teselas', () => {
    expect(r.featureCount(l)).toBe(41033)
    expect(r.bounds(l)).toEqual([-75, 4, -74, 5])
    expect(r.panelRow(l)).toMatchObject({ sub: '41.033 features · teselas', labelFields: ['estado'] })
  })
})

describe('rasters', () => {
  it('raster-xyz: la plantilla tal cual, opacidad raster y leyenda de rampa', () => {
    const r = rendererDe('raster-xyz')
    const l = capa({ kind: 'raster-xyz', url: '/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png', opacity: 0.6,
                     name: 'NDVI', legend: { field: 'NDVI', min: 0, max: 0.8 }, extent: { xmin: -74.2, ymin: 4.5, xmax: -74, ymax: 4.7 } })
    const { source, layers } = r.buildSpecs(l, CTX)
    expect(source).toEqual({ type: 'raster', tiles: ['/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png'], tileSize: 256 })
    expect(layers).toEqual([{ id: 'c1-raster', type: 'raster', source: 'c1', paint: {} }])
    expect(r.opacityPaint(l)).toEqual([['raster', 'raster-opacity', 0.6]])
    expect(r.legend(l)).toEqual({ title: 'NDVI', ramp: { field: 'NDVI', min: 0, max: 0.8, nota: undefined } })
    expect(r.bounds(l)).toEqual([-74.2, 4.5, -74, 4.7])
    expect(r.pickable).toBe(false)
  })

  it('arcgis-image: por el proxy del backend (CORS)', () => {
    const { source } = rendererDe('arcgis-image').buildSpecs(
      capa({ kind: 'arcgis-image', url: 'https://x.gov.co/arcgis/rest/services/Orto/ImageServer' }), CTX)
    expect(String((source as { tiles: string[] }).tiles[0])).toMatch(/^\/api\/v1\/proxy\/imagery/)
  })

  it('wms: GetMap por tesela con el bbox de MapLibre', () => {
    expect(wmsTileUrl('https://geo.x/wms', 'capa:uno')).toContain('LAYERS=capa%3Auno')
    expect(wmsTileUrl('https://geo.x/wms?map=a', 'x')).toContain('?map=a&SERVICE=WMS')
    const { source } = rendererDe('wms').buildSpecs(capa({ kind: 'wms', url: 'https://geo.x/wms', wmsLayers: 'x' }), CTX)
    expect(String((source as { tiles: string[] }).tiles[0])).toContain('BBOX={bbox-epsg-3857}')
  })
})

describe('firma de capa', () => {
  it('cambia con los datos o el estilo, no con visibilidad u opacidad', () => {
    const l = capa({ data: fc(1) })
    expect(firmaDeCapa({ ...l, visible: false, opacity: 0.2 })).toEqual(firmaDeCapa(l))
    expect(firmaDeCapa({ ...l, symbology: { symbology_type: 'heatmap' } })).not.toEqual(firmaDeCapa(l))
  })
})

describe('store único', () => {
  beforeEach(() => useMapStore.setState({ layers: [] }))

  it('un raster entra debajo de los vectores; luego se puede subir encima de uno', () => {
    const s = useMapStore.getState()
    const lotes = s.addLayer(fc(2), 'Lotes')
    const ndvi = s.addRasterLayer({ url: '/t/{z}/{x}/{y}.png', name: 'NDVI' })
    expect(useMapStore.getState().layers.map((l) => l.id)).toEqual([ndvi, lotes])
    useMapStore.getState().moveLayer(ndvi, 1)
    expect(useMapStore.getState().layers.map((l) => l.id)).toEqual([lotes, ndvi])
  })

  it('el tipo sale de lo que llega: MVT si hay teselas, XYZ si la URL es plantilla', () => {
    const s = useMapStore.getState()
    s.addLayer(EMPTY_FC, 'Grande', undefined, 'ds_1',
      { url: '/t/{z}/{x}/{y}.pbf', sourceLayer: 'dataset', geometryType: 'Point', bbox: null, featureCount: 9, fields: [] })
    s.addRasterLayer({ url: '/t/{z}/{x}/{y}.png', name: 'X' })
    s.addRasterLayer({ url: 'https://x/arcgis/rest/services/A/MapServer', name: 'A' })
    expect(useMapStore.getState().layers.map((l) => l.kind).sort()).toEqual(['arcgis-image', 'raster-xyz', 'vector-mvt'])
  })

  it('una tabla de la BD teselada es una capa MVT más, sin duplicarse', () => {
    const s = useMapStore.getState()
    s.addTableTiles('catastro', 'lotes', 'polygon')
    s.addTableTiles('catastro', 'lotes', 'polygon')
    const [l] = useMapStore.getState().layers
    expect(useMapStore.getState().layers).toHaveLength(1)
    expect(l).toMatchObject({ kind: 'vector-mvt', tiles: { sourceLayer: 'catastro.lotes', geometryType: 'Polygon' } })
  })

  it('re-estilar no cambia el sitio de la capa en el orden', () => {
    const s = useMapStore.getState()
    const a = s.addLayer(fc(1), 'A')
    s.addLayer(fc(1), 'B')
    useMapStore.getState().setLayerStyle(a, { symbology_type: 'heatmap', fill: { color: '#abcdef' } })
    const [primera] = useMapStore.getState().layers
    expect(primera.id).toBe(a)
    expect(primera.color).toBe('#abcdef')
  })
})
