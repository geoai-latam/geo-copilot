import { describe, it, expect, beforeEach, vi } from 'vitest'

// El viewport real depende del mapa MapLibre; lo mockeamos.
vi.mock('@/lib/mapViewport', () => ({
  getViewBounds: () => [-74.1, 4.6, -74.0, 4.7] as [number, number, number, number],
}))

import { buildMapContext } from './mapContext'
import { useMapStore } from '@/stores/mapStore'

const fc = (props: Record<string, unknown>) => ({
  type: 'FeatureCollection' as const,
  features: [
    {
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [0, 0] },
      properties: props,
    },
  ],
})

describe('buildMapContext', () => {
  beforeEach(() => {
    useMapStore.setState({
      layers: [],
      baseMapId: 'osm',
      mapCenter: [-74, 4.7],
      mapZoom: 12,
      selectedFeature: null,
      puntoMarcado: null,
    } as never)
  })

  it('snapshot vacío cuando no hay capas', () => {
    const mc = buildMapContext()
    expect(mc.layers).toEqual([])
    expect(mc.viewport?.bbox).toEqual([-74.1, 4.6, -74.0, 4.7])
    expect(mc.basemap).toBe('osm')
  })

  it('marca la última capa como activa y adjunta data a TODAS las visibles (FRT-04)', () => {
    useMapStore.getState().addLayer(fc({ codigo: '1', area: 10 }), 'Parcelas')
    useMapStore.getState().addLayer(fc({ tipo: 'via' }), 'Vias')

    const mc = buildMapContext()
    expect(mc.layers).toHaveLength(2)

    const active = mc.layers.find((l) => l.is_active)
    expect(active?.name).toBe('Vias')          // la última añadida
    expect(active?.data).toBeDefined()
    expect(active?.geometry_type).toBe('Point')

    // FRT-04: la inactiva-pero-visible SÍ lleva data (dentro del presupuesto) —
    // así el backend puede operar sobre la capa que el usuario NOMBRE, no solo la
    // activa. (Antes solo la activa la llevaba y "colorea los predios" fallaba.)
    const inactive = mc.layers.find((l) => !l.is_active)
    expect(inactive?.name).toBe('Parcelas')
    expect(inactive?.data).toBeDefined()
    expect(inactive?.fields).toContain('codigo')
  })

  it('respeta el presupuesto: una capa visible enorme va sin data (solo metadatos)', () => {
    useMapStore.setState({
      layers: [
        { id: 'a', name: 'Grande', kind: 'vector-geojson', data: fc({ x: 1 }), visible: true,
          featureCount: 25_000, color: '#000', addedAt: new Date(), opacity: 1 },
        { id: 'b', name: 'Chica', kind: 'vector-geojson', data: fc({ y: 2 }), visible: true,
          featureCount: 5, color: '#000', addedAt: new Date(), opacity: 1 },
      ],
    } as never)

    const mc = buildMapContext()
    const chica = mc.layers.find((l) => l.name === 'Chica')
    const grande = mc.layers.find((l) => l.name === 'Grande')

    // 'Chica' es la activa (última) → entra primero; 'Grande' (25k) excede el tope.
    expect(chica?.is_active).toBe(true)
    expect(chica?.data).toBeDefined()
    expect(grande?.data).toBeUndefined()      // excede FEATURE_BUDGET → sin data
    expect(grande?.fields).toContain('x')     // pero el LLM igual conoce sus campos
  })

  /**
   * Auditoría 2026-09-08 §5 (5): contar features no acota el cuerpo del POST.
   * Una capa por debajo de FEATURE_BUDGET puede pesar decenas de MB y chocar
   * contra `client_max_body_size 50m` (docker/nginx.frontend.conf:75) → 413 en
   * TODAS las consultas siguientes, no solo en la que cargó la capa.
   */
  it('respeta el presupuesto de BYTES aunque quepa en features', () => {
    // 200 features (muy por debajo de las 20.000) pero ~50 KB cada una:
    // ~10 MB serializados, por encima del presupuesto de bytes.
    const gordo = {
      type: 'FeatureCollection' as const,
      features: Array.from({ length: 200 }, () => ({
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates: [-74.0721456, 4.7110123] },
        properties: { codigo: 'X', wkt: 'x'.repeat(50_000) },
      })),
    }
    useMapStore.setState({
      layers: [
        { id: 'a', name: 'Pesada', kind: 'vector-geojson', data: gordo, visible: true,
          featureCount: 200, color: '#000', addedAt: new Date(), opacity: 1 },
        { id: 'b', name: 'Chica', kind: 'vector-geojson', data: fc({ y: 2 }), visible: true,
          featureCount: 5, color: '#000', addedAt: new Date(), opacity: 1 },
      ],
    } as never)

    const mc = buildMapContext()
    const pesada = mc.layers.find((l) => l.name === 'Pesada')
    const chica = mc.layers.find((l) => l.name === 'Chica')

    expect(pesada?.feature_count).toBe(200)          // cabe de sobra en features…
    expect(pesada?.data).toBeUndefined()             // …y aun así viaja sin data
    expect(pesada?.fields).toContain('codigo')       // el LLM sí conoce sus campos
    expect(chica?.data).toBeDefined()                // la pequeña no paga el pato
  })

  it('incluye la feature seleccionada', () => {
    useMapStore.setState({
      selectedFeature: { properties: { id: 7 }, layerName: 'Parcelas' },
    } as never)
    const mc = buildMapContext()
    expect(mc.selected_feature?.layer_id).toBe('Parcelas')
    expect(mc.selected_feature?.properties).toEqual({ id: 7 })
  })

  it('T2.0a: una capa del workspace viaja por referencia, sin data ni presupuesto', () => {
    useMapStore.getState().addLayer(fc({ codigo: '1' }), 'Lotes', undefined, 'ds_abc')
    useMapStore.getState().addLayer(fc({ tipo: 'via' }), 'Vias')

    const mc = buildMapContext()
    const lotes = mc.layers.find((l) => l.name === 'Lotes')!
    expect(lotes.dataset_id).toBe('ds_abc')
    expect(lotes.data).toBeUndefined()
    // los metadatos siguen: el LLM conoce la capa por nombre y campos
    expect(lotes.fields).toEqual(['codigo'])
    // la capa sin dataset sigue viajando con su geometría, como antes
    const vias = mc.layers.find((l) => l.name === 'Vias')!
    expect(vias.dataset_id).toBeUndefined()
    expect(vias.data).toBeDefined()
  })

  describe('S4.4 — por referencias (E4.5)', () => {
    const cargarMapaGrande = () => {
      const m = useMapStore.getState()
      // 60 000 lotes en teselas del workspace + 3 capas del workspace + un NDVI de un MCP.
      m.addLayer({ type: 'FeatureCollection', features: [] } as never, 'Lotes (todos)', undefined, 'ds_0123456789abcdef', {
        url: '/api/v1/tiles/ws/s1/ds_0123456789abcdef/{z}/{x}/{y}.pbf', sourceLayer: 'dataset',
        geometryType: 'Polygon', bbox: [-74.2, 4.5, -74.0, 4.8], featureCount: 60_000,
        fields: Array.from({ length: 30 }, (_, i) => `campo_${i}`),
      })
      for (const n of [1, 2, 3]) m.addLayer(fc({ a: 1 }) as never, `Capa ${n}`, undefined, `ds_${String(n).repeat(16)}`)
      return m.addRasterLayer({
        url: '/api/v1/proxy/mcp/imagery/tiles/S2B_18NWL_20260117_0_L2A/{z}/{x}/{y}.png?rescale=-0.2,0.9',
        name: 'NDVI 2026-01-17', kind: 'raster-xyz',
        extent: { xmin: -74.2, ymin: 4.5, xmax: -74.0, ymax: 4.7 },
        legend: { type: 'ramp', field: 'NDVI', min: 0.05, max: 0.85 } as never,
        origen: { capability: 'mcp.imagery.imagery_ndvi', arguments: { date_from: '2026-01-01' } },
      })
    }

    it('el agente ve la capa NDVI: tipo, teselas, procedencia, extensión y rampa', () => {
      const id = cargarMapaGrande()
      const ndvi = buildMapContext().layers.find((l) => l.id === id)!
      expect(ndvi.kind).toBe('raster-xyz')
      expect(ndvi.url).toContain('S2B_18NWL_20260117_0_L2A')
      expect(ndvi.origin).toEqual({ capability: 'mcp.imagery.imagery_ndvi', arguments: { date_from: '2026-01-01' } })
      expect(ndvi.bbox).toEqual([-74.2, 4.5, -74.0, 4.7])
      expect(ndvi.legend).toEqual({ field: 'NDVI', min: 0.05, max: 0.85 })
      expect(ndvi.data).toBeUndefined()
    })

    it('una capa MVT viaja con su tipo y su extensión, sin features', () => {
      cargarMapaGrande()
      const mvt = buildMapContext().layers.find((l) => l.name === 'Lotes (todos)')!
      expect(mvt).toMatchObject({ kind: 'vector-mvt', feature_count: 60_000, dataset_id: 'ds_0123456789abcdef' })
      expect(mvt.bbox).toEqual([-74.2, 4.5, -74.0, 4.8])
      expect(mvt.data).toBeUndefined()
    })

    it('el punto marcado es el «aquí»', () => {
      useMapStore.getState().setPuntoMarcado({ lon: -74.1, lat: 4.6 })
      expect(buildMapContext().clicked_point).toEqual({ lon: -74.1, lat: 4.6 })
    })

    it('con capas grandes el map_context pesa menos de 5 KB', () => {
      cargarMapaGrande()
      useMapStore.getState().setPuntoMarcado({ lon: -74.1, lat: 4.6 })
      const bytes = new TextEncoder().encode(JSON.stringify(buildMapContext())).length
      expect(bytes).toBeLessThan(5 * 1024)
    })
  })

  it('FH.4: las menciones viajan como referencias y, sin selección, la capa va sin ella', () => {
    useMapStore.setState({ layers: [] })
    const id = useMapStore.getState().addLayer({ type: 'FeatureCollection', features: [] } as never, 'Lotes')
    useMapStore.setState((s) => ({ layers: s.layers.map((l) => ({ ...l, seleccion: { ids: [1], count: 1, origin: 'click' as const } })) }))
    const m = { tipo: 'capa' as const, layer_id: id, texto: '@Lotes' }
    const con = buildMapContext({ menciones: [m] })
    expect(con.menciones).toEqual([m])
    expect(con.layers[0].seleccion).toMatchObject({ ids: [1], count: 1 })
    const sin = buildMapContext({ sinSeleccion: true })
    expect(sin.layers[0].seleccion).toBeUndefined()
    expect(sin.seleccion_excluida).toEqual({ layer_id: id, layer_name: 'Lotes', count: 1 })
    expect(sin.alcance_seleccion).toBeUndefined()
    expect(con.alcance_seleccion).toBe(true)
    expect(sin.menciones).toBeUndefined()
  })
})
