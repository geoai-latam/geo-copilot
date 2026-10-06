/**
 * F7 (auditoría) — la pestaña no deja de reconectar.
 *
 * Con 5 intentos (≈31 s) un despliegue con migraciones dejaba la pestaña desconectada para
 * siempre: la aprobación pendiente no reaparecía y el resultado del turno retomado, que solo llega
 * por el WebSocket, se perdía. Ahora el intervalo tiene tope y los reintentos siguen; al volver la
 * red se reintenta en el acto.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

class SocketQueFalla {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static abiertos = 0
  static fallar = true

  readyState = SocketQueFalla.CONNECTING
  onopen: (() => void) | null = null
  onclose: ((e: { code: number; reason: string; wasClean: boolean }) => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: ((e: unknown) => void) | null = null

  static ultimos: SocketQueFalla[] = []

  constructor(public url: string) {
    SocketQueFalla.abiertos++
    SocketQueFalla.ultimos.push(this)
    setTimeout(() => {
      if (SocketQueFalla.fallar) {
        this.readyState = SocketQueFalla.CLOSED
        this.onclose?.({ code: 1006, reason: '', wasClean: false })
      } else {
        this.readyState = SocketQueFalla.OPEN
        this.onopen?.()
      }
    }, 0)
  }

  send = vi.fn()
  close = vi.fn(() => {
    this.readyState = SocketQueFalla.CLOSED
  })
}

const original = globalThis.WebSocket
const fetchOriginal = globalThis.fetch

beforeEach(() => {
  globalThis.WebSocket = SocketQueFalla as unknown as typeof WebSocket
  globalThis.fetch = vi.fn().mockRejectedValue(new Error('sin red')) as unknown as typeof fetch
  SocketQueFalla.abiertos = 0
  SocketQueFalla.ultimos = []
  SocketQueFalla.fallar = true
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  globalThis.WebSocket = original
  globalThis.fetch = fetchOriginal
})

const { wsService } = await import('./websocket')

describe('reconexión sin tope de intentos', () => {
  it('sigue reintentando pasados los 31 s, con el intervalo acotado a 30 s', async () => {
    const delays: number[] = []
    const setTimeoutReal = globalThis.setTimeout
    vi.spyOn(globalThis, 'setTimeout').mockImplementation(((fn: () => void, ms?: number) => {
      if (ms && ms >= 1000) delays.push(ms)
      return setTimeoutReal(fn, ms)
    }) as typeof setTimeout)

    wsService.connect('s-larga')
    await vi.advanceTimersByTimeAsync(3 * 60_000)  // tres minutos de servidor caído
    expect(SocketQueFalla.abiertos).toBeGreaterThan(6)  // antes: 1 + 5 y se rendía
    expect(Math.max(...delays)).toBe(30_000)

    SocketQueFalla.fallar = false  // vuelve el servidor
    await vi.advanceTimersByTimeAsync(31_000)
    expect(wsService.isConnected).toBe(true)
    wsService.disconnect()
  })

  it('al volver la red reintenta en el acto, sin esperar al intervalo', async () => {
    wsService.connect('s-red')
    await vi.advanceTimersByTimeAsync(20_000)  // varios fallos: el siguiente intento tardaría
    const antes = SocketQueFalla.abiertos
    SocketQueFalla.fallar = false
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(5)
    expect(SocketQueFalla.abiertos).toBe(antes + 1)
    expect(wsService.isConnected).toBe(true)
    wsService.disconnect()
  })

  it('al volver la pestaña a primer plano también reintenta en el acto', async () => {
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    wsService.connect('s-vista')
    await vi.advanceTimersByTimeAsync(20_000)
    const antes = SocketQueFalla.abiertos
    SocketQueFalla.fallar = false
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(5)
    expect(SocketQueFalla.abiertos).toBe(antes + 1)
    expect(wsService.isConnected).toBe(true)
    wsService.disconnect()
  })
})

describe('una sola reconexión a la vez (F7, auditoría)', () => {
  // con la verificación de la sesión (un fetch) entre medias, que es donde se abría el segundo socket
  const verificacionLenta = async (id: string) => {
    await new Promise((r) => setTimeout(r, 50))
    return id
  }

  beforeEach(() => {
    // el ticket tarda lo que tarda una petición de verdad: ahí también se abría el segundo socket
    globalThis.fetch = vi.fn(() => new Promise((_r, rechazar) => {
      setTimeout(() => rechazar(new Error('sin red')), 30)
    })) as unknown as typeof fetch
  })

  afterEach(() => {
    wsService.setSessionRecovery(null)
  })

  it('«online» y «visibilitychange» seguidos (despertar el portátil) abren UN socket', async () => {
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    wsService.setSessionRecovery(verificacionLenta)
    wsService.connect('s-despierta')
    await vi.advanceTimersByTimeAsync(20_000)
    const antes = SocketQueFalla.abiertos
    SocketQueFalla.fallar = false
    window.dispatchEvent(new Event('online'))
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(200)
    expect(SocketQueFalla.abiertos).toBe(antes + 1)
    expect(wsService.isConnected).toBe(true)
    wsService.disconnect()
  })

  it('un «online» mientras el temporizador ya está reintentando no abre otro socket', async () => {
    wsService.setSessionRecovery(verificacionLenta)
    wsService.connect('s-timer')
    await vi.advanceTimersByTimeAsync(1_010)  // el 1.er reintento está verificando la sesión
    const antes = SocketQueFalla.abiertos
    SocketQueFalla.fallar = false
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(200)
    expect(SocketQueFalla.abiertos).toBe(antes + 1)
    expect(wsService.isConnected).toBe(true)
    wsService.disconnect()
  })

  it('el cierre de un socket reemplazado no marca «desconectado» con el nuevo abierto', async () => {
    SocketQueFalla.fallar = false
    const desconexiones = vi.fn()
    const off = wsService.onDisconnect(desconexiones)
    try {
      wsService.connect('s-a')
      await vi.advanceTimersByTimeAsync(100)
      wsService.connect('s-b')  // cambio de sesión: el de s-a se suelta
      await vi.advanceTimersByTimeAsync(100)
      desconexiones.mockClear()
      const viejo = (SocketQueFalla.ultimos[0] as SocketQueFalla)
      viejo.onclose?.({ code: 1000, reason: '', wasClean: true })  // su cierre llega tarde
      expect(desconexiones).not.toHaveBeenCalled()
      expect(wsService.isConnected).toBe(true)
    } finally {
      off()
      wsService.disconnect()
    }
  })
})
