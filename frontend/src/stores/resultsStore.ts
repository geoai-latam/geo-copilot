/**
 * Resultados por turno (F4, S4.3): cada respuesta deja N artefactos (tabla,
 * gráfico, estadísticas, informe…) que se ven A LA VEZ en el panel de
 * resultados, y el historial permite volver a los de turnos anteriores.
 * Las capas no se guardan aquí: van al mapa (store de capas).
 */
import { create } from 'zustand'

import type { Artifact } from '@/contracts'

export interface Turno {
  /** query_id del backend. */
  id: string
  consulta: string
  en: Date
  artefactos: Artifact[]
  sql: string | null
  mensaje: string
}

/** Turnos que se guardan (los más viejos se descartan). */
export const MAX_TURNOS = 30

interface ResultsState {
  turnos: Turno[]
  activo: string | null
  /** FH.5: la capa cuya tabla vinculada está abierta en el panel. */
  tablaCapa: string | null
  verTablaCapa: (layerId: string | null) => void
  registrar: (turno: Turno) => void
  ver: (id: string) => void
  limpiar: () => void
}

/**
 * ¿Deja el turno algo que ver en el panel o el mapa? Una respuesta solo de texto
 * («ya te lo mostré») no entra al historial: si no, tapaba el resultado anterior
 * con un panel vacío (V5 F4).
 */
export const dejaResultados = (t: Turno) =>
  t.artefactos.some((a) => a.kind !== 'services' && a.kind !== 'map_command')

export const useResultsStore = create<ResultsState>((set) => ({
  turnos: [],
  activo: null,
  tablaCapa: null,
  verTablaCapa: (layerId) => set({ tablaCapa: layerId }),
  registrar: (turno) =>
    set((s) =>
      dejaResultados(turno)
        ? { turnos: [...s.turnos.filter((t) => t.id !== turno.id), turno].slice(-MAX_TURNOS), activo: turno.id }
        : s,
    ),
  ver: (id) => set((s) => (s.turnos.some((t) => t.id === id) ? { activo: id } : s)),
  limpiar: () => set({ turnos: [], activo: null }),
}))

export const useTurnos = () => useResultsStore((s) => s.turnos)
export const useTurnoActivo = () =>
  useResultsStore((s) => s.turnos.find((t) => t.id === s.activo) ?? null)

/** Artefactos que se muestran en el panel (las capas y órdenes al mapa van al mapa). */
export const esDePanel = (a: Artifact) => a.kind !== 'layer' && a.kind !== 'map_command' && a.kind !== 'services'
