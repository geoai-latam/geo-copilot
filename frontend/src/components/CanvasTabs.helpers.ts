/**
 * Lógica pura del canvas (F4, S4.3): a qué pestaña saltar con un turno nuevo y
 * cómo se ve cada artefacto en el panel de resultados. Sin React, para testearla.
 */
import type { Artifact } from '@/contracts'
import { esDePanel, type Turno } from '@/stores/resultsStore'
import type { Visualization } from '@/types'

export type CanvasTabId = 'map' | 'results' | 'sql'

/**
 * Pestaña para un turno nuevo, o null si no hay que moverse. Con tabla, gráfico,
 * estadísticas o informe → resultados (el panel se abre SOBRE el mapa, que sigue
 * visible: capa + tabla + gráfico a la vez). Solo capas → mapa.
 */
export function pestanaPara(turno: Turno | null): CanvasTabId | null {
  if (!turno) return null
  if (turno.artefactos.some(esDePanel)) return 'results'
  if (turno.artefactos.some((a) => a.kind === 'layer' || a.kind === 'map_command')) return 'map'
  return null
}

export type Feature = { properties: Record<string, unknown> }

/** Filas de una tabla del contrato como las que pinta GeoDataTable. */
export function filasDeTabla(a: Extract<Artifact, { kind: 'table' }>): Feature[] {
  return a.preview.map((fila) => ({ properties: fila as Record<string, unknown> }))
}

/** Un gráfico del contrato en el formato del componente Chart. */
export function visualizacionDeGrafico(a: Extract<Artifact, { kind: 'chart' }>): Visualization {
  const y = Array.isArray(a.spec.y_key) ? a.spec.y_key[0] : a.spec.y_key
  return {
    type: 'chart',
    title: a.spec.title ?? undefined,
    config: { chart_type: a.spec.chart_type as 'bar', x_key: a.spec.x_key, y_key: y },
    data: a.data,
  }
}

/** Sin tabla pero con una capa inline: sus features son la tabla del turno. */
export function tablaDeCapa(turno: Turno): Feature[] | null {
  if (turno.artefactos.some((a) => a.kind === 'table')) return null
  for (const a of turno.artefactos) {
    if (a.kind === 'layer' && a.inline) {
      const feats = (a.inline as { features?: Feature[] }).features
      if (feats?.length) return feats
    }
  }
  return null
}

/** Cuántos artefactos de panel tiene el turno (badge de la pestaña). */
export function cuentaDePanel(turno: Turno | null): number {
  return turno ? turno.artefactos.filter(esDePanel).length + (tablaDeCapa(turno) ? 1 : 0) : 0
}

/** Etiqueta de un turno en el selector de historial. */
export function etiquetaDeTurno(t: Turno): string {
  const hora = t.en.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' })
  const texto = t.consulta.length > 48 ? `${t.consulta.slice(0, 47)}…` : t.consulta
  return `${hora} · ${texto}`
}
