/**
 * Auditoría pre-producción (V5): con cada WebSocket rechazado (origen no permitido) la barra decía
 * «Conectado» y la consulta esperaba en «Procesando…» una aprobación que nunca llegaba. El canal en
 * tiempo real es un hecho visible.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useSessionStore } from '@/stores'

vi.mock('./ProyectosControl', () => ({ ProyectosControl: () => null }))
vi.mock('./AuthGate', () => ({ MenuUsuario: () => null }))

import { TopBar } from './TopBar'

afterEach(() => {
  useSessionStore.setState({ sessionId: null, isConnected: false, portalConnected: false, bdConnected: false })
})

describe('TopBar', () => {
  it('sin tiempo real no dice «Conectado» y explica qué no llega', () => {
    useSessionStore.setState({ sessionId: 'abc123', isConnected: false, portalConnected: true, bdConnected: true })
    render(<TopBar />)
    expect(screen.getByText('Parcial')).toBeTruthy()
    const detalle = screen.getByText(/Tiempo real ✕/)
    expect(detalle.getAttribute('title')).toMatch(/aprobaciones/)
  })

  it('con todo conectado dice «Conectado»', () => {
    useSessionStore.setState({ sessionId: 'abc123', isConnected: true, portalConnected: true, bdConnected: true })
    render(<TopBar />)
    expect(screen.getByText('Conectado')).toBeTruthy()
    expect(screen.getByText(/Tiempo real ·/)).toBeTruthy()
  })
})
