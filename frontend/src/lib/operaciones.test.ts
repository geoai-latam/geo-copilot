import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useMapStore, type MapLayer } from '@/stores/mapStore'
import { accionesDesdeUltimoTurno, reducir, useOperaciones, type Operacion } from './operaciones'

const capa = (id: string, extra: Partial<MapLayer> = {}): MapLayer => ({
  id, name: id.toUpperCase(), kind: 'vector-geojson', data: { type: 'FeatureCollection', features: [] },
  visible: true, color: '#111', featureCount: 3, addedAt: new Date(0), opacity: 1, ...extra,
})
const ESTILO = {
  layer_title: null, symbology_type: 'graduated_colors', fill: null, stroke: null, marker: null,
  classification_field: 'area_m2', classification_method: 'quantile',
  class_breaks: [{ label: '0-100', color: '#fee', min_value: 0, max_value: 100, count: 2 }],
  color_scheme: null, num_classes: 1, symbol_size_min: null, symbol_size_max: null,
  heatmap_radius: null, heatmap_intensity_field: null,
} as never

const MAPA = [capa('raster', { kind: 'raster-xyz' }), capa('lotes', { labelField: null }), capa('vias')]

const ORDENES: Operacion[] = [
  { op: 'set_visibility', layer_id: 'lotes', args: { visible: false }, reason: null },
  { op: 'set_opacity', layer_id: 'raster', args: { opacity: 0.3 }, reason: null },
  { op: 'set_label', layer_id: 'lotes', args: { field: 'lotcodigo' }, reason: null },
  { op: 'set_style', layer_id: 'lotes', args: { style: ESTILO }, reason: null },
  { op: 'reorder', layer_id: 'raster', args: { to: 'top', relative_to: null }, reason: null },
  { op: 'reorder', layer_id: 'vias', args: { to: 'below', relative_to: 'lotes' }, reason: null },
  { op: 'remove_layer', layer_id: 'lotes', args: {}, reason: null },
]

describe('reducer del mapa compartido (FH.1)', () => {
  it.each(ORDENES.map((o) => [`${o.op} ${JSON.stringify((o as { args: unknown }).args)}`, o]))(
    'aplicar + deshacer = identidad: %s', (_n, orden) => {
      const { layers, inversa } = reducir(MAPA, orden as Operacion)
      expect(layers).not.toEqual(MAPA) // la orden cambió algo
      expect(inversa!(layers)).toEqual(MAPA)
    },
  )

  it('reorder above/below/top/bottom deja cada capa donde se pide (0 = abajo)', () => {
    const ids = (ls: MapLayer[]) => ls.map((l) => l.id)
    expect(ids(reducir(MAPA, ORDENES[4]).layers)).toEqual(['lotes', 'vias', 'raster'])
    expect(ids(reducir(MAPA, ORDENES[5]).layers)).toEqual(['raster', 'vias', 'lotes'])
    expect(ids(reducir(MAPA, { op: 'reorder', layer_id: 'vias', args: { to: 'bottom', relative_to: null }, reason: null }).layers))
      .toEqual(['vias', 'raster', 'lotes'])
  })

  it('una orden sobre una capa que no existe no cambia nada ni se puede deshacer', () => {
    const r = reducir(MAPA, { op: 'set_opacity', layer_id: 'no-existe', args: { opacity: 0.5 }, reason: null })
    expect(r.layers).toBe(MAPA)
    expect(r.inversa).toBeNull()
  })
})

describe('registro con deshacer/rehacer', () => {
  beforeEach(() => {
    useMapStore.setState({ layers: MAPA })
    useOperaciones.getState().limpiar()
  })

  it('deshacer la simbología del agente la quita y el agente lo ve como DESHECHA', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2026-09-25T10:00:00Z'))
      const ops = useOperaciones.getState()
      ops.ejecutar({ op: 'set_style', layer_id: 'lotes', args: { style: ESTILO }, reason: 'por área' }, 'agent')
      expect(useMapStore.getState().layers[1].symbology).toBeTruthy()
      // el turno siguiente se envía; después el usuario deshace con Ctrl+Z
      vi.setSystemTime(new Date('2026-09-25T10:01:00Z'))
      useOperaciones.getState().marcarTurno()
      vi.setSystemTime(new Date('2026-09-25T10:02:00Z'))
      useOperaciones.getState().deshacer()
      expect(useMapStore.getState().layers[1].symbology).toBeUndefined()
      // la simbología era de antes del turno, pero su deshacer es nuevo: el agente lo ve
      const [accion] = accionesDesdeUltimoTurno()
      expect(accion).toMatchObject({ op: 'set_style', layer_name: 'LOTES', author: 'agent', undone: true })
    } finally {
      vi.useRealTimers()
    }
  })

  it('un re-estilo del agente (replace_layer) dice qué estilo puso y cuál había (EH.6)', () => {
    const cb = (n: number) => Array.from({ length: n }, (_, i) => ({ label: `${i}`, color: '#000' }))
    const siete = { symbology_type: 'graduated_colors', classification_field: 'lotupredia', class_breaks: cb(7), color_scheme: 'Purples' }
    const seis = { ...siete, class_breaks: cb(6) }
    const previa = capa('lotes', { symbology: siete as never })
    useMapStore.setState({ layers: [MAPA[0], capa('lotes', { symbology: seis as never }), MAPA[2]] })
    useOperaciones.getState().ejecutar({ op: 'replace_layer', layer_id: 'lotes', previa }, 'agent')
    useOperaciones.getState().deshacer()
    const [accion] = accionesDesdeUltimoTurno()
    expect(accion).toMatchObject({
      op: 'set_style', undone: true,
      args: { estilo: 'graduated_colors por lotupredia, 6 clases, Purples', antes: 'graduated_colors por lotupredia, 7 clases, Purples' },
    })
    expect(useMapStore.getState().layers[1].symbology?.class_breaks).toHaveLength(7)
  })

  it('deshacer una acción no pisa lo que pasó después en otra capa', () => {
    const ops = useOperaciones.getState()
    ops.ejecutar({ op: 'set_opacity', layer_id: 'raster', args: { opacity: 0.2 }, reason: null }, 'user')
    ops.ejecutar({ op: 'set_visibility', layer_id: 'vias', args: { visible: false }, reason: null }, 'agent')
    // deshacer la última (vías) no toca la opacidad del raster
    useOperaciones.getState().deshacer()
    const [raster, , vias] = useMapStore.getState().layers
    expect(raster.opacity).toBe(0.2)
    expect(vias.visible).toBe(true)
  })

  it('arrastrar el slider es UNA entrada; deshacerla vuelve al valor de antes de arrastrar', () => {
    const ops = useOperaciones.getState()
    for (const o of [0.9, 0.7, 0.5, 0.35]) {
      ops.ejecutar({ op: 'set_opacity', layer_id: 'raster', args: { opacity: o }, reason: null }, 'user')
    }
    expect(useOperaciones.getState().registro).toHaveLength(1)
    useOperaciones.getState().deshacer()
    expect(useMapStore.getState().layers[0].opacity).toBe(1)
  })

  it('rehacer vuelve a aplicar lo deshecho; una acción nueva vacía la pila de rehacer', () => {
    const ops = useOperaciones.getState()
    ops.ejecutar({ op: 'set_visibility', layer_id: 'lotes', args: { visible: false }, reason: null }, 'user')
    useOperaciones.getState().deshacer()
    expect(useMapStore.getState().layers[1].visible).toBe(true)
    useOperaciones.getState().rehacerUltima()
    expect(useMapStore.getState().layers[1].visible).toBe(false)
    useOperaciones.getState().deshacer()
    ops.ejecutar({ op: 'set_opacity', layer_id: 'vias', args: { opacity: 0.5 }, reason: null }, 'user')
    expect(useOperaciones.getState().rehacer).toEqual([])
  })

  it('deshacer tras rehacer vuelve a deshacer (V5 FH.1)', () => {
    const ops = useOperaciones.getState()
    ops.ejecutar({ op: 'set_style', layer_id: 'lotes', args: { style: ESTILO }, reason: null }, 'agent')
    useOperaciones.getState().deshacer()
    useOperaciones.getState().rehacerUltima()
    expect(useMapStore.getState().layers[1].symbology).toBeTruthy()
    useOperaciones.getState().deshacer()
    expect(useMapStore.getState().layers[1].symbology).toBeUndefined()
  })

  it('lo anterior al último turno no se reenvía al agente (salvo que se deshaga ahora)', () => {
    const ops = useOperaciones.getState()
    ops.ejecutar({ op: 'set_visibility', layer_id: 'lotes', args: { visible: false }, reason: null }, 'user')
    useOperaciones.setState({ marcaTurno: new Date(Date.now() + 1000) })
    expect(accionesDesdeUltimoTurno()).toEqual([])
  })
})

describe('selección compartida en el reducer (FH.2)', () => {
  const conDatos = (id: string) => capa(id, {
    data: { type: 'FeatureCollection', features: [10, 120, 300, 90].map((a, i) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [i, 0] }, properties: { area_m2: a } })) } as never,
  })
  const LOTES = [conDatos('lotes'), capa('teselas', { kind: 'vector-mvt' })]
  const sel = (layer_id: string, args: Record<string, unknown>): Operacion =>
    ({ op: 'select', layer_id, args: { mode: 'replace', origin: 'click', ids: null, where: null, count: null, ...args },
       reason: null }) as Operacion

  it('clic, shift+clic (toggle) y añadir combinan los ids', () => {
    let ls = reducir(LOTES, sel('lotes', { ids: [0] })).layers
    ls = reducir(ls, sel('lotes', { ids: [2], mode: 'toggle' })).layers
    expect(ls[0].seleccion).toMatchObject({ ids: [0, 2], count: 2 })
    ls = reducir(ls, sel('lotes', { ids: [0], mode: 'toggle' })).layers
    expect(ls[0].seleccion?.ids).toEqual([2])
  })

  it('una condición en memoria se vuelve ids; sobre teselas queda como condición con su recuento', () => {
    const w = { field: 'area_m2', op: '>', value: 100 }
    expect(reducir(LOTES, sel('lotes', { where: w, origin: 'agent' })).layers[0].seleccion)
      .toMatchObject({ ids: [1, 2], count: 2, origin: 'agent' })
    expect(reducir(LOTES, sel('teselas', { where: w, count: 48210, origin: 'agent' })).layers[1].seleccion)
      .toMatchObject({ where: w, count: 48210 })
  })

  it('aplicar + deshacer = identidad también para seleccionar y limpiar', () => {
    const { layers, inversa } = reducir(LOTES, sel('lotes', { ids: [1, 3] }))
    expect(inversa!(layers)).toEqual(LOTES)
    const conSel = layers
    const limpio = reducir(conSel, { op: 'clear_selection', layer_id: null, args: {}, reason: null } as Operacion)
    expect(limpio.layers[0].seleccion).toBeNull()
    expect(limpio.inversa!(limpio.layers)).toEqual(conSel)
  })

  it('una selección vacía no deja nada seleccionado', () => {
    expect(reducir(LOTES, sel('lotes', { where: { field: 'area_m2', op: '>', value: 9999 } })).layers[0].seleccion).toBeNull()
  })
})
