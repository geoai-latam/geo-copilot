/**
 * Tests for Map Store
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useMapStore, LAYER_COLORS, type BaseMapId } from './mapStore'
import type { GeoJSONFeatureCollection } from '@/types'

describe('useMapStore', () => {
  const mockGeoJSON: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [-74.0, 4.7] },
        properties: { name: 'Test Point' },
      },
    ],
  }

  const mockMultiFeatureGeoJSON: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { id: 1 } },
      { type: 'Feature', geometry: { type: 'Point', coordinates: [1, 1] }, properties: { id: 2 } },
      { type: 'Feature', geometry: { type: 'Point', coordinates: [2, 2] }, properties: { id: 3 } },
    ],
  }

  beforeEach(() => {
    // Reset store state
    useMapStore.setState({
      layers: [],
      baseMapId: 'osm',
      mapCenter: [-74.0721, 4.711],
      mapZoom: 10,
      selectedFeature: null,
      flyToLayerId: null,
    })
  })

  describe('Layer Management', () => {
    it('should add a layer with default name', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockGeoJSON)

      const layers = useMapStore.getState().layers
      expect(layers).toHaveLength(1)
      expect(layers[0].data).toBe(mockGeoJSON)
      expect(layers[0].visible).toBe(true)
      expect(layers[0].featureCount).toBe(1)
      expect(layers[0].opacity).toBe(1)
    })

    it('setLayerOpacity actualiza y clampa a [0,1]', () => {
      const { addLayer } = useMapStore.getState()
      addLayer(mockGeoJSON, 'Capa')
      const id = useMapStore.getState().layers[0].id

      useMapStore.getState().setLayerOpacity(id, 0.3)
      expect(useMapStore.getState().layers[0].opacity).toBe(0.3)

      useMapStore.getState().setLayerOpacity(id, 5) // fuera de rango → clamp a 1
      expect(useMapStore.getState().layers[0].opacity).toBe(1)

      useMapStore.getState().setLayerOpacity(id, -2) // clamp a 0
      expect(useMapStore.getState().layers[0].opacity).toBe(0)
    })

    it('setLayerLabelField fija y limpia el campo de etiqueta', () => {
      const { addLayer } = useMapStore.getState()
      addLayer(mockGeoJSON, 'Capa')
      const id = useMapStore.getState().layers[0].id

      useMapStore.getState().setLayerLabelField(id, 'nombre')
      expect(useMapStore.getState().layers[0].labelField).toBe('nombre')

      useMapStore.getState().setLayerLabelField(id, '') // vacío → null
      expect(useMapStore.getState().layers[0].labelField).toBeNull()
    })

    it('should add a layer with custom name', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Custom Layer Name')

      expect(useMapStore.getState().layers[0].name).toBe('Custom Layer Name')
    })

    it('should use symbology layer_title over custom name', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Fallback Name', { layer_title: 'Symbology Title' })

      expect(useMapStore.getState().layers[0].name).toBe('Symbology Title')
    })

    it('should assign colors from LAYER_COLORS in sequence', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer 1')
      addLayer(mockGeoJSON, 'Layer 2')
      addLayer(mockGeoJSON, 'Layer 3')

      const layers = useMapStore.getState().layers
      expect(layers[0].color).toBe(LAYER_COLORS[0])
      expect(layers[1].color).toBe(LAYER_COLORS[1])
      expect(layers[2].color).toBe(LAYER_COLORS[2])
    })

    it('should cycle through colors when exceeding LAYER_COLORS length', () => {
      const { addLayer } = useMapStore.getState()

      for (let i = 0; i < LAYER_COLORS.length + 2; i++) {
        addLayer(mockGeoJSON, `Layer ${i}`)
      }

      const layers = useMapStore.getState().layers
      expect(layers[LAYER_COLORS.length].color).toBe(LAYER_COLORS[0])
      expect(layers[LAYER_COLORS.length + 1].color).toBe(LAYER_COLORS[1])
    })

    it('should use fill color from symbology if provided', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer', { fill: { color: '#ff0000' } })

      expect(useMapStore.getState().layers[0].color).toBe('#ff0000')
    })

    it('should use stroke color from symbology if fill not provided', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer', { stroke: { color: '#00ff00' } })

      expect(useMapStore.getState().layers[0].color).toBe('#00ff00')
    })

    it('should use marker color from symbology if fill/stroke not provided', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer', { marker: { color: '#0000ff' } })

      expect(useMapStore.getState().layers[0].color).toBe('#0000ff')
    })

    it('should count features correctly', () => {
      const { addLayer } = useMapStore.getState()

      addLayer(mockMultiFeatureGeoJSON, 'Multi Feature Layer')

      expect(useMapStore.getState().layers[0].featureCount).toBe(3)
    })

    it('should handle empty feature collection', () => {
      const { addLayer } = useMapStore.getState()
      const emptyGeoJSON: GeoJSONFeatureCollection = {
        type: 'FeatureCollection',
        features: [],
      }

      addLayer(emptyGeoJSON, 'Empty Layer')

      expect(useMapStore.getState().layers[0].featureCount).toBe(0)
    })

    it('should remove a layer by id', () => {
      const { addLayer, removeLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer 1')
      addLayer(mockGeoJSON, 'Layer 2')

      const layerId = useMapStore.getState().layers[0].id
      removeLayer(layerId)

      const layers = useMapStore.getState().layers
      expect(layers).toHaveLength(1)
      expect(layers[0].name).toBe('Layer 2')
    })

    it('should handle removing non-existent layer', () => {
      const { addLayer, removeLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer 1')
      removeLayer('non-existent-id')

      expect(useMapStore.getState().layers).toHaveLength(1)
    })

    it('should toggle layer visibility', () => {
      const { addLayer, toggleLayerVisibility } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer')
      const layerId = useMapStore.getState().layers[0].id

      expect(useMapStore.getState().layers[0].visible).toBe(true)

      toggleLayerVisibility(layerId)
      expect(useMapStore.getState().layers[0].visible).toBe(false)

      toggleLayerVisibility(layerId)
      expect(useMapStore.getState().layers[0].visible).toBe(true)
    })

    it('should clear all layers', () => {
      const { addLayer, clearAllLayers } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer 1')
      addLayer(mockGeoJSON, 'Layer 2')
      addLayer(mockGeoJSON, 'Layer 3')

      clearAllLayers()

      expect(useMapStore.getState().layers).toHaveLength(0)
    })

    it('should reorder layers correctly', () => {
      const { addLayer, moveLayer } = useMapStore.getState()

      const a = addLayer(mockGeoJSON, 'A')
      addLayer(mockGeoJSON, 'B')
      addLayer(mockGeoJSON, 'C')

      moveLayer(a, 2)

      const names = useMapStore.getState().layers.map(l => l.name)
      expect(names).toEqual(['B', 'C', 'A'])
    })

    it('should reorder layers from middle to start', () => {
      const { addLayer, moveLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'A')
      addLayer(mockGeoJSON, 'B')
      const c = addLayer(mockGeoJSON, 'C')

      moveLayer(c, 0)

      const names = useMapStore.getState().layers.map(l => l.name)
      expect(names).toEqual(['C', 'A', 'B'])
    })
  })

  describe('Base Map', () => {
    it('should set base map', () => {
      const { setBaseMap } = useMapStore.getState()

      setBaseMap('arcgis-imagery')
      expect(useMapStore.getState().baseMapId).toBe('arcgis-imagery')

      setBaseMap('carto-dark')
      expect(useMapStore.getState().baseMapId).toBe('carto-dark')
    })

    it('should accept all valid base map types', () => {
      const { setBaseMap } = useMapStore.getState()

      const validBaseMaps: BaseMapId[] = [
        'osm', 'osm-hot', 'arcgis-imagery', 'arcgis-imagery-labels',
        'arcgis-streets', 'arcgis-topo', 'arcgis-dark', 'arcgis-ocean',
        'carto-positron', 'carto-dark', 'carto-voyager'
      ]

      validBaseMaps.forEach(baseMap => {
        setBaseMap(baseMap)
        expect(useMapStore.getState().baseMapId).toBe(baseMap)
      })
    })
  })

  describe('Map View', () => {
    it('should set map view', () => {
      const { setMapView } = useMapStore.getState()

      setMapView([-73.0, 5.0], 15)

      expect(useMapStore.getState().mapCenter).toEqual([-73.0, 5.0])
      expect(useMapStore.getState().mapZoom).toBe(15)
    })

    it('should handle negative coordinates', () => {
      const { setMapView } = useMapStore.getState()

      setMapView([-180, -90], 1)

      expect(useMapStore.getState().mapCenter).toEqual([-180, -90])
    })
  })

  describe('Feature Selection', () => {
    it('should set selected feature', () => {
      const { setSelectedFeature } = useMapStore.getState()
      const feature = { type: 'Feature', properties: { id: 1 } }

      setSelectedFeature(feature)

      expect(useMapStore.getState().selectedFeature).toEqual(feature)
    })

    it('should clear selected feature', () => {
      const { setSelectedFeature } = useMapStore.getState()

      setSelectedFeature({ type: 'Feature' })
      setSelectedFeature(null)

      expect(useMapStore.getState().selectedFeature).toBeNull()
    })
  })

  describe('Fly To Layer', () => {
    it('should set fly to layer id', () => {
      const { addLayer, flyToLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer')
      const layerId = useMapStore.getState().layers[0].id

      flyToLayer(layerId)

      expect(useMapStore.getState().flyToLayerId).toBe(layerId)
    })

    it('should clear fly to layer', () => {
      const { addLayer, flyToLayer, clearFlyToLayer } = useMapStore.getState()

      addLayer(mockGeoJSON, 'Layer')
      flyToLayer(useMapStore.getState().layers[0].id)
      clearFlyToLayer()

      expect(useMapStore.getState().flyToLayerId).toBeNull()
    })
  })
})

describe('LAYER_COLORS constant', () => {
  it('should have 8 colors', () => {
    expect(LAYER_COLORS).toHaveLength(8)
  })

  it('should have valid hex colors', () => {
    LAYER_COLORS.forEach(color => {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i)
    })
  })

  it('should have unique colors', () => {
    const uniqueColors = new Set(LAYER_COLORS)
    expect(uniqueColors.size).toBe(LAYER_COLORS.length)
  })
})
