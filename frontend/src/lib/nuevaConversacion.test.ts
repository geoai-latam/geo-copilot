import { beforeEach, describe, expect, it, vi } from 'vitest'

const create = vi.fn()
const reset = vi.fn()
vi.mock('@/services/api', () => ({ sessionApi: { create: (...a: unknown[]) => create(...a), reset: (...a: unknown[]) => reset(...a) } }))

import { nuevaConversacion } from './nuevaConversacion'
import { useChatStore, useMapStore, useSessionStore } from '@/stores'
import { useOperaciones } from './operaciones'

const FC = { type: 'FeatureCollection' as const, features: [] }

describe('V5 FH — «Nueva conversación» es una sesión nueva', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    create.mockReset()
    reset.mockReset()
    useSessionStore.getState().setSessionId('sesion-vieja')
    useMapStore.getState().addLayer(FC, 'Círculo de 5000 m', undefined, 'ds_aaaaaaaaaaaaaaaa')
    useMapStore.getState().setPuntoMarcado({ lon: -74.31, lat: 4.99 })
  })

  it('crea sesión (historial y workspace vacíos), la recuerda y limpia mapa, punto y registro', async () => {
    create.mockResolvedValue({ session_id: 'sesion-nueva' })
    expect(await nuevaConversacion()).toBe('sesion-nueva')
    expect(useSessionStore.getState().sessionId).toBe('sesion-nueva')
    expect(window.sessionStorage.getItem('geo.session')).toBe('sesion-nueva')
    expect(useMapStore.getState().layers).toHaveLength(0)
    expect(useMapStore.getState().puntoMarcado).toBeNull()
    expect(useOperaciones.getState().registro).toHaveLength(0)
    expect(useChatStore.getState().messages).toHaveLength(0)
    expect(reset).not.toHaveBeenCalled()
  })

  it('si no se puede crear, al menos vacía el historial de la actual', async () => {
    create.mockRejectedValue(new Error('sin red'))
    reset.mockResolvedValue({})
    expect(await nuevaConversacion()).toBeNull()
    expect(reset).toHaveBeenCalledWith('sesion-vieja')
  })
})
