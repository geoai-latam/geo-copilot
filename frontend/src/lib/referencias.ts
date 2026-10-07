/**
 * FH.7 — enlaces al mapa en las respuestas del agente.
 *
 * El LLM decide si cita y qué (`INSTRUCCION_REFERENCIAS` en el backend):
 *   [[layer:<capa>|texto]]                 una capa
 *   [[layer:<capa>?<campo>=<valor>|texto]] un elemento por un valor que vio en los datos
 *   [[layer:<capa>#<id>|texto]]            un elemento por su id
 *   [[descarga:<capa>?formato=shp&crs=EPSG:9377|texto]]  la capa como archivo (`export_layer`)
 * `<capa>` = id de capa del mapa, `ds_…` o `activa` (la capa que dejó ese turno).
 * Aquí solo se RESUELVE contra el mapa real: lo que no resuelve se muestra como texto.
 */
import type { MapLayer } from '@/stores/mapStore'
import type { GeoJSONFeature } from '@/types'
import type { Predicado } from '@/contracts'
import { identidad, idsQueCumplen, type SeleccionCapa } from './seleccion'

export interface Referencia {
  capa: string
  id?: string
  campo?: string
  valor?: string
  /** Un enlace de DESCARGA de la capa (no la señala en el mapa). */
  descarga?: { formato: string; crs?: string }
}

export type Trozo = { texto: string; ref?: undefined } | { texto: string; ref: Referencia }

const PATRON = /\[\[(layer|descarga):([^\]|#?]+)(?:#([^\]|]+)|\?([^\]|]*))?(?:\|([^\]]*))?\]\]/g

function referenciaDe(tipo: string, capa: string, id?: string, consulta?: string): Referencia {
  const ref: Referencia = { capa: capa.trim(), ...(id ? { id: id.trim() } : {}) }
  if (tipo === 'descarga') {
    const q = new URLSearchParams(consulta ?? '')
    ref.descarga = { formato: q.get('formato') || 'gpkg', ...(q.get('crs') ? { crs: q.get('crs') as string } : {}) }
  } else if (consulta !== undefined) {
    const i = consulta.indexOf('=')
    if (i > 0) { ref.campo = consulta.slice(0, i).trim(); ref.valor = consulta.slice(i + 1).trim() }
  }
  return ref
}

/** El texto de la respuesta en trozos: texto normal y enlaces. */
export function trocear(texto: string): Trozo[] {
  const trozos: Trozo[] = []
  let desde = 0
  for (const m of texto.matchAll(PATRON)) {
    const i = m.index ?? 0
    const [entero, tipo, capa, id, consulta, etiqueta] = m
    // un `?…` de enlace al mapa sin `=` no es un enlace válido: se queda como texto
    if (tipo === 'layer' && consulta !== undefined && !consulta.includes('=')) continue
    if (i > desde) trozos.push({ texto: texto.slice(desde, i) })
    const ref = referenciaDe(tipo, capa, id, consulta)
    trozos.push({ texto: (etiqueta ?? '').trim() || ref.valor || ref.id || ref.capa, ref })
    desde = i + entero.length
  }
  if (desde < texto.length) trozos.push({ texto: texto.slice(desde) })
  return trozos
}

/**
 * La capa del mapa a la que apunta la referencia. `activa` = la última capa que dejó el
 * turno de esa respuesta (si sigue en el mapa) o, sin ella, la de arriba del mapa.
 */
export function resolverCapa(ref: Referencia, capas: MapLayer[], delTurno: string[] = [], texto?: string): MapLayer | undefined {
  const c = ref.capa
  if (c === 'activa') {
    const delTurnoVivas = delTurno.map((id) => capas.find((l) => l.id === id)).filter(Boolean) as MapLayer[]
    return delTurnoVivas[delTurnoVivas.length - 1] ?? capas[capas.length - 1]
  }
  if (c.startsWith('ds_')) return [...capas].reverse().find((l) => l.datasetId === c)
  const nombre = (n: string | undefined) => (n ? capas.filter((l) => l.name.toLowerCase() === n.trim().toLowerCase()) : [])
  const porTexto = nombre(texto)
  // V5 EH.7: un id que ya no existe (de un mensaje anterior) pero cuyo texto ES el nombre de
  // UNA capa del mapa: esa capa (con dos capas homónimas no se adivina).
  // V5 EH.10: con dos capas homónimas («NDVI 2026-06-11» de antes y la de este turno), la del
  // turno; si no, la más reciente (comparaba la cortina contra la vieja).
  const homonimas = nombre(c)
  const porNombre = homonimas.find((l) => delTurno.includes(l.id)) ?? homonimas[homonimas.length - 1]
  return capas.find((l) => l.id === c) ?? porNombre ?? (ref.id === undefined && ref.campo === undefined
    && porTexto.length === 1 ? porTexto[0] : undefined)
}

/**
 * Qué elementos señala dentro de la capa (null = la capa entera; `vacio` = la capa está
 * pero el elemento no, p. ej. quedó fuera de un filtro o el valor no existe).
 */
export function elementos(ref: Referencia, capa: MapLayer): SeleccionCapa | null | 'vacio' {
  if (ref.id !== undefined) {
    const n = Number(ref.id)
    if (!Number.isFinite(n)) return 'vacio'
    if (capa.kind === 'vector-geojson' && !(capa.data?.features ?? []).some((f, i) => identidad(f, i) === n)) return 'vacio'
    return { ids: [n], count: 1, origin: 'link' }
  }
  if (ref.campo !== undefined) {
    const where: Predicado = { field: ref.campo, op: '=', value: ref.valor ?? '' }
    if (capa.kind !== 'vector-geojson') return { where, count: 0, origin: 'link' } // teselas: por condición
    const ids = idsQueCumplen(capa, where)
    return ids.length ? { ids, count: ids.length, origin: 'link' } : 'vacio'
  }
  return null
}

function extender(b: number[], coords: unknown) {
  if (Array.isArray(coords) && typeof coords[0] === 'number') {
    const [x, y] = coords as number[]
    b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y)
  } else if (Array.isArray(coords)) {
    for (const c of coords) extender(b, c)
  }
}

/** La extensión de esos elementos (en memoria), para encuadrarlos. */
export function extensionDe(capa: MapLayer, ids: number[]): [number, number, number, number] | null {
  const quiero = new Set(ids)
  const b = [Infinity, Infinity, -Infinity, -Infinity]
  ;(capa.data?.features ?? []).forEach((f: GeoJSONFeature, i: number) => {
    if (quiero.has(identidad(f, i))) extender(b, (f.geometry as { coordinates?: unknown } | null)?.coordinates)
  })
  return Number.isFinite(b[0]) ? (b as [number, number, number, number]) : null
}
