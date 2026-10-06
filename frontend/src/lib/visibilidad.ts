/**
 * FH.10 — lo que el mapa dibuja de una capa: su visibilidad (la del usuario o el agente) y,
 * encima, la de la comparación (la capa de la derecha la dibuja el mapa de la cortina) y la
 * del tiempo (de una serie, solo la fecha elegida).
 */
import { useComparacion } from '@/lib/comparacion'
import { fechasDeSerie, useTiempo } from '@/lib/tiempo'
import type { MapLayer } from '@/stores/mapStore'

export function visibleEfectiva(capa: MapLayer, todas: MapLayer[]): boolean {
  if (!capa.visible) return false
  if (useComparacion.getState().actual?.right === capa.id) return false
  const actual = useTiempo.getState().actual
  if (actual && capa.fecha && fechasDeSerie(todas).length > 1) return capa.fecha === actual
  return true
}
