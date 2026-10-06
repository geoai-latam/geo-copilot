import { describe, expect, it } from 'vitest'

import type { MapLayer } from '@/stores/mapStore'
import { candidatas, filtrar, insertar, menciónEnCurso, vigentes } from './menciones'

const capa = (id: string, name: string, extra: Partial<MapLayer> = {}): MapLayer => ({
  id, name, kind: 'vector-geojson', visible: true, color: '#111', featureCount: 1, addedAt: new Date(0), opacity: 1,
  data: { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { area_m2: 1, lotcodigo: 'x' },
    geometry: { type: 'Point', coordinates: [0, 0] } }] } as never,
  ...extra,
})

const MAPA = [
  capa('l1', 'Lotes', { seleccion: { ids: [1, 2], count: 2, origin: 'box' } }),
  capa('l2', 'Vías'),
  capa('l3', 'Área 1', { origen: { capability: 'user.sketch', arguments: {} } }),
]

describe('menciones @ (FH.4)', () => {
  it('ofrece la selección, las capas (arriba primero, dibujos marcados) y los campos', () => {
    const c = candidatas(MAPA)
    expect(c[0]).toMatchObject({ tipo: 'seleccion', layer_id: 'l1', texto: '@selección', detalle: '2 seleccionados de Lotes' })
    expect(c.filter((x) => x.tipo === 'capa').map((x) => [x.texto, x.detalle]))
      .toEqual([['@Área 1', 'dibujo'], ['@Vías', 'capa'], ['@Lotes', 'capa']])
    expect(c.find((x) => x.texto === '@Lotes.area_m2')).toMatchObject({ tipo: 'campo', campo: 'area_m2', layer_id: 'l1' })
  })

  it('filtra sin tildes ni mayúsculas; los campos solo al pedirlos', () => {
    const c = candidatas(MAPA)
    expect(filtrar(c, 'via').map((x) => x.texto)).toEqual(['@Vías'])
    expect(filtrar(c, 'area').map((x) => x.texto)).toEqual(['@Área 1', '@Área 1.area_m2', '@Vías.area_m2', '@Lotes.area_m2'])
    expect(filtrar(c, 'lotes.').map((x) => x.texto)).toEqual(['@Lotes.area_m2', '@Lotes.lotcodigo'])
    expect(filtrar(c, '').map((x) => x.tipo)).not.toContain('campo')
  })

  it('detecta el @ en curso (a inicio de palabra) e inserta la elegida', () => {
    expect(menciónEnCurso('buffer a @Ví', 12)).toEqual({ inicio: 9, parcial: 'Ví' })
    expect(menciónEnCurso('correo a@b', 10)).toBeNull()
    expect(menciónEnCurso('sin arroba', 10)).toBeNull()
    const r = insertar('buffer a @Ví de 100 m', 9, 12, { tipo: 'capa', layer_id: 'l2', texto: '@Vías' })
    expect(r).toEqual({ texto: 'buffer a @Vías  de 100 m', cursor: 15 })
  })

  it('solo cuentan las menciones que siguen en el texto (y sin repetir)', () => {
    const vias = { tipo: 'capa' as const, layer_id: 'l2', texto: '@Vías' }
    const lotes = { tipo: 'capa' as const, layer_id: 'l1', texto: '@Lotes' }
    expect(vigentes('@Vías buffer 100 m', [vias, lotes, vias])).toEqual([vias])
  })
})
