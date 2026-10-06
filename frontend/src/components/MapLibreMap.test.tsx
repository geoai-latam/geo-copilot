/**
 * MAP-SETUP-BASEMAPS — MapLibreMap monta un mapa real con raster basemap.
 *
 * maplibre-gl necesita WebGL (no existe en jsdom), así que se mockea: probamos
 * el CABLEADO (se construye el Map con un raster source y se publica el oráculo),
 * no el render GPU (eso va en el E2E de paridad con navegador).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'

const mapInstance = {
  addControl: vi.fn(),
  removeControl: vi.fn(),
  remove: vi.fn(),
  getSource: vi.fn(() => ({ setTiles: vi.fn() })),
  isStyleLoaded: () => true,
  once: vi.fn(),
  on: vi.fn(),
  getStyle: vi.fn(() => ({ layers: [] as unknown[] })),
  addSource: vi.fn(),
  addLayer: vi.fn(),
  getLayer: vi.fn(() => ({ id: 'basemap-layer' })),
  removeLayer: vi.fn(),
  removeSource: vi.fn(),
  setLayoutProperty: vi.fn(),
  getCanvas: vi.fn(() => ({ style: {} as Record<string, string> })),
  fitBounds: vi.fn(),
  flyTo: vi.fn(),
  getBounds: vi.fn(() => ({ toArray: () => [[0, 0], [1, 1]] })),
  getCenter: vi.fn(() => ({ lng: 0, lat: 0 })),
  getZoom: vi.fn(() => 4.4),
  project: vi.fn(() => ({ x: 0, y: 0 })),
  queryRenderedFeatures: vi.fn(() => [] as unknown[]),
}
// `function` y no flecha: desde vitest 3 un mock se invoca con `new` solo si es constructor.
const MapMock = vi.fn(function () { return mapInstance })

// maplibre-gl 6 es solo ESM, sin export por defecto: se importa como namespace (exports con nombre).
vi.mock('maplibre-gl', () => ({
  Map: MapMock,
  NavigationControl: vi.fn(),
  AttributionControl: vi.fn(),
  setWorkerUrl: vi.fn(),
}))

const { MapLibreMap } = await import('./MapLibreMap')
const { getMapTestState } = await import('@/lib/mapTestState')

beforeEach(() => {
  delete (window as unknown as Record<string, unknown>).__mapTestState
  MapMock.mockClear()
})

describe('MapLibreMap (MAP-SETUP-BASEMAPS)', () => {
  it('monta el mapa y publica el oráculo engine=maplibre', () => {
    const { getByTestId } = render(<MapLibreMap />)
    expect(getByTestId('maplibre-map')).toBeTruthy()
    expect(MapMock).toHaveBeenCalledTimes(1)
    expect(getMapTestState()?.engine).toBe('maplibre')
  })

  it('inicializa con un raster source de basemap con tiles', () => {
    render(<MapLibreMap />)
    const opts = (MapMock.mock.calls[0] as unknown[])[0] as {
      style: { sources: Record<string, { type: string; tiles: string[] }>; layers: Array<{ type: string }> }
    }
    expect(opts.style.sources.basemap.type).toBe('raster')
    expect(opts.style.sources.basemap.tiles.length).toBeGreaterThanOrEqual(1)
    expect(opts.style.layers[0].type).toBe('raster')
  })
})
