import { describe, it, expect } from 'vitest'

import type { Accion } from '@/services/api'
import type { MapLayer } from '@/stores/mapStore'
import { geometriaDeCapa, paraCapa, valorObjetivo } from './MenuContextual.helpers'

const accion = (accepts: string[]): Accion => ({
  server: 'x', tool: 't', herramienta: 'x__t', titulo: 'T', description: '', objetivo: 'aoi', costo: 'low',
  input_schema: { properties: { aoi: { type: 'string' } } }, geo: { inputs: { aoi: { accepts } } }, riesgo: 'read', estado: 'habilitada',
})
const feats = [0, 1, 2].map((i) => ({ type: 'Feature', id: i, properties: { n: i },
  geometry: { type: 'Polygon', coordinates: [[[i, 0], [i + 1, 0], [i + 1, 1], [i, 0]]] } }))
const enMemoria = { id: 'layer-1', name: 'Lotes', kind: 'vector-geojson', visible: true, color: '#000',
  data: { type: 'FeatureCollection', features: feats }, seleccion: { ids: [1], count: 1, origin: 'click' } } as unknown as MapLayer
const delWorkspace = { ...enMemoria, datasetId: 'ds_lotes' } as MapLayer

describe('FH.8 — qué recibe cada acción del menú', () => {
  it('capa del workspace: la referencia (`seleccion` o su ds_), también para un servicio', () => {
    expect(valorObjetivo(accion(['dataset']), delWorkspace, 'seleccion')).toEqual({ ok: true, valor: 'seleccion' })
    expect(valorObjetivo(accion(['geometry', 'layer_ref']), delWorkspace, 'capa')).toEqual({ ok: true, valor: 'ds_lotes' })
  })

  it('capa solo en el navegador: un servicio recibe el GeoJSON de lo seleccionado; una ws_* no aplica', () => {
    const v = valorObjetivo(accion(['geometry', 'layer_ref']), enMemoria, 'seleccion')
    expect(v.ok && (v.valor as { features: unknown[] }).features).toEqual([feats[1]])
    expect(valorObjetivo(accion(['dataset']), enMemoria, 'capa')).toEqual({ ok: false, motivo: 'solo para capas guardadas en el workspace' })
    expect(paraCapa([accion(['dataset'])], enMemoria, 'capa')[0].disponible).toBe(false)
  })

  it('el tipo de geometría de la capa (teselas o su primer elemento)', () => {
    expect(geometriaDeCapa(enMemoria)).toBe('Polygon')
    expect(geometriaDeCapa({ ...enMemoria, tiles: { geometryType: 'MultiLineString' } } as unknown as MapLayer)).toBe('MultiLineString')
  })
})
