import { describe, it, expect, beforeEach } from 'vitest'

import type { QueryResponse } from '@/contracts'
import { aplicarArtefactos } from './aplicarArtefactos'
import { useComparacion } from './comparacion'
import { seccionesVectoriales } from './identificar'
import { fechasDeSerie, useTiempo } from './tiempo'
import { visibleEfectiva } from './visibilidad'
import { useVistas } from './vistas'
import { useMapStore, type MapLayer } from '@/stores/mapStore'

const raster = (id: string, fecha: string | null, visible = true) =>
  ({ id, name: `NDVI ${fecha}`, kind: 'raster-xyz', fecha, visible, color: '#000',
     data: { type: 'FeatureCollection', features: [] } }) as unknown as MapLayer

describe('FH.10 — vistas, cortina y tiempo', () => {
  beforeEach(() => {
    useComparacion.getState().cerrar()
    useTiempo.setState({ actual: null, reproduciendo: false })
    useMapStore.setState({ layers: [] } as never)
  })

  it('la serie: fechas ordenadas y sin repetir; sin fecha no cuenta', () => {
    expect(fechasDeSerie([raster('a', '2026-06-18'), raster('b', '2026-03-14'), raster('c', null), raster('d', '2026-03-14')]))
      .toEqual(['2026-03-14', '2026-06-18'])
  })

  it('lo que se dibuja: la del usuario, menos la derecha de la cortina, y de la serie solo la fecha elegida', () => {
    const [m, j, oculta] = [raster('m', '2026-03-14'), raster('j', '2026-06-18'), raster('x', null, false)]
    const todas = [m, j, oculta]
    expect([m, j, oculta].map((c) => visibleEfectiva(c, todas))).toEqual([true, true, false])
    useTiempo.getState().ir('2026-03-14')
    expect([m, j].map((c) => visibleEfectiva(c, todas))).toEqual([true, false])
    useTiempo.getState().ir(null)
    useComparacion.getState().abrir('m', 'j')
    expect([m, j].map((c) => visibleEfectiva(c, todas))).toEqual([true, false]) // j la dibuja la cortina
  })

  it('las órdenes del agente: compara por NOMBRE las capas de este turno; tiempo y vista', () => {
    const cmd = (command: unknown) => ({ kind: 'map_command', command }) as never
    const r1 = useMapStore.getState().addRasterLayer({ url: '/a/{z}/{x}/{y}.png', name: 'NDVI 2026-03-14', fecha: '2026-03-14' })
    const r2 = useMapStore.getState().addRasterLayer({ url: '/b/{z}/{x}/{y}.png', name: 'NDVI 2026-06-18', fecha: '2026-06-18' })
    aplicarArtefactos({ artifacts: [
      cmd({ op: 'compare', layer_id: null, reason: null, args: { left: 'NDVI 2026-03-14', right: 'NDVI 2026-06-18' } }),
      cmd({ op: 'set_time', layer_id: null, reason: null, args: { time: '2026-06-18', play: true } }),
    ] } as unknown as QueryResponse)
    expect(useComparacion.getState().actual).toMatchObject({ left: r1, right: r2 })
    expect(useTiempo.getState()).toMatchObject({ actual: '2026-06-18', reproduciendo: true })
    aplicarArtefactos({ artifacts: [cmd({ op: 'end_compare', layer_id: null, reason: null, args: {} })] } as unknown as QueryResponse)
    expect(useComparacion.getState().actual).toBeNull()
  })

  it('guardar una vista con el mismo nombre la reemplaza', () => {
    (window as unknown as { __mapViewport: unknown }).__mapViewport = { getBounds: () => ({ toArray: () => [[-74.1, 4.6], [-74.0, 4.7]] }) }
    useVistas.getState().cargar('s-test')
    const cmd = { kind: 'map_command', command: { op: 'save_view', layer_id: null, reason: null, args: { nombre: 'Finca' } } }
    aplicarArtefactos({ artifacts: [cmd, cmd] } as unknown as QueryResponse)
    expect(useVistas.getState().vistas.map((v) => [v.nombre, v.bbox])).toEqual([['Finca', [-74.1, 4.6, -74.0, 4.7]]])
  })

  it('identificar: el elemento de encima de CADA capa, una vez por capa', () => {
    const capas = [{ id: 'lotes', name: 'Lotes' }, { id: 'vias', name: 'Vías' }]
    const hits = [{ source: 'lotes', properties: { lot: 'A' } }, { source: 'lotes', properties: { lot: 'B' } },
                  { source: 'vias', properties: { via: 'Cl 145' } }, { source: 'otra', properties: {} }]
    expect(seccionesVectoriales(hits, capas).map((s) => [s.layerName, s.properties])).toEqual([
      ['Lotes', { lot: 'A' }], ['Vías', { via: 'Cl 145' }]])
  })
})
