/**
 * F7 (auditoría) — turnos sin petición HTTP de esta pestaña.
 *
 * Un turno reanudado tras un reinicio, o aprobado tras recargar, entrega su resultado por el
 * WebSocket. Antes: el aviso de reanudación reescribía el ÚLTIMO error del chat (fuera del turno
 * que fuera), dos avisos en el mismo milisegundo compartían id, el turno no bloqueaba otra
 * consulta ni mostraba «Detener», su resultado pisaba el chip de otra consulta en vuelo, y si el
 * resultado se perdía en una reconexión el chip quedaba en «Consultando…» para siempre.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const process = vi.fn()
vi.mock('@/services/api', () => ({
  queryApi: { process: (...args: unknown[]) => process(...args) },
}))

const { manejarAvisoDeTurno, runQuery, resetActiveQuery } = await import('./runQuery')
const { useChatStore, useMapStore, useSessionStore, useUIStore } = await import('@/stores')
const { useResultsStore } = await import('@/stores/resultsStore')
const { respuesta } = await import('@/contracts/fixtures')

const chat = () => useChatStore.getState()

beforeEach(() => {
  process.mockReset()
  resetActiveQuery()
  useChatStore.setState({
    messages: [], isLoading: false, chatStatus: 'ready', queryHistory: [],
    turnoRemoto: null, aprobacionesSinTurno: [], mensajeDeTurno: {}, turnosCortados: [], turnosTerminados: [],
  } as never)
  useMapStore.setState({ layers: [] } as never)
  useUIStore.setState({ showApprovalPanel: false } as never)
  useResultsStore.getState().limpiar()
  useSessionStore.setState({ sessionId: 's1' } as never)
})

afterEach(() => {
  vi.restoreAllMocks()
})

const reanudacion = (extra: Record<string, unknown>) =>
  manejarAvisoDeTurno('status', { status: 'reanudacion', message: 'aviso', ...extra })

describe('turno retomado: en vuelo hasta que llega su resultado', () => {
  it('bloquea otra consulta y muestra «Detener» (isLoading) hasta su resultado', () => {
    expect(reanudacion({ reanudada: true, turno_id: 't1', consulta: 'trae los lotes' })).toBe(true)
    expect(chat().isLoading).toBe(true)
    expect(chat().chatStatus).toBe('searching')
    expect(chat().turnoRemoto).toEqual({ id: 't1', bloquea: true })

    expect(manejarAvisoDeTurno('result', {
      entrega: 'ws', reanudada: true, turno_id: 't1', consulta: 'trae los lotes',
      respuesta: respuesta({ message: 'Traje los 4 lotes.' }),
    })).toBe(true)
    expect(chat().messages.slice(-1)[0]?.content).toBe('Traje los 4 lotes.')
    expect(chat().isLoading).toBe(false)
    expect(chat().chatStatus).toBe('ready')
    expect(chat().turnoRemoto).toBeNull()
  })

  it('un error del turno retomado lo dice y suelta el chip', () => {
    reanudacion({ reanudada: true, turno_id: 't1' })
    manejarAvisoDeTurno('result', { entrega: 'ws', reanudada: true, turno_id: 't1', error: 'No se pudo retomar la consulta.' })
    expect(chat().messages.slice(-1)[0]).toMatchObject({ content: 'No se pudo retomar la consulta.', status: 'error' })
    expect(chat().isLoading).toBe(false)
    expect(chat().chatStatus).toBe('error')
  })

  it('el resultado de un turno remoto no pisa el chip de otra consulta HTTP en vuelo', async () => {
    let soltar: (v: unknown) => void = () => {}
    process.mockReturnValue(new Promise((r) => { soltar = r }))
    const enVuelo = runQuery('cuenta los ríos')
    await vi.waitFor(() => expect(process).toHaveBeenCalled())
    manejarAvisoDeTurno('result', {
      entrega: 'ws', turno_id: 'tA', consulta: 'trae los lotes', respuesta: respuesta({ message: 'Lotes.' }),
    })
    expect(chat().isLoading).toBe(true)  // la consulta B sigue en vuelo
    expect(chat().chatStatus).toBe('searching')
    soltar(respuesta({ message: 'Ríos.' }))
    await enVuelo
    expect(chat().chatStatus).toBe('ready')
    expect(chat().messages.map((m) => m.content)).toContain('Lotes.')
  })

  it('al reconectar, si el turno que se seguía ya no está en curso, se dice (no queda «Consultando…»)', () => {
    reanudacion({ reanudada: true, turno_id: 't1' })
    expect(manejarAvisoDeTurno('status', { status: 'connected', turnos_en_curso: ['t1'] })).toBe(true)
    expect(chat().isLoading).toBe(true)  // sigue: nada que decir
    manejarAvisoDeTurno('status', { status: 'connected', turnos_en_curso: [] })
    expect(chat().isLoading).toBe(false)
    expect(chat().chatStatus).toBe('error')
    expect(chat().messages.slice(-1)[0]?.content).toMatch(/su resultado no llegó/)
  })

  it('el «result» que solo cierra el pipeline de una consulta HTTP viva no se toca', () => {
    expect(manejarAvisoDeTurno('result', { success: true })).toBe(false)
    expect(chat().messages).toHaveLength(0)
  })
})

describe('el aviso de reanudación va al mensaje de SU turno', () => {
  const sembrar = () => useChatStore.setState({
    messages: [
      { id: 'u1', role: 'user', content: 'trae los lotes', timestamp: new Date(), status: 'sent' },
      { id: 'a1', role: 'assistant', content: 'Error: Bad Gateway', timestamp: new Date(), status: 'error' },
      { id: 'u2', role: 'user', content: 'cuenta los ríos', timestamp: new Date(), status: 'sent' },
      { id: 'a2', role: 'assistant', content: 'Error: Bad Gateway', timestamp: new Date(), status: 'error' },
    ],
  } as never)

  it('reescribe el error del turno interrumpido, no el último error del chat', () => {
    sembrar()
    reanudacion({ reanudada: true, turno_id: 't1', consulta: 'trae los lotes' })
    const porId = Object.fromEntries(chat().messages.map((m) => [m.id, m]))
    expect(porId.a1.content).toMatch(/Se interrumpió/)
    expect(porId.a1.status).toBe('sent')
    expect(porId.a2.content).toBe('Error: Bad Gateway')  // el fallo real de la otra consulta queda
    expect(porId.a2.status).toBe('error')
  })

  it('si no encuentra el turno, no toca nada', () => {
    sembrar()
    reanudacion({ reanudada: false, approval_id: 'ap-1', turno_id: 't9', consulta: 'otra cosa' })
    expect(chat().messages.filter((m) => m.status === 'error').map((m) => m.id)).toEqual(['a1', 'a2'])
  })

  it('dos avisos en el mismo milisegundo tienen ids distintos', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
    reanudacion({ reanudada: true, turno_id: 't1' })
    manejarAvisoDeTurno('result', { entrega: 'ws', reanudada: true, turno_id: 't1', error: 'x' })
    const ids = chat().messages.map((m) => m.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('la aprobación que no retoma nada', () => {
  it('deja el chip en «Listo» y lo recuerda para el panel', () => {
    useChatStore.setState({ turnoRemoto: { id: 't1', bloquea: true }, isLoading: true, chatStatus: 'searching' } as never)
    reanudacion({ reanudada: false, approval_id: 'ap-1', turno_id: 't1' })
    expect(chat().aprobacionesSinTurno).toContain('ap-1')
    expect(chat().isLoading).toBe(false)
    expect(chat().chatStatus).toBe('ready')
  })
})

describe('V5 F7: recargar con una aprobación pendiente', () => {
  it('el resultado por WS va al mensaje que quedó esperando ese turno tras recargar', async () => {
    // lo que se restaura de sessionStorage tras recargar: el mensaje guardó el turno que seguía
    useChatStore.setState({
      messages: [
        { id: 'u1', role: 'user', content: 'trae los lotes', timestamp: new Date(), status: 'sent' },
        { id: 'a1', role: 'assistant', content: 'La consulta sigue en el servidor; su resultado aparecerá aquí.',
          timestamp: new Date(), status: 'sent', turnoPendiente: 't9' },
      ],
    } as never)
    manejarAvisoDeTurno('result', {
      entrega: 'ws', turno_id: 't9', consulta: 'trae los lotes', respuesta: respuesta({ message: 'Traje los 4 lotes.' }),
    })
    expect(chat().messages).toHaveLength(2)                       // en SU mensaje, no uno nuevo
    expect(chat().messages[1]).toMatchObject({ id: 'a1', content: 'Traje los 4 lotes.' })
    expect(chat().messages[1].turnoPendiente).toBeUndefined()
  })

  it('la copia por WS del turno de una consulta HTTP viva de esta pestaña no se pinta dos veces', async () => {
    let soltar: (v: unknown) => void = () => {}
    process.mockReturnValue(new Promise((r) => { soltar = r }))
    const enVuelo = runQuery('trae los lotes')
    await vi.waitFor(() => expect(process).toHaveBeenCalled())
    manejarAvisoDeTurno('status', { status: 'processing', turno_id: 'tX' })  // su turno
    expect(manejarAvisoDeTurno('result', {
      entrega: 'ws', turno_id: 'tX', consulta: 'trae los lotes', respuesta: respuesta({ message: 'Lotes (WS).' }),
    })).toBe(true)
    soltar(respuesta({ message: 'Lotes (HTTP).' }))
    await enVuelo
    const asistente = chat().messages.filter((m) => m.role === 'assistant').map((m) => m.content)
    expect(asistente).toEqual(['Lotes (HTTP).'])
  })
})
