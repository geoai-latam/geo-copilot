import { describe, expect, it } from 'vitest'

import type { MapLayer } from '@/stores/mapStore'

import { cumple, filtroSeleccion, idsQueCumplen, NADA, puntoEnPoligono, tocaLazo } from './seleccion'

describe('selección (FH.2)', () => {
  it('el filtro de MapLibre sale de los ids o de la condición', () => {
    expect(filtroSeleccion(null)).toEqual(NADA)
    expect(filtroSeleccion({ ids: [3, 5], count: 2, origin: 'click' })).toEqual(['in', ['id'], ['literal', [3, 5]]])
    expect(filtroSeleccion({ where: { field: 'area', op: '>', value: 5 }, count: 1, origin: 'agent' }))
      .toEqual(['all', ['has', 'area'], ['>', ['to-number', ['get', 'area']], 5]])
    expect(filtroSeleccion({ where: { field: 'uso', op: 'in', value: ['res', 'com'] }, count: 1, origin: 'agent' }))
      .toEqual(['in', ['to-string', ['get', 'uso']], ['literal', ['res', 'com']]])
  })

  it('la misma regla que el backend para el predicado', () => {
    expect(cumple({ field: 'a', op: '>=', value: 10 }, { a: '10' })).toBe(true)
    expect(cumple({ field: 'c', op: 'contains', value: 'AB' }, { c: 'xaby' })).toBe(true)
    expect(cumple({ field: 'x', op: '=', value: 1 }, {})).toBe(false)
  })

  it('el lazo toca un polígono por sus vértices o por dentro', () => {
    const cuadrado = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    expect(puntoEnPoligono([5, 5], cuadrado)).toBe(true)
    expect(puntoEnPoligono([15, 5], cuadrado)).toBe(false)
    const lote = { type: 'Polygon', coordinates: [[[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]] }
    expect(tocaLazo(lote, cuadrado)).toBe(true)
    // un lazo pequeño DENTRO de un lote grande también lo selecciona
    const grande = { type: 'Polygon', coordinates: [[[-5, -5], [50, -5], [50, 50], [-5, 50], [-5, -5]]] }
    expect(tocaLazo(grande, cuadrado)).toBe(true)
    expect(tocaLazo({ type: 'Point', coordinates: [20, 20] }, cuadrado)).toBe(false)
  })

  it('la identidad es el id de la feature si lo trae (fid del workspace); si no, su posición', () => {
    const feat = (valor: number, id?: number) => ({
      type: 'Feature', ...(id === undefined ? {} : { id }), properties: { valor },
      geometry: { type: 'Point', coordinates: [0, 0] },
    })
    const capa = (features: unknown[]) => ({ data: { type: 'FeatureCollection', features } }) as unknown as MapLayer
    const w = { field: 'valor', op: '>', value: 5 } as const
    expect(idsQueCumplen(capa([feat(1), feat(9), feat(7)]), w)).toEqual([1, 2])
    // dataset hecho por SQL: fid desde 1 y en otro orden
    expect(idsQueCumplen(capa([feat(9, 3), feat(1, 1), feat(7, 2)]), w)).toEqual([3, 2])
  })
})
