/**
 * La trazabilidad del turno en curso: lo que el agente está haciendo, paso a paso, tal como lo
 * cuenta el servidor por el WebSocket (`trace`, orchestrator/traza.py). El chat la pinta en vivo
 * y, al terminar, la guarda con la respuesta (`ChatMessage.traza`).
 */
import { create } from 'zustand'

import type { TrazaPaso } from '@/types'

interface TrazaEstado {
  pasos: TrazaPaso[]
  /** Un evento del servidor: un paso nuevo o la actualización de uno (mismo id: en curso → ok). */
  aplicar: (evento: TrazaPaso) => void
  reiniciar: () => void
}

export const useTraza = create<TrazaEstado>((set) => ({
  pasos: [],
  aplicar: (e) => set((s) => {
    if (!e || typeof e.id !== 'string') return s
    const i = s.pasos.findIndex((p) => p.id === e.id)
    if (i === -1) return { pasos: [...s.pasos, e] }
    const pasos = s.pasos.slice()
    pasos[i] = { ...pasos[i], ...e }
    return { pasos }
  }),
  reiniciar: () => set({ pasos: [] }),
}))
