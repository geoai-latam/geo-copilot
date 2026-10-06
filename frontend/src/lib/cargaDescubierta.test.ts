import { describe, it, expect, beforeEach, vi } from 'vitest'

const load = vi.fn()
const layers = vi.fn()
vi.mock('@/services/api', () => ({
  discoveryApi: { load: (...a: unknown[]) => load(...a), layers: (...a: unknown[]) => layers(...a) },
}))

import { cargarDescubierto, mensajeDeCarga } from './cargaDescubierta'
import { useMapStore } from '@/stores/mapStore'
import type { HubItem } from '@/types/discovery'

const RAIZ = 'https://s/arcgis/rest/services/Malla/FeatureServer'
const item = (extra: Partial<HubItem> = {}): HubItem => ({
  id: 'a', source: 'arcgis_online', org: 'x', title: 'Malla vial', description: '',
  service_type: 'FeatureServer', service_url: RAIZ, ...extra,
})
const TILES = {
  url: '/api/v1/tiles/ws/s-1/ds_aaaaaaaaaaaaaaaa/{z}/{x}/{y}.pbf', source_layer: 'dataset',
  geometry_type: 'LineString', bbox: [-74.2, 4.4, -73.9, 4.8] as [number, number, number, number],
  feature_count: 136956, fields: ['mvinombre'],
}

describe('cargar un servicio descubierto (panel y tarjetas del chat)', () => {
  beforeEach(() => {
    load.mockReset()
    layers.mockReset()
    useMapStore.setState({ layers: [] } as never)
  })

  it('una capa grande llega COMPLETA por teselas, no una muestra de 2000', async () => {
    load.mockResolvedValue({
      type: 'geojson', name: 'Malla vial', service_type: 'FeatureServer', service_url: `${RAIZ}/0`,
      geojson: null, tiles: TILES, dataset_id: 'ds_aaaaaaaaaaaaaaaa', total_available: 136956, feature_count: 136956,
    })
    const r = await cargarDescubierto(item({ single_layer: true }), { sessionId: 's-1' })
    expect(load.mock.calls[0][1]).toEqual({ sessionId: 's-1' }) // sin `limit`: completa
    const [capa] = useMapStore.getState().layers
    expect(capa.kind).toBe('vector-mvt')
    expect(capa.tiles?.featureCount).toBe(136956)
    expect(capa.datasetId).toBe('ds_aaaaaaaaaaaaaaaa')
    expect(r).toEqual({ estado: 'cargada', layerId: capa.id,
      mensaje: `Cargué «Malla vial» al mapa (${(136956).toLocaleString('es-CO')} elementos).` })
  })

  it('con varias capas devuelve cuáles para elegir y no carga nada', async () => {
    const capas = [{ id: 0, nombre: 'Puntos', tipo_geometria: 'Point' }, { id: 3, nombre: 'Vía', tipo_geometria: 'Polyline' }]
    layers.mockResolvedValue(capas)
    expect(await cargarDescubierto(item(), { sessionId: 's-1' })).toEqual({ estado: 'elegir_capa', capas })
    expect(load).not.toHaveBeenCalled()
  })

  it('la capa elegida se pide por su id y su nombre', async () => {
    load.mockResolvedValue({ type: 'geojson', name: 'Cota · Vía', service_type: 'FeatureServer', service_url: RAIZ,
      geojson: { type: 'FeatureCollection', features: [] } })
    await cargarDescubierto(item(), { sessionId: 's-1', capa: { id: 3, nombre: 'Vía', tipo_geometria: 'Polyline' } })
    expect(layers).not.toHaveBeenCalled()
    expect(load.mock.calls[0][1]).toEqual({ sessionId: 's-1', layerId: 3, layerName: 'Vía' })
  })

  it('si no vino todo, se dice que es una muestra', () => {
    expect(mensajeDeCarga({ type: 'geojson', name: 'X', service_type: 'FeatureServer', service_url: RAIZ,
      geojson: { type: 'FeatureCollection', features: [] }, feature_count: 5000, total_available: 9000 }))
      .toBe(`Cargué «X» al mapa (${(5000).toLocaleString('es-CO')} elementos). El servicio tiene ${(9000).toLocaleString('es-CO')}: es una muestra, no el total.`)
  })
})
