/**
 * FH.10 — comparación con cortina (swipe) entre dos capas: `left` se ve a la izquierda de la
 * cortina y `right` a la derecha. La abre el usuario o el agente (`compare`); el mapa
 * principal oculta `right` y un segundo mapa recortado la dibuja (CompararCortina).
 */
import { create } from 'zustand'

export interface Comparacion {
  left: string
  right: string
  /** Posición de la cortina, 0..1 del ancho del mapa. */
  pos: number
}

interface ComparacionState {
  actual: Comparacion | null
  abrir: (left: string, right: string) => void
  mover: (pos: number) => void
  cerrar: () => void
}

export const useComparacion = create<ComparacionState>((set) => ({
  actual: null,
  abrir: (left, right) => set({ actual: { left, right, pos: 0.5 } }),
  mover: (pos) => set((s) => (s.actual ? { actual: { ...s.actual, pos: Math.min(0.98, Math.max(0.02, pos)) } } : s)),
  cerrar: () => set({ actual: null }),
}))
