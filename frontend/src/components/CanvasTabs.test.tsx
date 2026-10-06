/**
 * Auditoría 2026-09-08 §5 (3) — "Refrescar" ya no es un segundo cliente.
 *
 * El botón llamaba a `queryApi.process` por su cuenta, sin `map_context`, sin
 * `AbortSignal` y sin `target_layer_id`, y siempre con `addLayer`: en una
 * consulta de simbología duplicaba la capa. Lo que se fija aquí es que ya no
 * exista ese camino — que el botón delegue en el MISMO `runQuery` que el
 * chat, cuyas propiedades están probadas en `lib/runQuery.test.ts`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'

const runQuery = vi.fn()
vi.mock('@/lib/runQuery', () => ({
  runQuery: (...args: unknown[]) => runQuery(...args),
  abortActiveQuery: vi.fn(),
}))
// El canvas no debe hablar con la red por su cuenta. Si algún día alguien
// vuelve a meter un `queryApi.process` aquí, este mock lo delata.
const apiProcess = vi.fn()
vi.mock('@/services/api', () => ({
  queryApi: { process: (...args: unknown[]) => apiProcess(...args) },
}))

const { CanvasTabs } = await import('./CanvasTabs')
const { useChatStore, useSessionStore, useUIStore, useMapStore } = await import('@/stores')

const refreshBtn = (c: HTMLElement) =>
  c.querySelector('[title="Refrescar última consulta"]') as HTMLButtonElement

beforeEach(() => {
  runQuery.mockReset()
  apiProcess.mockReset()
  useChatStore.setState({ isLoading: false, queryHistory: [] } as never)
  useSessionStore.setState({ sessionId: 's1' } as never)
  useUIStore.setState({ activeVisualization: null } as never)
  useMapStore.setState({ layers: [] } as never)
})

describe('CanvasTabs — Refrescar', () => {
  it('delega en runQuery con la última consulta, sin tocar la API', () => {
    useChatStore.setState({
      queryHistory: [{ query: 'píntalos de rojo', timestamp: new Date(), success: true }],
    } as never)
    const { container } = render(<CanvasTabs><div>mapa</div></CanvasTabs>)
    fireEvent.click(refreshBtn(container))
    expect(runQuery).toHaveBeenCalledWith('píntalos de rojo')
    expect(apiProcess).not.toHaveBeenCalled()
  })

  it('sin historial el botón está deshabilitado y lo dice', () => {
    const { container } = render(<CanvasTabs><div>mapa</div></CanvasTabs>)
    const btn = container.querySelector('[title="Sin consulta previa"]') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('sin sesión el botón está deshabilitado, no falsamente activo', () => {
    useChatStore.setState({
      queryHistory: [{ query: 'algo', timestamp: new Date(), success: true }],
    } as never)
    useSessionStore.setState({ sessionId: null } as never)
    const { container } = render(<CanvasTabs><div>mapa</div></CanvasTabs>)
    const btn = container.querySelector('[title="Sin sesión activa"]') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('con una consulta en vuelo no se puede lanzar otra', () => {
    useChatStore.setState({
      isLoading: true,
      queryHistory: [{ query: 'algo', timestamp: new Date(), success: true }],
    } as never)
    const { container } = render(<CanvasTabs><div>mapa</div></CanvasTabs>)
    expect(refreshBtn(container).disabled).toBe(true)
  })
})
