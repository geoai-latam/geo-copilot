/**
 * Test setup for Vitest
 */
import { vi } from 'vitest'

// NOTA F4.1 (2026-05-25): el setup global NO importa
// `@testing-library/jest-dom/vitest` porque eso forzaría tener la lib
// instalada para que la suite arranque. En su lugar, los tests de
// componente que necesitan matchers DOM (`.toBeInTheDocument()`, etc.)
// hacen el import directamente en el head del archivo .test.tsx, lo
// cual es self-contained: si activas el archivo .disabled sin instalar,
// vitest falla con un error claro de import.

// Mock WebSocket
class MockWebSocket {
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1 // OPEN

  constructor() {
    setTimeout(() => this.onopen?.(), 0)
  }

  send = vi.fn()
  close = vi.fn()
}

vi.stubGlobal('WebSocket', MockWebSocket)

// Mock fetch
vi.stubGlobal('fetch', vi.fn())

// Mock ResizeObserver
vi.stubGlobal('ResizeObserver', vi.fn(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
})))
