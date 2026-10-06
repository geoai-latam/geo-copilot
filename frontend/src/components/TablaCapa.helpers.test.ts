import { describe, expect, it } from 'vitest'

import type { MapLayer } from '@/stores/mapStore'
import { reducir } from '@/lib/operaciones'
import { filtroDeCapa, filtroResaltado, idsQueCumplen } from '@/lib/seleccion'
import { desdeWorkspace, estadisticaEnMemoria, estaSeleccionada, filasEnMemoria } from './TablaCapa.helpers'
import { textoCondicion, valorDe } from './FiltroCapa.helpers'

const feat = (i: number, estrato: number, uso: string) => ({
  type: 'Feature', properties: { lotcodigo: `L${i}`, estrato, uso },
  geometry: { type: 'Point', coordinates: [i, i * 2] },
})
const capa = (extra: Partial<MapLayer> = {}): MapLayer => ({
  id: 'l1', name: 'Lotes', kind: 'vector-geojson', visible: true, color: '#111', featureCount: 5, addedAt: new Date(0),
  data: { type: 'FeatureCollection', features: [feat(0, 1, 'res'), feat(1, 3, 'com'), feat(2, 3, 'res'), feat(3, 2, 'res'), feat(4, 3, 'ind')] } as never,
  ...extra,
})
const ESTRATO3 = [{ field: 'estrato', op: '=' as const, value: 3 }]

describe('tabla vinculada y filtros (FH.5)', () => {
  it('la tabla muestra lo que muestra la capa: su filtro, lo seleccionado y el orden', () => {
    expect(filasEnMemoria(capa()).map((f) => f.id)).toEqual([0, 1, 2, 3, 4])
    const filtrada = capa({ filtro: ESTRATO3 })
    expect(filasEnMemoria(filtrada).map((f) => f.properties.lotcodigo)).toEqual(['L1', 'L2', 'L4'])
    const conSel = capa({ filtro: ESTRATO3, seleccion: { ids: [2, 3], count: 2, origin: 'click' } })
    expect(filasEnMemoria(conSel, { soloSeleccion: true }).map((f) => f.id)).toEqual([2]) // el 3 no pasa el filtro
    expect(filasEnMemoria(capa(), { orden: 'uso', desc: true }).map((f) => f.properties.uso))
      .toEqual(['res', 'res', 'res', 'ind', 'com'])
    expect(filasEnMemoria(capa())[1].bbox).toEqual([1, 2, 1, 2]) // para encuadrar
  })

  it('una fila está seleccionada por su id o por la condición de la selección', () => {
    const f = { id: 1, properties: { estrato: 3 } }
    expect(estaSeleccionada(capa({ seleccion: { ids: [1], count: 1, origin: 'table' } }), f)).toBe(true)
    expect(estaSeleccionada(capa({ seleccion: { where: ESTRATO3[0], count: 3, origin: 'agent' } }), f)).toBe(true)
    expect(estaSeleccionada(capa(), f)).toBe(false)
  })

  it('estadística de un campo numérico y de uno de texto', () => {
    const e = estadisticaEnMemoria(filasEnMemoria(capa()), 'estrato')
    expect(e).toMatchObject({ n: 5, con_valor: 5, unicos: 3, numerico: true, min: 1, max: 3, media: 2.4 })
    expect(e.frecuentes[0]).toEqual({ valor: '3', n: 3 })
    const t = estadisticaEnMemoria(filasEnMemoria(capa()), 'uso')
    expect(t.numerico).toBe(false)
    expect(t.min).toBeUndefined()
  })

  it('una capa en teselas o sin datos se pagina contra el workspace', () => {
    expect(desdeWorkspace(capa())).toBe(false)
    expect(desdeWorkspace(capa({ kind: 'vector-mvt', datasetId: 'ds_x' }))).toBe(true)
    expect(desdeWorkspace(capa({ datasetId: 'ds_x', data: { type: 'FeatureCollection', features: [] } as never }))).toBe(true)
  })

  it('el valor del filtro como lo escribe el usuario', () => {
    expect(valorDe('3', '=')).toBe(3)
    expect(valorDe(' res ', '=')).toBe('res')
    expect(valorDe('res, 2, com', 'in')).toEqual(['res', 2, 'com'])
    expect(textoCondicion({ field: 'uso', op: 'in', value: ['res', 'com'] })).toBe('uso en res, com')
  })

  it('set_filter: aplicar + deshacer = identidad; cuenta lo que queda', () => {
    const antes = [capa()]
    const { layers, inversa } = reducir(antes, { op: 'set_filter', layer_id: 'l1', reason: null,
      args: { where: ESTRATO3 as never, count: null } })
    expect(layers[0].filtro).toEqual(ESTRATO3)
    expect(layers[0].filtroCount).toBe(3)
    expect(inversa!(layers)).toEqual(antes)
    const sin = reducir(layers, { op: 'set_filter', layer_id: 'l1', reason: null, args: { where: [] as never, count: null } })
    expect(sin.layers[0].filtro).toBeNull()
  })

  it('el filtro como expresión del mapa, y el resaltado respeta el filtro', () => {
    expect(filtroDeCapa(null)).toBeNull()
    expect(filtroDeCapa(ESTRATO3)).toEqual(['all', ['has', 'estrato'], ['==', ['to-number', ['get', 'estrato']], 3]])
    const r = filtroResaltado({ seleccion: { ids: [1], count: 1, origin: 'click' }, filtro: ESTRATO3 })
    expect((r as unknown[])[0]).toBe('all')
    // seleccionar por condición en una capa filtrada: solo lo visible
    expect(idsQueCumplen(capa({ filtro: ESTRATO3 }), { field: 'uso', op: '=', value: 'res' })).toEqual([2])
  })
})
