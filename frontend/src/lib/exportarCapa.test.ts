import { describe, expect, it } from 'vitest'

import { alcancesDe, pedidoDe } from './exportarCapa'
import type { MapLayer } from '@/stores'

const capa = (extra: Partial<MapLayer> = {}) =>
  ({ id: 'l1', name: 'Lotes', kind: 'vector-mvt', datasetId: 'ds_0123456789abcdef', ...extra }) as MapLayer

const FILTRO = [['>', ['get', 'area'], 10]]

describe('exportar una capa: qué se pide al servidor', () => {
  it('una capa sin filtro ni selección solo se exporta entera', () => {
    expect(alcancesDe(capa())).toEqual(['toda'])
    expect(pedidoDe(capa(), 'toda')).toEqual({ filtro: null, ids: null })
  })

  it('con filtro ofrece lo filtrado y lo manda como filtro', () => {
    const c = capa({ filtro: FILTRO as never })
    expect(alcancesDe(c)).toEqual(['toda', 'filtro'])
    expect(pedidoDe(c, 'filtro')).toEqual({ filtro: FILTRO, ids: null })
    expect(pedidoDe(c, 'toda')).toEqual({ filtro: null, ids: null })       // entera ignora el filtro
  })

  it('la selección por ids viaja como ids y respeta el filtro de la capa', () => {
    const c = capa({ filtro: FILTRO as never, seleccion: { ids: [3, 7], count: 2, origin: 'user' } as never })
    expect(alcancesDe(c)).toEqual(['toda', 'filtro', 'seleccion'])
    expect(pedidoDe(c, 'seleccion')).toEqual({ filtro: FILTRO, ids: [3, 7] })
  })

  it('la selección por condición se suma al filtro', () => {
    const where = { field: 'tipo', op: '=', value: 'rural' }
    const c = capa({ seleccion: { where, count: 40, origin: 'query' } as never })
    expect(pedidoDe(c, 'seleccion')).toEqual({ filtro: [where], ids: null })
  })
})
