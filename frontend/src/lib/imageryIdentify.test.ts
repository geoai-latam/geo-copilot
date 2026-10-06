/**
 * FRT-03: el click sobre una capa raster (imagery) dispara /identify por el
 * proxy y puebla el popup `source: 'imagery'`. Antes: el módulo arcgisIdentify
 * era código muerto (solo su propio test) y el click sobre imagery no mostraba
 * nada (setSelectedFeature(null)).
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import type * as maplibregl from 'maplibre-gl'
import { EMPTY_FC, useMapStore } from '@/stores/mapStore'
import { identifyImageryAt, IDENTIFY_PROXY_BASE } from './imageryIdentify'

const SERVICE = 'https://mapas2.igac.gov.co/server/rest/services/Cat/IGAC/MapServer'

const mockMap = {
  getBounds: () => ({
    getWest: () => -75, getSouth: () => 4, getEast: () => -73, getNorth: () => 6,
  }),
  getCanvas: () => ({ width: 800, height: 600 }),
} as unknown as maplibregl.Map

function clickAt(lng: number, lat: number) {
  return {
    lngLat: { lng, lat },
    originalEvent: { clientX: 10, clientY: 20 },
  } as unknown as maplibregl.MapMouseEvent
}

function seedImageryLayer() {
  useMapStore.setState({
    layers: [
      {
        id: 'img-1',
        name: 'Predios IGAC',
        kind: 'arcgis-image',
        url: SERVICE,
        visible: true,
        extent: { xmin: -75, ymin: 4, xmax: -73, ymax: 6 },
        addedAt: new Date(0),
        data: EMPTY_FC,
        color: '#8a4c8a',
        featureCount: 0,
      },
    ],
    selectedFeature: null,
  })
}

describe('identifyImageryAt (FRT-03)', () => {
  beforeEach(() => {
    seedImageryLayer()
  })
  afterEach(() => {
    vi.restoreAllMocks()
    useMapStore.setState({ layers: [], selectedFeature: null })
  })

  it('click dentro del extent → identify por el proxy y popup imagery con atributos', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [{ layerName: 'Predios', attributes: { ESTRATO: 3, USO: 'residencial' } }],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await identifyImageryAt(mockMap, clickAt(-74, 5))

    // Pidió el identify por el proxy same-origin, con el servicio correcto.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const url = fetchMock.mock.calls[0][0] as string
    expect(url.startsWith(IDENTIFY_PROXY_BASE)).toBe(true)
    expect(url).toContain(`service=${encodeURIComponent(SERVICE)}`)

    // Pobló el popup imagery con los atributos parseados.
    const sel = useMapStore.getState().selectedFeature as {
      source: string
      layerName: string
      properties: Record<string, unknown>
    }
    expect(sel.source).toBe('imagery')
    expect(sel.layerName).toBe('Predios')
    expect(sel.properties.ESTRATO).toBe(3)
    expect(sel.properties.USO).toBe('residencial')
  })

  it('valor de píxel (ImageServer) llega al popup como "Valor"', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ value: 0.42, name: 'NDVI' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await identifyImageryAt(mockMap, clickAt(-74, 5))

    const sel = useMapStore.getState().selectedFeature as {
      properties: Record<string, unknown>
    }
    expect(sel.properties.Valor).toBe(0.42)
  })

  it('click FUERA de todo extent → no llama al identify y limpia la selección', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await identifyImageryAt(mockMap, clickAt(10, 10)) // fuera del extent

    expect(fetchMock).not.toHaveBeenCalled()
    expect(useMapStore.getState().selectedFeature).toBeNull()
  })

  it('identify sin resultados → limpia la selección (no popup vacío)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [] }),
    }))
    await identifyImageryAt(mockMap, clickAt(-74, 5))
    expect(useMapStore.getState().selectedFeature).toBeNull()
  })

  it('fallo de red → limpia la selección sin romper', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')))
    await identifyImageryAt(mockMap, clickAt(-74, 5))
    expect(useMapStore.getState().selectedFeature).toBeNull()
  })

  it('un raster XYZ (MCP) no se identifica por ArcGIS', async () => {
    useMapStore.setState({
      layers: [{ ...useMapStore.getState().layers[0], kind: 'raster-xyz', url: '/api/v1/proxy/mcp/imagery/tiles/S/{z}/{x}/{y}.png' }],
    })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await identifyImageryAt(mockMap, clickAt(-74, 5))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('capa no visible → no se identifica', async () => {
    useMapStore.setState({
      layers: [{ ...useMapStore.getState().layers[0], visible: false }],
    })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await identifyImageryAt(mockMap, clickAt(-74, 5))
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
