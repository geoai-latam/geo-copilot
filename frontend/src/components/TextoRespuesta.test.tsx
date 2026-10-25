/**
 * Los enlaces al mapa de una respuesta (FH.7). El tachado (`ref-rota`) se reserva para un enlace
 * que SÍ apuntó a una capa y se rompió al limpiar el mapa; una respuesta SIN geometría (su turno no
 * pintó nada) muestra el texto normal, no tachado.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

import { TextoRespuesta } from './TextoRespuesta'
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

describe('TextoRespuesta · enlaces al mapa', () => {
  beforeEach(() => {
    useMapStore.setState({ layers: [] })
  })

  it('respuesta SIN geometría (turno sin capas): el enlace es texto normal, no tachado', () => {
    render(<TextoRespuesta texto="174 víctimas en [[layer:activa?mpio=05313|Granada, Antioquia]]." delTurno={[]} />)
    expect(screen.queryByTestId('ref-rota')).toBeNull()      // no se tacha
    expect(screen.queryByTestId('ref-mapa')).toBeNull()      // ni es un enlace muerto
    expect(screen.getByText(/Granada, Antioquia/)).toBeTruthy()  // sale el texto, limpio
  })

  it('el turno pintó una capa que ya no está en el mapa: el enlace se tacha (roto)', () => {
    render(<TextoRespuesta texto="Pintada [[layer:l1|la vereda]]." delTurno={['l1']} />)
    const roto = screen.getByTestId('ref-rota')
    expect(roto.textContent).toBe('la vereda')
    expect(roto.getAttribute('title')).toMatch(/ya no está en el mapa/)
  })

  it('la capa sigue en el mapa: el enlace es un botón cliqueable', () => {
    useMapStore.setState({ layers: [layer({ id: 'l1', name: 'Veredas' })] })
    render(<TextoRespuesta texto="Pintada [[layer:l1|la vereda]]." delTurno={['l1']} />)
    expect(screen.getByTestId('ref-mapa')).toBeTruthy()
    expect(screen.queryByTestId('ref-rota')).toBeNull()
  })
})
