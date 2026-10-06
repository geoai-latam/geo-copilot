/**
 * FRT-04: el re-estilo apunta a la capa indicada por el backend
 * (target_layer_id), no a "la última añadida".
 */
import { describe, it, expect } from 'vitest'
import { pickRestyleTarget } from './restyleTarget'

const LAYERS = [
  { id: 'predios', name: 'Predios' },
  { id: 'rios', name: 'Ríos' }, // la ÚLTIMA añadida
]

describe('pickRestyleTarget (FRT-04)', () => {
  it('escenario del hallazgo: estilar "predios" aunque "ríos" sea la última', () => {
    // Backend resolvió target=predios; sin este id se re-estilaría ríos (última).
    const t = pickRestyleTarget(LAYERS, 'predios')
    expect(t?.id).toBe('predios')
  })

  it('sin target del backend → cae a la última añadida (compat)', () => {
    expect(pickRestyleTarget(LAYERS, null)?.id).toBe('rios')
    expect(pickRestyleTarget(LAYERS, undefined)?.id).toBe('rios')
  })

  it('target que ya no existe entre las capas → cae a la última', () => {
    expect(pickRestyleTarget(LAYERS, 'borrada')?.id).toBe('rios')
  })

  it('sin capas → undefined', () => {
    expect(pickRestyleTarget([], 'x')).toBeUndefined()
    expect(pickRestyleTarget([], null)).toBeUndefined()
  })
})
