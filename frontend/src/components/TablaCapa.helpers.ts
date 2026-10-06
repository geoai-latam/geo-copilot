/** FH.5 — la tabla vinculada de una capa: lógica pura (filas, orden, estadística). */
import { boundsOf } from '@/lib/maplibreGeoJson'
import { cumple, cumpleTodas, identidad } from '@/lib/seleccion'
import type { MapLayer } from '@/stores/mapStore'
import type { GeoJSONFeatureCollection } from '@/types'

export interface Fila {
  /** La identidad del elemento en el mapa (fid o índice): la misma que usa la selección. */
  id: number
  properties: Record<string, unknown>
  bbox: [number, number, number, number] | null
}

export interface Estadistica {
  n: number
  con_valor: number
  unicos: number
  numerico: boolean
  min?: number | null
  max?: number | null
  media?: number | null
  frecuentes: Array<{ valor: string; n: number }>
}

/** ¿La tabla se sirve desde el workspace (capa en teselas / sin datos en memoria)? */
export const desdeWorkspace = (l: MapLayer) =>
  !!l.datasetId && (l.kind === 'vector-mvt' || !(l.data?.features?.length))

/** ¿Está seleccionado este elemento? (por id o por la condición de la selección) */
export function estaSeleccionada(l: MapLayer, fila: Pick<Fila, 'id' | 'properties'>): boolean {
  const sel = l.seleccion
  if (!sel) return false
  if (sel.ids) return sel.ids.includes(fila.id)
  return sel.where ? cumple(sel.where, fila.properties) : false
}

function comparar(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1
  const na = Number(a)
  const nb = Number(b)
  if (typeof a !== 'boolean' && Number.isFinite(na) && Number.isFinite(nb)) return na - nb
  return String(a).localeCompare(String(b), 'es')
}

/** Las filas de una capa en memoria: solo lo que deja ver su filtro (y, si se pide, lo seleccionado). */
export function filasEnMemoria(
  l: MapLayer, q: { soloSeleccion?: boolean; orden?: string | null; desc?: boolean } = {},
): Fila[] {
  const filas: Fila[] = []
  ;(l.data?.features ?? []).forEach((f, i) => {
    const props = (f.properties ?? {}) as Record<string, unknown>
    if (!cumpleTodas(l.filtro, props)) return
    const fila = { id: identidad(f, i), properties: props, bbox: null as Fila['bbox'] }
    if (q.soloSeleccion && !estaSeleccionada(l, fila)) return
    fila.bbox = boundsOf({ type: 'FeatureCollection', features: [f] } as GeoJSONFeatureCollection)
    filas.push(fila)
  })
  if (q.orden) {
    const campo = q.orden
    filas.sort((a, b) => comparar(a.properties[campo], b.properties[campo]) * (q.desc ? -1 : 1))
  }
  return filas
}

/** Estadística de un campo sobre las filas visibles. */
export function estadisticaEnMemoria(filas: Fila[], campo: string): Estadistica {
  const valores = filas.map((f) => f.properties[campo]).filter((v) => v !== null && v !== undefined && v !== '')
  const nums = valores.map(Number)
  const numerico = valores.length > 0 && valores.every((v) => typeof v !== 'boolean') && nums.every(Number.isFinite)
  const cuenta = new Map<string, number>()
  for (const v of valores) cuenta.set(String(v), (cuenta.get(String(v)) ?? 0) + 1)
  const frecuentes = [...cuenta.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 10).map(([valor, n]) => ({ valor, n }))
  return {
    n: filas.length, con_valor: valores.length, unicos: cuenta.size, numerico, frecuentes,
    ...(numerico ? { min: Math.min(...nums), max: Math.max(...nums), media: nums.reduce((a, b) => a + b, 0) / nums.length } : {}),
  }
}
