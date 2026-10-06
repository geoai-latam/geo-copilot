import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MapLegend } from './MapLegend'
import { useMapStore, type MapLayer } from '@/stores/mapStore'

function layer(partial: Partial<MapLayer>): MapLayer {
  return {
    id: partial.id ?? 'l1',
    name: partial.name ?? 'Capa',
    kind: partial.kind ?? 'vector-geojson',
    data: { type: 'FeatureCollection', features: [] } as never,
    visible: partial.visible ?? true,
    color: partial.color ?? '#ff0000',
    featureCount: 0,
    addedAt: new Date(0),
    opacity: 1,
    ...partial,
  }
}

describe('MapLegend', () => {
  beforeEach(() => {
    useMapStore.setState({ layers: [] })
  })

  it('no renderiza nada sin capas', () => {
    const { container } = render(<MapLegend />)
    expect(container.querySelector('.map-legend')).toBeNull()
  })

  it('muestra un swatch por clase con su label (unique_values)', () => {
    useMapStore.setState({
      layers: [
        layer({
          name: 'Lotes por estrato',
          symbology: {
            symbology_type: 'unique_values',
            class_breaks: [
              { label: 'Estrato 1', color: '#111111' },
              { label: 'Estrato 2', color: '#222222' },
            ],
          },
        }),
      ],
    })
    render(<MapLegend />)
    expect(screen.getByText('Estrato 1')).toBeTruthy()
    expect(screen.getByText('Estrato 2')).toBeTruthy()
    expect(screen.getByText('Lotes por estrato')).toBeTruthy()
  })

  it('single_symbol muestra un swatch único', () => {
    useMapStore.setState({
      layers: [layer({ name: 'Puntos', symbology: { symbology_type: 'single_symbol' } })],
    })
    render(<MapLegend />)
    expect(screen.getByText('Todos los elementos')).toBeTruthy()
  })

  it('omite capas ocultas', () => {
    useMapStore.setState({
      layers: [layer({ name: 'Oculta', visible: false, symbology: { symbology_type: 'single_symbol' } })],
    })
    const { container } = render(<MapLegend />)
    expect(container.querySelector('.map-legend')).toBeNull()
  })

  it('incluye el count de la clase cuando existe', () => {
    useMapStore.setState({
      layers: [
        layer({
          symbology: {
            symbology_type: 'graduated_colors',
            class_breaks: [{ label: '0–10', color: '#abc', count: 42 }],
          },
        }),
      ],
    })
    render(<MapLegend />)
    expect(screen.getByText('0–10 (42)')).toBeTruthy()
  })
})
