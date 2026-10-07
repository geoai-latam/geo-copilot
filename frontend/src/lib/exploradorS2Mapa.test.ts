import { beforeEach, describe, expect, it } from 'vitest'

import { useMapStore } from '@/stores'
import { esCuadriculaS2 } from './exploradorS2Mapa'

const poligono = { type: 'Polygon', coordinates: [[[-74.5, 4.5], [-73.5, 4.5], [-73.5, 5.4], [-74.5, 5.4], [-74.5, 4.5]]] }
const cuadricula = {
  type: 'FeatureCollection' as const,
  features: [{ type: 'Feature' as const, id: 0, geometry: poligono as never, properties: { tile: '18NWL', nubes_min: 1, escenas: 25 } }],
}
const simbologia = { symbology_type: 'graduated_colors', classification_field: 'nubes_min', fill: { color: '#1a9850', opacity: 0.45 } }

describe('explorador S2: una escena nueva no queda teñida por la cuadrícula', () => {
  beforeEach(() => useMapStore.setState({ layers: [] }))

  it('reconoce las cuadrículas del explorador por sus campos', () => {
    const id = useMapStore.getState().addLayer(cuadricula, 'Mundo', simbologia as never)
    const otra = useMapStore.getState().addLayer({ ...cuadricula, features: [{ ...cuadricula.features[0], properties: { nombre: 'x' } }] }, 'Lotes')
    const capas = useMapStore.getState().layers
    expect(esCuadriculaS2(capas.find((l) => l.id === id)!)).toBe(true)
    expect(esCuadriculaS2(capas.find((l) => l.id === otra)!)).toBe(false)
  })

  it('al entrar una escena de imagery (del panel o del agente) la cuadrícula queda en contorno y sin selección', () => {
    const map = useMapStore.getState()
    const id = map.addLayer(cuadricula, 'Mundo', simbologia as never)
    useMapStore.setState({ layers: useMapStore.getState().layers.map((l) => (l.id === id
      ? { ...l, seleccion: { ids: [0], count: 1, origin: 'click' as const } } : l)) })
    map.addRasterLayer({ url: '/api/v1/proxy/mcp/imagery/tiles-rgb/S2X/true_color/{z}/{x}/{y}.png', name: 'color',
      origen: { capability: 'mcp.imagery.imagery_scene_view' } })
    const g = useMapStore.getState().layers.find((l) => l.id === id)!
    expect(g.symbology?.fill?.opacity).toBe(0)
    expect(g.symbology?.classification_field).toBe('nubes_min')        // conserva su simbología
    expect(g.seleccion ?? null).toBeNull()
  })

  it('un raster que no es de imagery no la toca', () => {
    const map = useMapStore.getState()
    const id = map.addLayer(cuadricula, 'Mundo', simbologia as never)
    map.addRasterLayer({ url: 'https://otro.test/{z}/{x}/{y}.png', name: 'base' })
    expect(useMapStore.getState().layers.find((l) => l.id === id)!.symbology?.fill?.opacity).toBe(0.45)
  })
})
