import { describe, it, expect, beforeEach, vi } from 'vitest'

const exists = vi.fn()
const create = vi.fn()
const capa = vi.fn()
vi.mock('@/services/api', () => ({
  sessionApi: { exists: (...a: unknown[]) => exists(...a), create: (...a: unknown[]) => create(...a) },
  workspaceApi: { capa: (...a: unknown[]) => capa(...a) },
}))

import { _reiniciarSesionDeLaPestana, leerChat, recordarCapas, recordarChat, restaurarCapas, sesionDeLaPestana } from './workspaceRestore'
import { useMapStore } from '@/stores/mapStore'

const FC = {
  type: 'FeatureCollection' as const,
  features: [{ type: 'Feature' as const, geometry: { type: 'Point' as const, coordinates: [-74, 4.6] }, properties: { n: 1 } }],
}

describe('E2.4 — restaurar el workspace al recargar', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    _reiniciarSesionDeLaPestana()
    exists.mockReset()
    create.mockReset()
    capa.mockReset()
    useMapStore.setState({ layers: [] } as never)
  })

  it('la pestaña vuelve a SU sesión si sigue viva', async () => {
    window.sessionStorage.setItem('geo.session', 'sess-a')
    exists.mockResolvedValue(true)
    expect(await sesionDeLaPestana()).toEqual({ sessionId: 'sess-a', recuperada: true })
    expect(create).not.toHaveBeenCalled()
  })

  it('si la sesión murió (o no había), abre una nueva y la recuerda', async () => {
    window.sessionStorage.setItem('geo.session', 'sess-vieja')
    exists.mockResolvedValue(false)
    create.mockResolvedValue({ session_id: 'sess-nueva' })
    expect(await sesionDeLaPestana()).toEqual({ sessionId: 'sess-nueva', recuperada: false })
    expect(window.sessionStorage.getItem('geo.session')).toBe('sess-nueva')
  })

  it('dos inits simultáneos (StrictMode) comparten UNA sesión', async () => {
    create.mockResolvedValue({ session_id: 'sess-unica' })
    const [a, b] = await Promise.all([sesionDeLaPestana(), sesionDeLaPestana()])
    expect(a.sessionId).toBe('sess-unica')
    expect(b.sessionId).toBe('sess-unica')
    expect(create).toHaveBeenCalledTimes(1)
  })

  it('recuerda las capas con dataset (no las que solo viven en el navegador), con su estilo, y las restaura', async () => {
    const m = useMapStore.getState()
    m.addLayer(FC, 'Lotes', { symbology_type: 'single_symbol', fill: { color: '#ff0000' } } as never, 'ds_aaaaaaaaaaaaaaaa')
    m.addLayer(FC, 'Sin dataset')
    const lotes = useMapStore.getState().layers[0]
    m.setLayerOpacity(lotes.id, 0.5)
    m.setLayerLabelField(lotes.id, 'n')
    m.toggleLayerVisibility(lotes.id)
    recordarCapas('sess-a', useMapStore.getState().layers)

    useMapStore.setState({ layers: [] } as never)  // la recarga
    capa.mockResolvedValue({ layer_ref: { id: 'ds_aaaaaaaaaaaaaaaa' }, geojson: FC, tiles: null })
    expect(await restaurarCapas('sess-a')).toBe(1)

    expect(capa).toHaveBeenCalledWith('sess-a', 'ds_aaaaaaaaaaaaaaaa')
    const [r] = useMapStore.getState().layers
    expect(r.name).toBe('Lotes')
    expect(r.datasetId).toBe('ds_aaaaaaaaaaaaaaaa')
    expect(r.symbology?.fill?.color).toBe('#ff0000')
    expect(r.opacity).toBe(0.5)
    expect(r.labelField).toBe('n')
    expect(r.visible).toBe(false)
  })

  it('FH.1: al recargar la capa conserva su nombre aunque su estilo traiga otro título', async () => {
    const m = useMapStore.getState()
    m.addLayer(FC, 'Lotes', undefined, 'ds_bbbbbbbbbbbbbbbb')
    const [lotes] = useMapStore.getState().layers
    m.setLayerStyle(lotes.id, { symbology_type: 'single_symbol', layer_title: 'Lotes con código semitransparente' } as never)
    recordarCapas('sess-t', useMapStore.getState().layers)
    useMapStore.setState({ layers: [] } as never)
    capa.mockResolvedValue({ layer_ref: { id: 'ds_bbbbbbbbbbbbbbbb' }, geojson: FC, tiles: null })
    await restaurarCapas('sess-t')
    expect(useMapStore.getState().layers[0].name).toBe('Lotes')
  })

  it('V5 EH.7: al recargar la capa vuelve con SU id (los enlaces de la conversación siguen vivos)', async () => {
    const m = useMapStore.getState()
    m.addLayer(FC, 'Lotes', undefined, 'ds_cccccccccccccccc')
    const [lotes] = useMapStore.getState().layers
    recordarCapas('sess-id', useMapStore.getState().layers)
    useMapStore.setState({ layers: [] } as never)
    capa.mockResolvedValue({ layer_ref: { id: 'ds_cccccccccccccccc' }, geojson: FC, tiles: null })
    await restaurarCapas('sess-id')
    expect(useMapStore.getState().layers.map((l) => l.id)).toEqual([lotes.id])
  })

  it('FH.5 (V5 en Chrome): al recargar, la capa conserva su filtro y lo que fijó a mano en el estilo', async () => {
    const m = useMapStore.getState()
    const id = m.addLayer(FC, 'Lotes', undefined, 'ds_cccccccccccccccc')
    useMapStore.setState((st) => ({ layers: st.layers.map((l) => (l.id === id ? {
      ...l, filtro: [{ field: 'area', op: '>', value: 100 }], filtroCount: 3,
      symbology: { symbology_type: 'single_symbol', color_scheme: 'Reds', pinned: ['color_scheme'] } } : l)) }))
    recordarCapas('sess-f', useMapStore.getState().layers)
    useMapStore.setState({ layers: [] } as never)
    capa.mockResolvedValue({ layer_ref: { id: 'ds_cccccccccccccccc' }, geojson: FC, tiles: null })
    await restaurarCapas('sess-f')
    const l = useMapStore.getState().layers[0]
    expect(l.filtro).toEqual([{ field: 'area', op: '>', value: 100 }])
    expect(l.filtroCount).toBe(3)
    expect(l.symbology?.pinned).toEqual(['color_scheme'])
  })

  it('restaurar es idempotente: ni una capa ya presente ni un guardado duplicado se añaden dos veces', async () => {
    const m = useMapStore.getState()
    m.addLayer(FC, 'Lotes', undefined, 'ds_dddddddddddddddd')
    const [l] = useMapStore.getState().layers
    recordarCapas('sess-d', [l, { ...l, id: 'otra' }])  // un guardado que ya venía duplicado
    useMapStore.setState({ layers: [] } as never)
    capa.mockResolvedValue({ layer_ref: { id: 'ds_dddddddddddddddd' }, geojson: FC, tiles: null })
    expect(await restaurarCapas('sess-d')).toBe(1)
    expect(useMapStore.getState().layers).toHaveLength(1)
  })

  it('F4: los raster también vuelven, y en el mismo orden (NDVI encima de los lotes)', async () => {
    const m = useMapStore.getState()
    const lotes = m.addLayer(FC, 'Lotes', undefined, 'ds_aaaaaaaaaaaaaaaa')
    const ndvi = m.addRasterLayer({ url: '/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png', name: 'NDVI',
                                    legend: { field: 'NDVI', min: 0, max: 1 } })
    useMapStore.getState().moveLayer(ndvi, 1)  // el usuario lo subió encima
    useMapStore.getState().setLayerOpacity(ndvi, 0.4)
    expect(useMapStore.getState().layers.map((l) => l.id)).toEqual([lotes, ndvi])
    recordarCapas('sess-a', useMapStore.getState().layers)

    useMapStore.setState({ layers: [] } as never)  // la recarga
    capa.mockResolvedValue({ layer_ref: { id: 'ds_aaaaaaaaaaaaaaaa' }, geojson: FC, tiles: null })
    expect(await restaurarCapas('sess-a')).toBe(2)
    const capas = useMapStore.getState().layers
    expect(capas.map((l) => [l.name, l.kind])).toEqual([['Lotes', 'vector-geojson'], ['NDVI', 'raster-xyz']])
    expect(capas[1]).toMatchObject({ opacity: 0.4, legend: { field: 'NDVI', min: 0, max: 1 } })
  })

  it('una capa grande vuelve teselada y una vencida se omite sin tumbar las demás', async () => {
    window.sessionStorage.setItem('geo.layers.sess-a', JSON.stringify([
      { datasetId: 'ds_vencido0000000', name: 'Vieja', visible: true },
      { datasetId: 'ds_grande00000000', name: 'Grande', visible: true },
    ]))
    capa.mockImplementation(async (_s: string, id: string) => {
      if (id === 'ds_vencido0000000') throw new Error('404')
      return {
        layer_ref: { id }, geojson: null,
        tiles: { url: '/api/v1/tiles/ws/sess-a/x/{z}/{x}/{y}.pbf', source_layer: 'dataset',
          geometry_type: 'Polygon', bbox: [-74.2, 4.5, -74, 4.7], feature_count: 60000, fields: ['a'] },
      }
    })
    expect(await restaurarCapas('sess-a')).toBe(1)
    const [g] = useMapStore.getState().layers
    expect(g.name).toBe('Grande')
    expect(g.tiles?.featureCount).toBe(60000)
    expect(g.featureCount).toBe(60000)
  })

  it('V5 EH.11: la conversación guardada conserva las capas de cada turno (`activa` en sus enlaces)', () => {
    recordarChat('sess-c', [{ id: 'm1', role: 'assistant', content: 'ver [[layer:activa#0|el lote]]',
                              timestamp: new Date(), status: 'sent', capas: ['layer-7-1'] } as never])
    expect(leerChat('sess-c')[0].capas).toEqual(['layer-7-1'])
  })

  it('sin almacenamiento no restaura nada y no revienta', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('bloqueado') })
    expect(await restaurarCapas('sess-a')).toBe(0)
    spy.mockRestore()
  })
})
