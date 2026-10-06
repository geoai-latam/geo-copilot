import { describe, it, expect, beforeEach } from 'vitest'

import type { QueryResponse } from '@/contracts'
import { aplicarArtefactos } from './aplicarArtefactos'
import { useMapStore } from '@/stores/mapStore'
import type { LayerSymbology } from '@/types'

const fc = (conArea: boolean) => ({
  type: 'FeatureCollection' as const,
  features: [0, 1, 2].map((i) => ({ type: 'Feature' as const, id: i, geometry: { type: 'Point' as const, coordinates: [-74, 4.6] },
                                    properties: { lotupredia: i * 100, ...(conArea ? { area_m2: i * 10 } : {}) } })),
})
const REDS = { symbology_type: 'graduated_colors', classification_field: 'lotupredia', color_scheme: 'Reds',
               class_breaks: [{ label: '0 – 200', color: '#fee5d9' }], pinned: ['color_scheme'] } as unknown as LayerSymbology

const respuesta = (datasetId: string, replaces: string): QueryResponse => ({
  intent: 'spatial_analysis',
  artifacts: [{ kind: 'layer', replaces, inline: fc(true),
                layer: { id: datasetId, name: 'Lotes', kind: 'vector', storage: { kind: 'workspace-table' } } }],
} as unknown as QueryResponse)

describe('FH — la MISMA capa con un campo más (V5: «el área de cada lote»)', () => {
  beforeEach(() => useMapStore.setState({ layers: [] } as never))

  it('conserva estilo, filtro, etiqueta, opacidad y nombre, y trae los datos nuevos', () => {
    const id = useMapStore.getState().addLayer(fc(false), 'Lotes Manzana 008', REDS, 'ds_lotes')
    useMapStore.setState((s) => ({ layers: s.layers.map((l) => ({ ...l, opacity: 0.5, labelField: 'lotupredia',
      filtro: [{ field: 'lotupredia', op: '>', value: 100 }], filtroCount: 1 })) }))
    aplicarArtefactos(respuesta('ds_lotes', id))
    const [capa] = useMapStore.getState().layers
    expect(useMapStore.getState().layers).toHaveLength(1)
    expect(capa.name).toBe('Lotes Manzana 008')
    expect(capa.symbology?.color_scheme).toBe('Reds')
    expect(capa.symbology?.pinned).toEqual(['color_scheme'])
    expect(capa.filtro).toEqual([{ field: 'lotupredia', op: '>', value: 100 }])
    expect(capa.filtroCount).toBe(1)
    expect(capa.labelField).toBe('lotupredia')
    expect(capa.opacity).toBe(0.5)
    expect(capa.data?.features[1].properties?.area_m2).toBe(10)
    // V5 EH.7: conserva también el id, así los enlaces de las respuestas siguen vivos
    expect(capa.id).toBe(id)
  })

  it('una capa DISTINTA que sustituye no hereda el filtro de la anterior', () => {
    const id = useMapStore.getState().addLayer(fc(false), 'Lotes', REDS, 'ds_lotes')
    useMapStore.setState((s) => ({ layers: s.layers.map((l) => ({ ...l, filtro: [{ field: 'lotupredia', op: '>', value: 100 }] })) }))
    aplicarArtefactos(respuesta('ds_otro', id))
    const [capa] = useMapStore.getState().layers
    expect(capa.filtro ?? null).toBeNull()
    expect(capa.datasetId).toBe('ds_otro')
  })
})

describe('FH.13 — rehacer un reemplazo en su sitio', () => {
  beforeEach(() => useMapStore.setState({ layers: [] } as never))

  it('Ctrl+Z vuelve a la capa de antes y Ctrl+Shift+Z vuelve a la nueva (antes no hacía nada)', async () => {
    const { useOperaciones } = await import('./operaciones')
    useOperaciones.getState().limpiar()
    const id = useMapStore.getState().addLayer(fc(false), 'Lotes', REDS, 'ds_lotes')
    aplicarArtefactos(respuesta('ds_lotes', id))
    const tieneArea = () => useMapStore.getState().layers[0].data?.features[1].properties?.area_m2 !== undefined
    expect(tieneArea()).toBe(true)
    useOperaciones.getState().deshacer()
    expect(tieneArea()).toBe(false)
    useOperaciones.getState().rehacerUltima()
    expect(tieneArea()).toBe(true)
    expect(useMapStore.getState().layers).toHaveLength(1)
  })
})
