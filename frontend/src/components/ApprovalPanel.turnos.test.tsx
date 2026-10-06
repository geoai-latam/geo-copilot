/**
 * F7 (auditoría) — resolver una aprobación sin consulta HTTP en vuelo en esta pestaña.
 *
 * Tras recargar con una aprobación pendiente, el panel vuelve a mostrarse; al aprobarla el turno
 * sigue en el servidor y su resultado llega por el WebSocket. Antes el chip pasaba a «Consultando…»
 * sin nada que lo devolviera a «Listo»: ni el resultado se aplicaba, ni un rechazo de una huérfana
 * lo cerraba.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const submit = vi.fn()
vi.mock('@/services/api', () => ({
  approvalApi: { submit: (...args: unknown[]) => submit(...args) },
}))

const { ApprovalPanel } = await import('./ApprovalPanel')
const { manejarAvisoDeTurno } = await import('@/lib/runQuery')
const { respuesta } = await import('@/contracts/fixtures')
const { useUIStore, useSessionStore, useChatStore } = await import('@/stores')

const approval = {
  approval_id: 'ap-1',
  content: 'SELECT 1',
  content_type: 'sql',
  title: 'Confirmación',
  created_at: '2026-10-03T12:00:00Z',
  turno_id: 't-1',
}

beforeEach(() => {
  submit.mockReset()
  submit.mockResolvedValue({ success: true })
  useUIStore.setState({ pendingApprovals: [approval], showApprovalPanel: true } as never)
  useSessionStore.setState({ sessionId: 's1' } as never)
  useChatStore.setState({
    messages: [], chatStatus: 'waiting_approval', isLoading: false, turnoRemoto: null, aprobacionesSinTurno: [],
    turnosTerminados: [], mensajeDeTurno: {}, turnosCortados: [],
  } as never)
})

afterEach(() => {
  useUIStore.setState({ pendingApprovals: [], showApprovalPanel: false } as never)
})

describe('ApprovalPanel — turno sin petición HTTP en esta pestaña', () => {
  it('aprobar tras recargar sigue el turno por su id: en vuelo hasta su resultado', async () => {
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Aprobar y ejecutar'))
    await waitFor(() => expect(useChatStore.getState().turnoRemoto).toEqual({ id: 't-1', bloquea: true }))
    expect(useChatStore.getState().isLoading).toBe(true)
    expect(useChatStore.getState().chatStatus).toBe('searching')
  })

  it('si el servidor ya dijo que no retoma nada, el chip vuelve a «Listo»', async () => {
    useChatStore.setState({ aprobacionesSinTurno: ['ap-1'] } as never)
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Rechazar'))
    await waitFor(() => expect(submit).toHaveBeenCalled())
    await waitFor(() => expect(useChatStore.getState().chatStatus).toBe('ready'))
    expect(useChatStore.getState().isLoading).toBe(false)
    expect(useChatStore.getState().turnoRemoto).toBeNull()
  })

  it('con la consulta HTTP de esta pestaña viva, el chip es suyo (no se sigue nada aparte)', async () => {
    useChatStore.setState({ isLoading: true } as never)
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Aprobar y ejecutar'))
    await waitFor(() => expect(useChatStore.getState().chatStatus).toBe('searching'))
    expect(useChatStore.getState().turnoRemoto).toBeNull()
  })

  it.each([['Aprobar y ejecutar'], ['Rechazar']])(
    'si el resultado del turno llega por WS antes que la respuesta del POST (%s), nada queda en vuelo',
    async (boton) => {
      let responder: (v: unknown) => void = () => {}
      submit.mockReturnValue(new Promise((r) => { responder = r }))
      render(<ApprovalPanel />)
      fireEvent.click(screen.getByText(boton))
      await waitFor(() => expect(submit).toHaveBeenCalled())
      // el turno termina (un rechazo es terminal) y su resultado se adelanta al 200 del POST
      manejarAvisoDeTurno('result', {
        entrega: 'ws', turno_id: 't-1', consulta: 'trae los lotes', respuesta: respuesta({ message: 'Hecho.' }),
      })
      responder({ success: true })
      await waitFor(() => expect(useUIStore.getState().showApprovalPanel).toBe(false))
      expect(useChatStore.getState().isLoading).toBe(false)
      expect(useChatStore.getState().chatStatus).toBe('ready')
      expect(useChatStore.getState().turnoRemoto).toBeNull()
    },
  )
})
