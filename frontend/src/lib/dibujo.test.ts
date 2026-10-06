import { beforeEach, describe, expect, it } from 'vitest'

import { useMapStore, type MapLayer } from '@/stores/mapStore'
import { CAPACIDAD_DIBUJO, esDibujo, modoDeEdicion, nombreParaDibujo } from './dibujo'
import { accionesDesdeUltimoTurno, capaDelUsuario, reducir, useOperaciones } from './operaciones'

const capa = (id: string, extra: Partial<MapLayer> = {}): MapLayer => ({
  id, name: id, kind: 'vector-geojson', data: { type: 'FeatureCollection', features: [] },
  visible: true, color: '#111', featureCount: 1, addedAt: new Date(0), opacity: 1, ...extra,
})

describe('dibujos (FH.3)', () => {
  beforeEach(() => {
    useMapStore.setState({ layers: [], modoDibujo: null, editandoId: null, modoSeleccion: null })
    useOperaciones.getState().limpiar()
  })

  it('nombre por defecto: el siguiente libre de su tipo', () => {
    expect(nombreParaDibujo('polygon', [])).toBe('Área 1')
    expect(nombreParaDibujo('circle', [{ name: 'Área 1' }, { name: 'Área 3' }])).toBe('Área 2')
    expect(nombreParaDibujo('linestring', [{ name: 'Área 1' }])).toBe('Línea 1')
    expect(nombreParaDibujo('point', [{ name: 'Punto 1' }])).toBe('Punto 2')
  })

  it('qué se edita vértice a vértice (rectángulo y círculo se guardan como polígono)', () => {
    expect(modoDeEdicion('Polygon')).toBe('polygon')
    expect(modoDeEdicion('LineString')).toBe('linestring')
    expect(modoDeEdicion('Point')).toBe('point')
    expect(modoDeEdicion('MultiPolygon')).toBeNull()
  })

  it('una capa es dibujo por su procedencia', () => {
    expect(esDibujo(capa('a', { origen: { capability: CAPACIDAD_DIBUJO, arguments: {} } }))).toBe(true)
    expect(esDibujo(capa('b'))).toBe(false)
  })

  it('seleccionar y dibujar se excluyen', () => {
    useMapStore.getState().setModoSeleccion('box')
    useMapStore.getState().setModoDibujo('polygon')
    expect(useMapStore.getState().modoSeleccion).toBeNull()
    useMapStore.getState().setModoSeleccion('lasso')
    expect(useMapStore.getState().modoDibujo).toBeNull()
  })

  it('renombrar y editar vértices cambian la capa, van al registro y no se deshacen con Ctrl+Z', () => {
    useMapStore.setState({ layers: [capa('a', { name: 'Área 1' })] })
    capaDelUsuario('a', { dibujo: 'Polygon' })
    const nuevo = { type: 'FeatureCollection', features: [
      { type: 'Feature', id: 0, properties: {}, geometry: { type: 'Point', coordinates: [1, 2] } }] } as never
    const ops = useOperaciones.getState()
    ops.ejecutar({ op: 'rename_layer', layer_id: 'a', args: { nombre: 'Finca', antes: 'Área 1' } }, 'user')
    ops.ejecutar({ op: 'edit_geometry', layer_id: 'a', data: nuevo }, 'user')
    const l = useMapStore.getState().layers[0]
    expect(l.name).toBe('Finca')
    expect(l.data).toBe(nuevo)
    expect(accionesDesdeUltimoTurno().map((a) => [a.op, a.args, a.layer_name])).toEqual([
      ['add_layer', { dibujo: 'Polygon' }, 'Área 1'],
      ['rename_layer', { nombre: 'Finca', antes: 'Área 1' }, 'Área 1'],
      ['edit_geometry', {}, 'Finca'],
    ])
    // Ctrl+Z salta las dos (viven en el workspace) y deshace el alta de la capa
    expect(useOperaciones.getState().deshacer()?.op).toBe('add_layer')
    expect(reducir([capa('a')], { op: 'rename_layer', layer_id: 'a', args: { nombre: 'x', antes: 'a' } }).inversa).toBeNull()
  })
})
