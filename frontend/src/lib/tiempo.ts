/**
 * FH.10 — control de tiempo: las capas con fecha (p. ej. el NDVI de varias escenas) forman
 * una serie; se muestra la de la fecha elegida y `play` la anima. No toca la visibilidad que
 * puso el usuario: la del tiempo se aplica encima (ver `visibleEfectiva`).
 */
import { create } from 'zustand'

import type { MapLayer } from '@/stores/mapStore'

interface TiempoState {
  actual: string | null
  reproduciendo: boolean
  ir: (fecha: string | null) => void
  reproducir: (on: boolean) => void
}

export const useTiempo = create<TiempoState>((set) => ({
  actual: null,
  reproduciendo: false,
  ir: (actual) => set({ actual }),
  reproducir: (reproduciendo) => set({ reproduciendo }),
}))

/** Las fechas de la serie (capas con fecha), ordenadas y sin repetir. */
export function fechasDeSerie(capas: Pick<MapLayer, 'fecha'>[]): string[] {
  return [...new Set(capas.map((c) => c.fecha).filter((f): f is string => !!f))].sort()
}
