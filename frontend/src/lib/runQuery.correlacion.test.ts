/**
 * F7 (auditoría, segunda pasada) — lo que llega por WebSocket va al mensaje de SU turno.
 *
 * El texto de la consulta no identifica un turno (tras un «Bad Gateway» el usuario reenvía la
 * misma): el `processing` del servidor trae el `turno_id` y liga el mensaje de la consulta HTTP en
 * vuelo a ese turno. Con eso: «Detener» (o un 504) ya no muestra el desenlace dos veces cuando el
 * turno termina igual y llega por el WebSocket; el aviso de reinicio reescribe el error de SU
 * turno; y un turno retomado no deja la entrada bloqueada sin salida.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const process = vi.fn()
vi.mock('@/services/api', () => ({
  queryApi: { process: (...args: unknown[]) => process(...args) },
}))

const { manejarAvisoDeTurno, runQuery, resetActiveQuery, abortActiveQuery } = await import('./runQuery')
const { useChatStore, useMapStore, useSessionStore, useUIStore } = await import('@/stores')
const { useResultsStore } = await import('@/stores/resultsStore')
const { useOperaciones } = await import('@/lib/operaciones')
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

/** El servidor anuncia por WS el turno que atiende la consulta HTTP en vuelo. */
const procesando = (turnoId: string) => manejarAvisoDeTurno('status', { status: 'processing', turno_id: turnoId })

/** Una consulta HTTP que se queda colgada hasta que se aborta («Detener»). */
const colgadaHastaAbortar = (_body: unknown, signal: AbortSignal) =>
  new Promise((_r, rechazar) => {
    signal.addEventListener('abort', () => rechazar(new DOMException('aborted', 'AbortError')))
  })

const asistentes = () => chat().messages.filter((m) => m.role === 'assistant')

describe('«Detener» o un corte de la petición HTTP: el desenlace se muestra una vez', () => {
  it('Detener y el «Consulta detenida.» que llega por WS van al MISMO mensaje, sin duplicar el historial', async () => {
    process.mockImplementation(colgadaHastaAbortar)
    const enVuelo = runQuery('trae los lotes')
    await vi.waitFor(() => expect(process).toHaveBeenCalled())
    expect(procesando('t-9')).toBe(false)  // el pipeline de agentes también lo usa
    abortActiveQuery()
    await enVuelo
    expect(asistentes().map((m) => m.content)).toEqual(['Consulta cancelada.'])

    // el servidor canceló el turno y, como la petición HTTP ya no estaba, lo entrega por el WS
    expect(manejarAvisoDeTurno('result', {
      entrega: 'ws', turno_id: 't-9', consulta: 'trae los lotes',
      respuesta: respuesta({ message: 'Consulta detenida.', status: 'failed' }),
    })).toBe(true)
    expect(asistentes().map((m) => m.content)).toEqual(['Consulta detenida.'])
    expect(chat().queryHistory).toHaveLength(0)  // como una cancelada: no se apunta (antes, sí, aparte)
    expect(chat().isLoading).toBe(false)
    expect(chat().chatStatus).toBe('ready')
  })

  it('tras un 504 del proxy, el resultado que llega por WS reemplaza el error de SU mensaje', async () => {
    process.mockImplementation(async () => {
      procesando('t-5')
      throw new Error('Gateway Timeout')
    })
    await runQuery('cuenta los ríos')
    expect(asistentes()[0]).toMatchObject({ content: 'Error: Gateway Timeout', status: 'error' })

    manejarAvisoDeTurno('result', {
      entrega: 'ws', turno_id: 't-5', consulta: 'cuenta los ríos', respuesta: respuesta({ message: 'Hay 12 ríos.' }),
    })
    expect(asistentes()).toHaveLength(1)
    expect(asistentes()[0]).toMatchObject({ content: 'Hay 12 ríos.', status: 'sent' })
    expect(chat().queryHistory).toHaveLength(1)  // el turno ya estaba apuntado
    expect(chat().chatStatus).toBe('ready')
  })

  it('el resultado de un turno que NO es de esta pestaña sigue llegando como respuesta aparte', () => {
    manejarAvisoDeTurno('result', {
      entrega: 'ws', turno_id: 't-x', consulta: 'trae los lotes', respuesta: respuesta({ message: 'Lotes.' }),
    })
    expect(asistentes().map((m) => m.content)).toEqual(['Lotes.'])
  })
})

describe('el aviso de reinicio va al mensaje de SU turno aunque el texto se repita', () => {
  it('reenviar la misma consulta tras el «Bad Gateway» no hace reescribir el error real del reenvío', async () => {
    process.mockImplementationOnce(async () => {
      procesando('t1')
      throw new Error('Bad Gateway')
    })
    await runQuery('trae los lotes')
    process.mockImplementationOnce(async () => {
      procesando('t2')
      throw new Error('Cuota del proveedor agotada')
    })
    await runQuery('trae los lotes')
    const [a1, a2] = asistentes()

    manejarAvisoDeTurno('status', {
      status: 'reanudacion', reanudada: true, turno_id: 't1', consulta: 'trae los lotes', message: 'retomo tu consulta',
    })
    const porId = Object.fromEntries(chat().messages.map((m) => [m.id, m]))
    expect(porId[a1.id]).toMatchObject({ status: 'sent' })
    expect(porId[a1.id].content).toMatch(/Se interrumpió/)
    expect(porId[a2.id]).toMatchObject({ content: 'Error: Cuota del proveedor agotada', status: 'error' })
  })

  it('una respuesta en el mapa (FH.9, el chat muestra la etiqueta) también se encuentra por su turno', async () => {
    process.mockImplementationOnce(async () => {
      procesando('t3')
      throw new Error('Bad Gateway')
    })
    await runQuery('marca el punto', { etiqueta: '📍 Punto marcado', respuestaMapa: true } as never)
    manejarAvisoDeTurno('status', {
      status: 'reanudacion', reanudada: false, approval_id: 'ap-3', turno_id: 't3', consulta: 'marca el punto',
      message: 'La rechazaste',
    })
    expect(asistentes()[0].content).toMatch(/Se interrumpió/)
  })

  it('sin vínculo (se recargó la pestaña) y con dos candidatos del mismo texto, no toca nada', () => {
    useChatStore.setState({
      messages: [
        { id: 'u1', role: 'user', content: 'trae los lotes', timestamp: new Date(), status: 'sent' },
        { id: 'a1', role: 'assistant', content: 'Error: Bad Gateway', timestamp: new Date(), status: 'error' },
        { id: 'u2', role: 'user', content: 'trae los lotes', timestamp: new Date(), status: 'sent' },
        { id: 'a2', role: 'assistant', content: 'Error: 429', timestamp: new Date(), status: 'error' },
      ],
    } as never)
    manejarAvisoDeTurno('status', {
      status: 'reanudacion', reanudada: true, turno_id: 't-desconocido', consulta: 'trae los lotes', message: 'x',
    })
    expect(chat().messages.filter((m) => m.status === 'error').map((m) => m.id)).toEqual(['a1', 'a2'])
  })
})

describe('el turno retomado nunca deja la entrada bloqueada sin salida', () => {
  it('«Detener» sin consulta HTTP suelta el turno remoto y lo dice', () => {
    manejarAvisoDeTurno('status', { status: 'reanudacion', reanudada: true, turno_id: 't1', message: 'retomo' })
    expect(chat().isLoading).toBe(true)
    abortActiveQuery()
    expect(chat().isLoading).toBe(false)
    expect(chat().turnoRemoto).toBeNull()
    expect(chat().chatStatus).toBe('ready')
    expect(chat().messages.slice(-1)[0].content).toMatch(/Dejé de esperar/)
    // si termina después, su resultado aparece igual
    manejarAvisoDeTurno('result', {
      entrega: 'ws', reanudada: true, turno_id: 't1', consulta: 'q', respuesta: respuesta({ message: 'Llegó.' }),
    })
    expect(chat().messages.slice(-1)[0].content).toBe('Llegó.')
  })

  it('si la consulta HTTP en vuelo termina antes que el turno retomado, el chip y la entrada pasan a él', async () => {
    let soltar: (v: unknown) => void = () => {}
    process.mockReturnValue(new Promise((r) => { soltar = r }))
    const enVuelo = runQuery('cuenta los ríos')
    await vi.waitFor(() => expect(process).toHaveBeenCalled())
    manejarAvisoDeTurno('status', { status: 'reanudacion', reanudada: true, turno_id: 'tR', message: 'retomo' })
    expect(chat().turnoRemoto).toEqual({ id: 'tR', bloquea: false })
    soltar(respuesta({ message: 'Ríos.' }))
    await enVuelo
    expect(chat().isLoading).toBe(true)  // otra consulta no se solapa con el turno retomado
    expect(chat().chatStatus).toBe('searching')
    expect(chat().turnoRemoto).toEqual({ id: 'tR', bloquea: true })
    manejarAvisoDeTurno('result', {
      entrega: 'ws', reanudada: true, turno_id: 'tR', consulta: 'q', respuesta: respuesta({ message: 'Retomada.' }),
    })
    expect(chat().isLoading).toBe(false)
    expect(chat().chatStatus).toBe('ready')
  })

  it('el resultado de un turno remoto no corta el registro de operaciones de la consulta en vuelo', async () => {
    const marcarTurno = vi.fn()
    const original = useOperaciones.getState().marcarTurno
    let soltar: (v: unknown) => void = () => {}
    process.mockReturnValue(new Promise((r) => { soltar = r }))
    const enVuelo = runQuery('cuenta los ríos')
    await vi.waitFor(() => expect(process).toHaveBeenCalled())
    useOperaciones.setState({ marcarTurno } as never)
    try {
      manejarAvisoDeTurno('result', {
        entrega: 'ws', turno_id: 'tA', consulta: 'trae los lotes', respuesta: respuesta({ message: 'Lotes.' }),
      })
      expect(marcarTurno).not.toHaveBeenCalled()  // a mitad de la consulta en vuelo
      soltar(respuesta({ message: 'Ríos.' }))
      await enVuelo
      expect(marcarTurno).toHaveBeenCalledTimes(1)  // el de su propia respuesta
    } finally {
      useOperaciones.setState({ marcarTurno: original } as never)
    }
  })

  it('el resultado de OTRO turno no suelta el turno remoto que se sigue', () => {
    manejarAvisoDeTurno('status', { status: 'reanudacion', reanudada: true, turno_id: 't1', message: 'retomo' })
    manejarAvisoDeTurno('result', {
      entrega: 'ws', turno_id: 't-otro', consulta: 'x', respuesta: respuesta({ message: 'Otro.' }),
    })
    expect(chat().isLoading).toBe(true)
    expect(chat().turnoRemoto).toEqual({ id: 't1', bloquea: true })
  })
})
