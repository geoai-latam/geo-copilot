/**
 * MAP-SEAM — oráculo de paridad window.__mapTestState.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setMapTestState, getMapTestState } from './mapTestState'

beforeEach(() => {
  delete (window as unknown as Record<string, unknown>).__mapTestState
})

describe('mapTestState', () => {
  it('publica el estado con defaults y mergea parches sucesivos', () => {
    setMapTestState({ featureCount: 5 })
    let s = getMapTestState()
    expect(s?.engine).toBe('maplibre') // motor único
    expect(s?.featureCount).toBe(5)
    expect(s?.rendererKind).toBeNull()

    // Un parche parcial preserva lo anterior (merge, no reemplazo).
    setMapTestState({ rendererKind: 'cluster' })
    s = getMapTestState()
    expect(s?.featureCount).toBe(5)
    expect(s?.rendererKind).toBe('cluster')
  })
})
