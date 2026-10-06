import { describe, it, expect } from 'vitest'

import type { MapLayer } from '@/stores/mapStore'
import { elementos, extensionDe, resolverCapa, trocear } from './referencias'
import { filtroResaltado } from './seleccion'

const lotes = {
  id: 'layer-1', name: 'Lotes Manzana 008', kind: 'vector-geojson', datasetId: 'ds_lotes', visible: true, color: '#000',
  data: { type: 'FeatureCollection', features: [
    { type: 'Feature', id: 10, properties: { lotcodigo: 'A' }, geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } },
    { type: 'Feature', id: 11, properties: { lotcodigo: 'B' }, geometry: { type: 'Polygon', coordinates: [[[5, 5], [7, 5], [7, 8], [5, 5]]] } },
  ] },
} as unknown as MapLayer
const teselas = { id: 'layer-2', name: 'Predios', kind: 'vector-mvt', datasetId: 'ds_predios', visible: true, color: '#000',
                  data: { type: 'FeatureCollection', features: [] } } as unknown as MapLayer

describe('FH.7 — enlaces al mapa en la respuesta', () => {
  it('trocea el texto: capa, elemento por valor y por id; sin etiqueta usa el valor', () => {
    const t = trocear('El mayor es [[layer:activa?lotcodigo=B|el lote B]] en [[layer:ds_lotes]]; ver [[layer:layer-1#10]].')
    expect(t.map((x) => x.texto)).toEqual(['El mayor es ', 'el lote B', ' en ', 'ds_lotes', '; ver ', '10', '.'])
    expect(t[1].ref).toEqual({ capa: 'activa', campo: 'lotcodigo', valor: 'B' })
    expect(t[3].ref).toEqual({ capa: 'ds_lotes' })
    expect(t[5].ref).toEqual({ capa: 'layer-1', id: '10' })
  })

  it('un texto sin enlaces queda igual (y un corchete suelto no es enlace)', () => {
    expect(trocear('3 lotes [de 27]')).toEqual([{ texto: '3 lotes [de 27]' }])
  })

  it('resuelve la capa: `activa` = la que dejó ese turno; ds_ por dataset; id; nombre', () => {
    const capas = [lotes, teselas]
    expect(resolverCapa({ capa: 'activa' }, capas, ['layer-1'])?.id).toBe('layer-1')
    expect(resolverCapa({ capa: 'activa' }, capas, ['layer-borrada'])?.id).toBe('layer-2') // ya no está: la de arriba
    expect(resolverCapa({ capa: 'ds_predios' }, capas)?.id).toBe('layer-2')
    expect(resolverCapa({ capa: 'layer-1' }, capas)?.id).toBe('layer-1')
    // V5 EH.7: un id viejo (de un mensaje anterior) cuyo texto es el nombre de una capa: esa capa
    const nombre = capas[1].name
    expect(resolverCapa({ capa: 'layer-viejo' }, capas, [], nombre)?.id).toBe(capas[1].id)
    expect(resolverCapa({ capa: 'layer-viejo' }, capas, [], 'otra cosa')).toBeUndefined()
    expect(resolverCapa({ capa: 'layer-viejo', id: '3' }, capas, [], nombre)).toBeUndefined()
    // V5 EH.10: dos capas homónimas → la de este turno; sin turno, la más reciente
    const vieja = { ...capas[1], id: 'ndvi-vieja' }
    const nueva = { ...capas[1], id: 'ndvi-nueva' }
    expect(resolverCapa({ capa: nombre }, [vieja, nueva, capas[0]], ['ndvi-nueva'])?.id).toBe('ndvi-nueva')
    expect(resolverCapa({ capa: nombre }, [nueva, vieja], ['ndvi-nueva'])?.id).toBe('ndvi-nueva')
    expect(resolverCapa({ capa: nombre }, [vieja, nueva])?.id).toBe('ndvi-nueva')
    expect(resolverCapa({ capa: 'lotes manzana 008' }, capas)?.id).toBe('layer-1')
    expect(resolverCapa({ capa: 'ds_otro' }, capas)).toBeUndefined()
  })

  it('los elementos: por valor (en memoria → ids), por id, en teselas por condición; lo que no existe es «vacio»', () => {
    expect(elementos({ capa: 'x', campo: 'lotcodigo', valor: 'B' }, lotes)).toEqual({ ids: [11], count: 1, origin: 'link' })
    expect(elementos({ capa: 'x', id: '10' }, lotes)).toEqual({ ids: [10], count: 1, origin: 'link' })
    expect(elementos({ capa: 'x', campo: 'lotcodigo', valor: 'Z' }, lotes)).toBe('vacio')
    expect(elementos({ capa: 'x', id: '99' }, lotes)).toBe('vacio')
    expect(elementos({ capa: 'x' }, lotes)).toBeNull()
    expect(elementos({ capa: 'x', campo: 'lotcodigo', valor: 'B' }, teselas))
      .toEqual({ where: { field: 'lotcodigo', op: '=', value: 'B' }, count: 0, origin: 'link' })
  })

  it('un elemento filtrado fuera de la capa no se enlaza (la capa filtrada ES su subconjunto)', () => {
    const filtrada = { ...lotes, filtro: [{ field: 'lotcodigo', op: '=', value: 'A' }] } as MapLayer
    expect(elementos({ capa: 'x', campo: 'lotcodigo', valor: 'B' }, filtrada)).toBe('vacio')
  })

  it('encuadra la extensión de esos elementos', () => {
    expect(extensionDe(lotes, [11])).toEqual([5, 5, 7, 8])
    expect(extensionDe(lotes, [42])).toBeNull()
  })

  it('el resaltado del enlace se suma al de la selección, sin reemplazarla', () => {
    const f = filtroResaltado({ seleccion: { ids: [10], count: 1, origin: 'click' }, resaltado: { ids: [11], count: 1, origin: 'link' } })
    expect(f).toEqual(['any', ['in', ['id'], ['literal', [10]]], ['in', ['id'], ['literal', [11]]]])
    expect(filtroResaltado({ seleccion: null })).toEqual(['boolean', false])
  })
})
