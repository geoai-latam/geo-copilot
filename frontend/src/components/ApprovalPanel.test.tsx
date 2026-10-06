/**
 * UI-HITL-CLOSE — cerrar el panel = rechazar (no solo ocultar) + errores en UI.
 *
 * Antes: X / Cancelar / backdrop solo hacían setShowApprovalPanel(false) → el
 * backend quedaba suspendido hasta el timeout. Y los errores de submit se
 * tragaban con logger.error → la app parecía colgada.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const submit = vi.fn()
vi.mock('@/services/api', () => ({
  approvalApi: { submit: (...args: unknown[]) => submit(...args) },
}))

const { ApprovalPanel } = await import('./ApprovalPanel')
const { useUIStore, useSessionStore, useChatStore } = await import('@/stores')

// Fixture con los campos que el backend SÍ emite (security/hitl.py:340-354).
// Deliberadamente sin `impact_estimate` ni `sandbox`: así es como llega hoy.
const approval = {
  approval_id: 'ap-1',
  content: 'SELECT 1',
  content_type: 'sql',
  title: 'Confirmación',
  created_at: '2026-09-08T12:00:00Z',
}

beforeEach(() => {
  submit.mockReset()
  useUIStore.setState({ pendingApprovals: [approval], showApprovalPanel: true } as never)
  useSessionStore.setState({ sessionId: 's1' } as never)
  useChatStore.setState({ messages: [], chatStatus: 'ready' } as never)
})

afterEach(() => {
  useUIStore.setState({ pendingApprovals: [], showApprovalPanel: false } as never)
})

describe('ApprovalPanel — cerrar = rechazar', () => {
  it('el botón "Cancelar" RECHAZA en el backend (no solo oculta)', async () => {
    submit.mockResolvedValue({ success: true })
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Cancelar'))
    await waitFor(() => expect(submit).toHaveBeenCalled())
    expect(submit).toHaveBeenCalledWith(
      'ap-1',
      expect.objectContaining({ action: 'reject', session_id: 's1' }),
    )
  })

  it('la X del header también rechaza', async () => {
    submit.mockResolvedValue({ success: true })
    const { container } = render(<ApprovalPanel />)
    const closeBtn = container.querySelector('.icon-btn.close') as HTMLButtonElement
    fireEvent.click(closeBtn)
    await waitFor(() => expect(submit).toHaveBeenCalledWith(
      'ap-1', expect.objectContaining({ action: 'reject' }),
    ))
  })

  it('un error de submit se muestra en el chat (no se traga)', async () => {
    submit.mockRejectedValue(new Error('network'))
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Cancelar'))
    await waitFor(() => {
      const msgs = (useChatStore.getState() as { messages: Array<{ content: string }> }).messages
      expect(msgs.some((m) => /no se pudo rechazar/i.test(m.content))).toBe(true)
    })
  })
})

/**
 * Auditoría 2026-09-08 §5 (1) — el panel no afirma lo que nadie le dijo.
 *
 * El backend (`security/hitl.py:340-354`) manda approval_id, content_type,
 * content, content_sha256, preview, warnings, risk_level, title, description,
 * action_type y created_at. NO manda `impact_estimate` ni `sandbox`. El panel
 * pintaba igual las tres celdas de impacto (tres "—" fijos) y escribía
 * `Sandbox gis_readonly` por defecto — un confinamiento afirmado sin respaldo
 * en la única pantalla donde el usuario decide si algo se ejecuta.
 */
describe('ApprovalPanel — no inventa datos que el backend no manda', () => {
  it('sin impact_estimate NO pinta el grid: declara que no llegó estimación', () => {
    const { container } = render(<ApprovalPanel />)
    expect(container.querySelector('.impact-grid')).toBeNull()
    expect(container.querySelector('.impact-missing')).not.toBeNull()
    expect(screen.getByText(/no envió estimación de impacto/i)).toBeTruthy()
    // Y no queda ni un guion suelto haciéndose pasar por un cálculo vacío.
    expect(screen.queryByText('—')).toBeNull()
  })

  it('con impact_estimate parcial pinta SOLO las celdas con dato', () => {
    useUIStore.setState({
      pendingApprovals: [{ ...approval, impact_estimate: { rows: 1200 } }],
    } as never)
    const { container } = render(<ApprovalPanel />)
    const cells = container.querySelectorAll('.impact-grid .cell')
    expect(cells).toHaveLength(1)
    expect(screen.getByText('Filas estimadas')).toBeTruthy()
    expect(screen.queryByText('Tiempo estimado')).toBeNull()
  })

  it('NO afirma "gis_readonly" cuando el backend no manda `sandbox`', () => {
    render(<ApprovalPanel />)
    expect(screen.queryByText('gis_readonly')).toBeNull()
    expect(screen.getByText(/sandbox no declarado por el servidor/i)).toBeTruthy()
  })

  it('nombra el sandbox cuando el backend SÍ lo manda', () => {
    useUIStore.setState({
      pendingApprovals: [{ ...approval, sandbox: 'gis_readonly' }],
    } as never)
    render(<ApprovalPanel />)
    expect(screen.getByText('gis_readonly')).toBeTruthy()
    expect(screen.queryByText(/sandbox no declarado/i)).toBeNull()
  })

  it('un `content_type` degenerado (null) no tumba el panel', () => {
    // websocket.py:559-572 puede emitir content_type: null. Antes esto era
    // `null.toUpperCase()` → excepción → pantalla en blanco.
    useUIStore.setState({
      pendingApprovals: [
        { approval_id: 'ap-2', content: 'SELECT 1', content_type: null, title: 'Confirmación' },
      ],
    } as never)
    expect(() => render(<ApprovalPanel />)).not.toThrow()
    expect(screen.getByText('una acción')).toBeTruthy()
  })
})

/**
 * Auditoría 2026-09-08 §5 (2) — el chip del chat sigue la aprobación.
 */
describe('ApprovalPanel — mueve el estado del chat', () => {
  it('resuelta la última aprobación, la espera vuelve a ser del servidor', async () => {
    submit.mockResolvedValue({ success: true })
    useChatStore.setState({ chatStatus: 'waiting_approval' } as never)
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Aprobar y ejecutar'))
    await waitFor(() =>
      expect(useChatStore.getState().chatStatus).toBe('searching'),
    )
  })

  it('con más solicitudes en cola el chip sigue esperando al usuario', async () => {
    submit.mockResolvedValue({ success: true })
    useUIStore.setState({
      pendingApprovals: [approval, { ...approval, approval_id: 'ap-9' }],
    } as never)
    useChatStore.setState({ chatStatus: 'waiting_approval' } as never)
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Aprobar y ejecutar'))
    await waitFor(() => expect(submit).toHaveBeenCalled())
    expect(useChatStore.getState().chatStatus).toBe('waiting_approval')
  })

  it('un fallo de submit deja el chip en error', async () => {
    submit.mockRejectedValue(new Error('network'))
    useChatStore.setState({ chatStatus: 'waiting_approval' } as never)
    render(<ApprovalPanel />)
    fireEvent.click(screen.getByText('Rechazar'))
    await waitFor(() => expect(useChatStore.getState().chatStatus).toBe('error'))
  })
})
