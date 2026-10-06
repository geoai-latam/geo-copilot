import { describe, expect, it } from 'vitest'

import { leyendaDe } from '@/lib/leyenda'
import { conFijados } from '@/lib/operaciones'
import type { MapLayer } from '@/stores/mapStore'
import type { LayerSymbology } from '@/types'
import { cambiar, completo, disenoDe } from './EditorEstilo.helpers'

const capa = (extra: Partial<MapLayer> = {}): MapLayer => ({
  id: 'l1', name: 'Lotes', kind: 'vector-geojson', visible: true, color: '#3b82f6', featureCount: 3, addedAt: new Date(0),
  data: { type: 'FeatureCollection', features: [] } as never, ...extra,
})
const GRAD: LayerSymbology = {
  symbology_type: 'graduated_colors', classification_field: 'area', classification_method: 'natural_breaks',
  num_classes: 3, color_scheme: 'Reds', pinned: ['color_scheme'],
  class_breaks: [{ label: '0-10', color: '#fee' }, { label: '10-20', color: '#f88' }, { label: '20-30', color: '#c00' }] as never,
}

describe('editor de estilo y leyenda viva (FH.6)', () => {
  it('lo que el usuario toca queda fijado; cambiar a graduado completa método y clases', () => {
    const d = disenoDe(capa({ symbology: GRAD }))
    expect(d).toMatchObject({ symbology_type: 'graduated_colors', classification_field: 'area', num_classes: 3, color_scheme: 'Reds' })
    const r = cambiar(d, ['color_scheme'], 'num_classes', 7)
    expect(r.pinned).toEqual(['color_scheme', 'num_classes'])
    const g = cambiar(disenoDe(capa()), [], 'symbology_type', 'graduated_colors')
    expect(g.diseno).toMatchObject({ classification_method: 'natural_breaks', num_classes: 5 })
    expect(completo(g.diseno)).toBe(false) // falta el campo
    expect(completo({ ...g.diseno, classification_field: 'area' })).toBe(true)
  })

  it('un restyle del agente conserva los fijados que no cambió y suelta los que sí', () => {
    const siete = { ...GRAD, num_classes: 7, pinned: undefined }
    expect(conFijados(siete, GRAD).pinned).toEqual(['color_scheme']) // la rampa sigue siendo Reds
    const azul = { ...GRAD, color_scheme: 'Blues', pinned: undefined }
    expect(conFijados(azul, GRAD).pinned).toBeUndefined() // se la cambiaron a pedido: ya no está fijada
    const delEditor = { ...GRAD, pinned: ['num_classes'] }
    expect(conFijados(delEditor, GRAD).pinned).toEqual(['num_classes']) // el editor manda
  })

  it('leyenda viva: clases, color único, calor, cluster y raster (sin inventar clases)', () => {
    expect(leyendaDe(capa({ symbology: GRAD }))).toMatchObject({ filas: [{ label: '0-10' }, {}, {}], nota: 'por area · rampa Reds' })
    expect(leyendaDe(capa())).toMatchObject({ filas: [{ label: 'Todos los elementos', color: '#3b82f6' }] })
    expect(leyendaDe(capa({ symbology: { symbology_type: 'heatmap' } }))?.rampa?.colores.length).toBeGreaterThan(1)
    expect(leyendaDe(capa({ symbology: { symbology_type: 'cluster' } }))?.filas?.[0].label).toMatch(/agrupados/)
    const ndvi = capa({ kind: 'raster-xyz', legend: { field: 'NDVI', min: -0.1, max: 0.8 } as never })
    expect(leyendaDe(ndvi)).toMatchObject({ rampa: { min: -0.1, max: 0.8, campo: 'NDVI' } })
    expect(leyendaDe(capa({ kind: 'raster-xyz' }))).toBeNull() // una imagen en color no tiene «Todos los elementos»
    expect(leyendaDe(capa({ visible: false }))).toBeNull()
  })

  it('con la capa filtrada, la leyenda cuenta solo lo visible', () => {
    const feats = [5, 15, 25, 25, 8].map((v, i) => ({ type: 'Feature', properties: { area: v, k: i },
      geometry: { type: 'Point', coordinates: [i, 0] } }))
    const sim = { ...GRAD, class_breaks: [
      { label: '0-10', color: '#fee', min_value: 0, max_value: 10, count: 2 },
      { label: '10-20', color: '#f88', min_value: 10, max_value: 20, count: 1 },
      { label: '20-30', color: '#c00', min_value: 20, max_value: 30, count: 2 }] } as never
    const l = capa({ data: { type: 'FeatureCollection', features: feats } as never, symbology: sim,
                     filtro: [{ field: 'area', op: '>', value: 10 }] })
    expect(leyendaDe(l)?.filas?.map((f) => f.count)).toEqual([0, 1, 2])
    expect(leyendaDe(capa({ ...l, filtro: null }))?.filas?.map((f) => f.count)).toEqual([2, 1, 2])
  })
})
