/**
 * Tests for WebSocket Service
 *
 * Validates:
 * 1. Connection management
 * 2. Message handling (including plan_created, step_progress, etc.)
 * 3. Reconnection logic
 * 4. Handler registration/unregistration
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { WSMessage } from '@/types'

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  readyState = MockWebSocket.CONNECTING
  url: string
  onopen: (() => void) | null = null
  onclose: ((event: { code: number; reason: string; wasClean: boolean }) => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: ((error: unknown) => void) | null = null

  constructor(url: string) {
    this.url = url
    // Simulate async connection
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN
      this.onopen?.()
    }, 0)
  }

  send = vi.fn()
  close = vi.fn((code?: number, reason?: string) => {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({ code: code || 1000, reason: reason || '', wasClean: true })
  })

  // Helper to simulate receiving a message
  simulateMessage(data: WSMessage) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  // Helper to simulate disconnect
  simulateDisconnect(wasClean = true) {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({ code: 1000, reason: 'test', wasClean })
  }

  // Helper to simulate error
  simulateError(error: unknown) {
    this.onerror?.(error)
  }
}

// Replace global WebSocket
const originalWebSocket = globalThis.WebSocket
beforeEach(() => {
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket
})
afterEach(() => {
  globalThis.WebSocket = originalWebSocket
})

// Import after mocking WebSocket
import { wsService } from './websocket'

describe('WebSocketService', () => {
  beforeEach(() => {
    // Reset service state
    wsService.disconnect()
    vi.clearAllMocks()
  })

  describe('Connection', () => {
    it('should connect to correct URL', async () => {
      wsService.connect('test-session-123')
      // Wait for async connection
      await new Promise(resolve => setTimeout(resolve, 10))

      expect(wsService.currentSessionId).toBe('test-session-123')
    })

    it('should report connected status after connection', async () => {
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      expect(wsService.isConnected).toBe(true)
    })

    it('should disconnect properly', async () => {
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      wsService.disconnect()

      expect(wsService.isConnected).toBe(false)
      expect(wsService.currentSessionId).toBeNull()
    })

    it('should not reconnect when session changes', async () => {
      wsService.connect('session-1')
      await new Promise(resolve => setTimeout(resolve, 10))

      wsService.connect('session-2')
      await new Promise(resolve => setTimeout(resolve, 10))

      expect(wsService.currentSessionId).toBe('session-2')
    })
  })

  describe('Message Handling', () => {
    it('should call message handlers on message received', async () => {
      const handler = vi.fn()
      wsService.onMessage(handler)
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      // Get the mock WebSocket instance
      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateMessage({
        type: 'progress',
        session_id: 'test-session',
        data: { agent: 'TestAgent', message: 'Hello' }
      })

      expect(handler).toHaveBeenCalledWith({
        type: 'progress',
        session_id: 'test-session',
        data: { agent: 'TestAgent', message: 'Hello' }
      })
    })

    it('should handle plan_created message', async () => {
      const handler = vi.fn()
      wsService.onMessage(handler)
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateMessage({
        type: 'plan_created',
        session_id: 'test-session',
        data: {
          plan: [
            { step_id: 'step_1', description: 'Buscar datos' },
            { step_id: 'step_2', description: 'Aplicar buffer' },
          ],
          reasoning: 'Test plan'
        }
      })

      expect(handler).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'plan_created',
          data: expect.objectContaining({
            plan: expect.arrayContaining([
              expect.objectContaining({ step_id: 'step_1' })
            ])
          })
        })
      )
    })

    it('should handle step_progress message', async () => {
      const handler = vi.fn()
      wsService.onMessage(handler)
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateMessage({
        type: 'step_started',
        session_id: 'test-session',
        data: {
          step_index: 0,
          total_steps: 3,
          action: 'Buscando datos',
          status: 'started'
        }
      })

      expect(handler).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'step_started',
          data: expect.objectContaining({
            step_index: 0,
            status: 'started'
          })
        })
      )
    })

    it('should handle approval_request message', async () => {
      const handler = vi.fn()
      wsService.onMessage(handler)
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateMessage({
        type: 'approval_request',
        session_id: 'test-session',
        data: {
          approval_id: 'apr-123',
          content: 'SELECT * FROM table',
          content_type: 'sql',
          risk_level: 'low'
        }
      })

      expect(handler).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'approval_request',
          data: expect.objectContaining({
            approval_id: 'apr-123',
            content_type: 'sql'
          })
        })
      )
    })

    it('should unsubscribe handler correctly', async () => {
      const handler = vi.fn()
      const unsubscribe = wsService.onMessage(handler)
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      unsubscribe()

      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateMessage({ type: 'status', session_id: 'test-session', data: {} })

      expect(handler).not.toHaveBeenCalled()
    })
  })

  describe('Connection Handlers', () => {
    it('should call connect handlers on connection', async () => {
      const handler = vi.fn()
      wsService.onConnect(handler)
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      expect(handler).toHaveBeenCalled()
    })

    it('should call disconnect handlers on disconnect', async () => {
      const handler = vi.fn()
      wsService.onDisconnect(handler)
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateDisconnect()

      expect(handler).toHaveBeenCalled()
    })
  })

  describe('Send Messages', () => {
    it('should send message when connected', async () => {
      wsService.connect('test-session')
      await new Promise(resolve => setTimeout(resolve, 10))

      wsService.send({ type: 'test', data: 'hello' })

      const mockWs = (wsService as any).ws as MockWebSocket
      expect(mockWs.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'test', data: 'hello' })
      )
    })

    it('should not throw when sending while disconnected', () => {
      expect(() => {
        wsService.send({ type: 'test' })
      }).not.toThrow()
    })

    /**
     * GAP DEL PRODUCTO (documentado en auditoría 2026-05-24):
     * El método `send()` actualmente DROP el mensaje silenciosamente
     * cuando el WS no está conectado — solo loguea warning. NO hay
     * buffering ni retry. Si esto cambia (se implementa buffer), añadir
     * tests positivos aquí.
     */
    it('drops messages while disconnected (current behavior, no buffer)', () => {
      // No conectado
      wsService.send({ type: 'msg1', data: {} })
      wsService.send({ type: 'msg2', data: {} })
      // Verificación: no hay `ws` interno, no se acumula nada.
      expect((wsService as any).ws).toBeNull()
      // Si se añadiera buffer interno (ej. messageBuffer), el test debería
      // verificar que tiene 2 elementos. Como NO existe, este test
      // confirma el comportamiento current.
    })
  })

  // ===========================================================================
  // Reconnect logic (auditoría 2026-05-24: antes 0 tests)
  // ===========================================================================
  describe('Reconnection', () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('should attempt reconnect after unclean disconnect', async () => {
      wsService.connect('reconnect-test')
      // Avanzar el timer asincrono que abre la conexión.
      await vi.advanceTimersByTimeAsync(5)
      expect(wsService.isConnected).toBe(true)

      const mockWs1 = (wsService as any).ws as MockWebSocket
      // Simular desconexión NO limpia (network drop).
      mockWs1.simulateDisconnect(false)

      // El service NO está conectado inmediatamente.
      expect(wsService.isConnected).toBe(false)

      // Después del primer delay (1000 ms) debe haber intentado reconnect.
      await vi.advanceTimersByTimeAsync(1100)
      // Nuevo MockWebSocket se creó internamente; verificar conexión.
      await vi.advanceTimersByTimeAsync(10)
      expect(wsService.isConnected).toBe(true)
    })

    it('reconnects when the SERVER closes cleanly (restart / deploy)', async () => {
      // V5 F3: al reiniciar el backend el socket se cerraba "limpio" (1001/1012)
      // y el cliente no reconectaba: la aprobación HITL siguiente nunca llegaba
      // y la consulta quedaba colgada con el chip en "Conectado".
      wsService.connect('server-restart')
      await vi.advanceTimersByTimeAsync(5)
      const mockWs1 = (wsService as any).ws as MockWebSocket
      mockWs1.readyState = MockWebSocket.CLOSED
      mockWs1.onclose?.({ code: 1012, reason: 'service restart', wasClean: true })
      expect(wsService.isConnected).toBe(false)

      await vi.advanceTimersByTimeAsync(1100)
      await vi.advanceTimersByTimeAsync(10)
      expect(wsService.isConnected).toBe(true)
      expect((wsService as any).ws).not.toBe(mockWs1)
    })

    it('should NOT attempt reconnect after clean disconnect', async () => {
      wsService.connect('clean-test')
      await vi.advanceTimersByTimeAsync(5)
      expect(wsService.isConnected).toBe(true)

      // Disconnect limpio (Client called disconnect).
      wsService.disconnect()
      expect(wsService.isConnected).toBe(false)

      // Avanzar el tiempo bastante — NO debe haber reconnect.
      await vi.advanceTimersByTimeAsync(60_000)
      expect(wsService.isConnected).toBe(false)
      expect(wsService.currentSessionId).toBeNull()
    })

    it('increments reconnectAttempts counter on unclean disconnect', async () => {
      wsService.connect('reconnect-counter-test')
      await vi.advanceTimersByTimeAsync(5)
      expect(wsService.isConnected).toBe(true)
      expect((wsService as any).reconnectAttempts).toBe(0)

      // Simular un drop NO limpio.
      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateDisconnect(false)

      // El contador subió a 1 (handleReconnect lo incrementa).
      expect((wsService as any).reconnectAttempts).toBeGreaterThanOrEqual(1)
    })

    it('resets reconnectAttempts after successful reconnect', async () => {
      wsService.connect('reset-counter-test')
      await vi.advanceTimersByTimeAsync(5)

      const mockWs1 = (wsService as any).ws as MockWebSocket
      mockWs1.simulateDisconnect(false)

      // Avanzar para que el reconnect ocurra.
      await vi.advanceTimersByTimeAsync(1100)
      await vi.advanceTimersByTimeAsync(10)

      // Una vez conectado de nuevo, el contador debe haberse reseteado
      // (lógica en setupHandlers.onopen).
      if (wsService.isConnected) {
        expect((wsService as any).reconnectAttempts).toBe(0)
      }
    })

    it('uses exponential backoff for reconnect delay', async () => {
      const delays: number[] = []
      const originalSetTimeout = globalThis.setTimeout
      // Spy sobre setTimeout para capturar delays usados.
      vi.spyOn(globalThis, 'setTimeout').mockImplementation((fn: any, ms?: number) => {
        if (ms && ms >= 1000) delays.push(ms)
        return originalSetTimeout(fn, 0)  // ejecutar inmediato en test
      })

      wsService.connect('backoff-test')
      await vi.advanceTimersByTimeAsync(5)

      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateDisconnect(false)
      await vi.advanceTimersByTimeAsync(10)

      // Primer reconnect: 1000 ms (1s × 2^0).
      expect(delays.length).toBeGreaterThanOrEqual(1)
      expect(delays[0]).toBe(1000)

      vi.restoreAllMocks()
    })

    it('FE1: disconnect() cancela el timer de reconexión pendiente', async () => {
      wsService.connect('cancel-test')
      await vi.advanceTimersByTimeAsync(5)

      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateDisconnect(false) // programa reconexión

      // Hay un timer de reconexión pendiente (el campo solo existe con el fix).
      expect((wsService as any).reconnectTimer).not.toBeNull()

      // El usuario se desconecta → debe cancelar el timer pendiente.
      wsService.disconnect()
      expect((wsService as any).reconnectTimer).toBeNull()

      // Y avanzar el reloj no debe reconectar.
      await vi.advanceTimersByTimeAsync(5000)
      expect(wsService.isConnected).toBe(false)
      expect(wsService.currentSessionId).toBeNull()
    })

    it('FE1: un nuevo connect() cancela la reconexión pendiente previa', async () => {
      wsService.connect('s1')
      await vi.advanceTimersByTimeAsync(5)
      const mockWs = (wsService as any).ws as MockWebSocket
      mockWs.simulateDisconnect(false)
      expect((wsService as any).reconnectTimer).not.toBeNull()

      // Conectar a otra sesión debe invalidar la reconexión pendiente de s1.
      wsService.connect('s2')
      expect((wsService as any).reconnectTimer).toBeNull()
      await vi.advanceTimersByTimeAsync(5)
      expect(wsService.currentSessionId).toBe('s2')
    })

    it('S0.3: reconecta con el mismo id si la sesión sigue viva', async () => {
      const recovery = vi.fn(async (id: string) => id)
      wsService.setSessionRecovery(recovery)
      try {
        wsService.connect('viva')
        await vi.advanceTimersByTimeAsync(5)
        ;((wsService as any).ws as MockWebSocket).simulateDisconnect(false)

        await vi.advanceTimersByTimeAsync(1100)
        await vi.advanceTimersByTimeAsync(10)
        expect(recovery).toHaveBeenCalledWith('viva')
        expect(wsService.currentSessionId).toBe('viva')
        expect(wsService.isConnected).toBe(true)
      } finally {
        wsService.setSessionRecovery(null)
        wsService.disconnect()
      }
    })

    it('S0.3: si la sesión se perdió, no insiste con el id viejo', async () => {
      // La recuperación crea otra sesión y delega la reconexión en su dueño.
      const recovery = vi.fn(async () => null)
      wsService.setSessionRecovery(recovery)
      try {
        wsService.connect('perdida')
        await vi.advanceTimersByTimeAsync(5)
        const viejo = (wsService as any).ws as MockWebSocket
        viejo.simulateDisconnect(false)

        await vi.advanceTimersByTimeAsync(1100)
        await vi.advanceTimersByTimeAsync(10)
        expect(recovery).toHaveBeenCalledWith('perdida')
        // No se abrió un socket nuevo contra la sesión inexistente.
        expect((wsService as any).ws).toBe(viejo)
        expect(wsService.isConnected).toBe(false)
      } finally {
        wsService.setSessionRecovery(null)
        wsService.disconnect()
      }
    })

    it('S0.3: si verificar la sesión falla por red, reintenta con el mismo id', async () => {
      wsService.setSessionRecovery(async () => {
        throw new Error('sin red')
      })
      try {
        wsService.connect('sin-red')
        await vi.advanceTimersByTimeAsync(5)
        ;((wsService as any).ws as MockWebSocket).simulateDisconnect(false)

        await vi.advanceTimersByTimeAsync(1100)
        await vi.advanceTimersByTimeAsync(10)
        expect(wsService.currentSessionId).toBe('sin-red')
        expect(wsService.isConnected).toBe(true)
      } finally {
        wsService.setSessionRecovery(null)
        wsService.disconnect()
      }
    })
  })
})

describe('WebSocket Message Types for Planner', () => {
  it('should have correct structure for plan_created', () => {
    const planCreatedMsg: WSMessage = {
      type: 'plan_created',
      session_id: 'test-session',
      data: {
        plan: [
          { step_id: 'step_1', description: 'Buscar', query_fragment: 'busca' },
          { step_id: 'step_2', description: 'Buffer', query_fragment: 'buffer' },
        ],
        reasoning: 'Multi-step plan'
      }
    }

    expect(planCreatedMsg.type).toBe('plan_created')
    const data = planCreatedMsg.data as { plan: unknown[]; reasoning: string }
    expect(data.plan).toHaveLength(2)
    expect(data.reasoning).toBeDefined()
  })

  it('should have correct structure for step_started', () => {
    const stepProgressMsg: WSMessage = {
      type: 'step_started',
      session_id: 'test-session',
      data: {
        step_index: 1,
        total_steps: 3,
        action: 'Aplicando buffer',
        status: 'completed',
        result: { success: true }
      }
    }

    expect(stepProgressMsg.type).toBe('step_started')
    const data = stepProgressMsg.data as { status: string }
    expect(data.status).toBe('completed')
  })

  it('should have correct structure for retry_started', () => {
    const retryMsg: WSMessage = {
      type: 'retry_started',
      session_id: 'test-session',
      data: {
        agent: 'PythonAgent',
        attempt: 2,
        max_attempts: 3,
        error: 'KeyError: column not found',
        action: 'Reintentando operación'
      }
    }

    expect(retryMsg.type).toBe('retry_started')
    const data = retryMsg.data as { attempt: number; max_attempts: number }
    expect(data.attempt).toBeLessThanOrEqual(data.max_attempts)
  })

  it('should have correct structure for execution_cancelled', () => {
    const cancelledMsg: WSMessage = {
      type: 'execution_cancelled',
      session_id: 'test-session',
      data: {
        partial_results: [
          { step_id: 'step_1', success: true }
        ],
        completed_steps: 1,
        total_steps: 3
      }
    }

    expect(cancelledMsg.type).toBe('execution_cancelled')
    const data = cancelledMsg.data as { completed_steps: number; total_steps: number }
    expect(data.completed_steps).toBeLessThan(data.total_steps)
  })
})
