/**
 * FH.2 — selección compartida en el cliente: lo que hay seleccionado en cada capa,
 * cómo se resalta y cómo se decide qué cae en una caja o un lazo.
 *
 * Identidad de un elemento: su `id` de Feature si lo trae (el `fid` del workspace:
 * GeoJSON de un dataset, teselas MVT); si no, su índice (el `generateId` de la
 * fuente GeoJSON). En todos los casos es `['id']` en una expresión de MapLibre, y
 * la misma regla que el backend (`platform/seleccion.py::identidad`).
 *
 * Una selección grande (por condición, sobre teselas) no tiene ids: se resalta
 * con un FILTRO sobre sus atributos y viaja al agente como predicado.
 */
import type { Predicado } from '@/contracts'
import type { MapLayer } from '@/stores/mapStore'

export type Origen = 'click' | 'box' | 'lasso' | 'table' | 'query' | 'agent' | 'link'

export interface SeleccionCapa {
  ids?: number[]
  where?: Predicado
  count: number
  origin: Origen
}

/** Filtro que no deja pasar nada (capas de resaltado sin selección). */
export const NADA: unknown[] = ['boolean', false]

const num = (v: unknown) => {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

/** Expresión de MapLibre que deja pasar solo lo seleccionado. */
export function filtroSeleccion(sel: SeleccionCapa | null | undefined): unknown[] {
  if (!sel) return NADA
  if (sel.ids) return sel.ids.length ? ['in', ['id'], ['literal', sel.ids]] : NADA
  return sel.where ? exprDePredicado(sel.where) : NADA
}

/** FH.5: la expresión del filtro de una capa (todas las condiciones), o null si no tiene. */
export function filtroDeCapa(condiciones: Predicado[] | null | undefined): unknown[] | null {
  if (!condiciones?.length) return null
  const partes = condiciones.map(exprDePredicado)
  return partes.length === 1 ? partes[0] : ['all', ...partes]
}

/** `base` Y `extra` (cualquiera puede faltar). */
export function yTambien(base: unknown, extra: unknown[] | null): unknown {
  if (!extra) return base
  if (!base) return extra
  return ['all', base, extra]
}

/** El resaltado: lo seleccionado (y lo que señala un enlace de la respuesta, FH.7) que
 * además pasa el filtro de la capa. */
export function filtroResaltado(layer: {
  seleccion?: SeleccionCapa | null; resaltado?: SeleccionCapa | null; filtro?: Predicado[] | null
}): unknown {
  const sel = filtroSeleccion(layer.seleccion)
  const base = layer.resaltado ? ['any', sel, filtroSeleccion(layer.resaltado)] : sel
  return yTambien(base, filtroDeCapa(layer.filtro))
}

/** Una condición como expresión de MapLibre (misma regla que `cumple`). */
export function exprDePredicado(w: Predicado): unknown[] {
  const campo = ['get', w.field]
  switch (w.op) {
    case 'in':
      return ['in', ['to-string', campo], ['literal', (Array.isArray(w.value) ? w.value : [w.value]).map(String)]]
    case 'contains':
      return ['in', String(w.value).toLowerCase(), ['downcase', ['to-string', campo]]]
    default: {
      const n = num(w.value)
      if (n !== null && typeof w.value !== 'boolean') {
        const op = w.op === '=' ? '==' : w.op
        return ['all', ['has', w.field], [op, ['to-number', campo], n]]
      }
      return [w.op === '=' ? '==' : w.op, ['to-string', campo], String(w.value)]
    }
  }
}

/** ¿Estas propiedades cumplen el predicado? (Misma regla que el backend: `platform/seleccion.py`.) */
export function cumple(w: Predicado, props: Record<string, unknown>): boolean {
  if (!(w.field in props)) return false
  const x = props[w.field]
  if (w.op === 'in') return (Array.isArray(w.value) ? w.value : [w.value]).some((o) => String(x) === String(o))
  if (w.op === 'contains') return String(x).toLowerCase().includes(String(w.value).toLowerCase())
  const a = num(x)
  const b = num(w.value)
  if (a !== null && b !== null) {
    return { '=': a === b, '!=': a !== b, '>': a > b, '>=': a >= b, '<': a < b, '<=': a <= b }[w.op] ?? false
  }
  return w.op === '=' ? String(x) === String(w.value) : w.op === '!=' ? String(x) !== String(w.value) : false
}

/** El id con el que el mapa se refiere a la feature `i` (ver arriba). */
export function identidad(f: object, i: number): number {
  const id = (f as { id?: unknown }).id
  return typeof id === 'number' ? id : i
}

/** FH.5: ¿cumple todas las condiciones del filtro de su capa? */
export function cumpleTodas(condiciones: Predicado[] | null | undefined, props: Record<string, unknown>): boolean {
  return !condiciones?.length || condiciones.every((c) => cumple(c, props))
}

/** Ids de los elementos de una capa EN MEMORIA que cumplen el predicado. */
export function idsQueCumplen(layer: MapLayer, w: Predicado): number[] {
  // FH.5: en una capa filtrada solo existen (y se seleccionan) los que pasan el filtro
  return (layer.data.features ?? []).flatMap((f, i) => {
    const props = (f.properties ?? {}) as Record<string, unknown>
    return cumple(w, props) && cumpleTodas(layer.filtro, props) ? [identidad(f, i)] : []
  })
}

/** Combina una selección nueva de ids con la que había, según el modo. */
export function combinar(previa: number[] | undefined, nuevos: number[], modo: 'replace' | 'add' | 'toggle'): number[] {
  if (modo === 'replace' || !previa) return [...new Set(nuevos)]
  const s = new Set(previa)
  for (const id of nuevos) {
    if (modo === 'add') s.add(id)
    else if (s.has(id)) s.delete(id)
    else s.add(id)
  }
  return [...s]
}

/** Punto en polígono (ray casting). `poligono` = anillo exterior [[x, y], …]. */
export function puntoEnPoligono(p: number[], poligono: number[][]): boolean {
  let dentro = false
  for (let i = 0, j = poligono.length - 1; i < poligono.length; j = i++) {
    const [xi, yi] = poligono[i]
    const [xj, yj] = poligono[j]
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) dentro = !dentro
  }
  return dentro
}

function vertices(coords: unknown, out: number[][] = []): number[][] {
  if (Array.isArray(coords) && typeof coords[0] === 'number') out.push(coords as number[])
  else if (Array.isArray(coords)) for (const c of coords) vertices(c, out)
  return out
}

/**
 * ¿Una geometría toca el lazo? Algún vértice suyo dentro del lazo, o algún
 * vértice del lazo dentro de ella (un lazo pequeño DENTRO de un lote grande).
 */
export function tocaLazo(geom: { type: string; coordinates: unknown } | null | undefined, lazo: number[][]): boolean {
  if (!geom) return false
  const vs = vertices(geom.coordinates)
  if (vs.some((v) => puntoEnPoligono(v, lazo))) return true
  if (geom.type === 'Polygon') {
    const anillo = (geom.coordinates as number[][][])[0]
    return lazo.some((v) => puntoEnPoligono(v, anillo))
  }
  if (geom.type === 'MultiPolygon') {
    return (geom.coordinates as number[][][][]).some((p) => lazo.some((v) => puntoEnPoligono(v, p[0])))
  }
  return false
}
