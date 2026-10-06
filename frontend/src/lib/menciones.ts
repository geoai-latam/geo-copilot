/**
 * FH.4 — menciones `@` en el chat: el usuario ELIGE de una lista la capa, el
 * dibujo, la selección o el campo al que se refiere, y eso viaja como referencia
 * (`map_context.menciones`), no como texto que el agente tenga que interpretar.
 * Qué hacer con ella lo sigue decidiendo el agente.
 */
import { esDibujo } from '@/lib/dibujo'
import type { MapLayer } from '@/stores/mapStore'

export interface Mencion {
  tipo: 'capa' | 'seleccion' | 'campo'
  layer_id: string
  /** Como aparece en el mensaje: `@Vías`, `@selección`, `@Lotes.area_m2`. */
  texto: string
  campo?: string
}

export interface Candidata extends Mencion {
  /** Qué es, para la lista: «capa», «dibujo», «selección», «campo de Lotes». */
  detalle: string
}

const sinTildes = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()

function camposDe(l: MapLayer): string[] {
  if (l.tiles?.fields?.length) return l.tiles.fields
  const props = l.data?.features?.[0]?.properties
  return props ? Object.keys(props) : []
}

/** Todo lo que se puede mencionar ahora, en el orden en que se ofrece. */
export function candidatas(layers: MapLayer[]): Candidata[] {
  const salida: Candidata[] = []
  const conSeleccion = layers.find((l) => l.seleccion && (l.seleccion.ids?.length || l.seleccion.where))
  if (conSeleccion) {
    salida.push({ tipo: 'seleccion', layer_id: conSeleccion.id, texto: '@selección',
                  detalle: `${conSeleccion.seleccion?.count ?? 0} seleccionados de ${conSeleccion.name}` })
  }
  const deArriba = [...layers].reverse() // arriba en el mapa = primero en la lista
  for (const l of deArriba) {
    salida.push({ tipo: 'capa', layer_id: l.id, texto: `@${l.name}`, detalle: esDibujo(l) ? 'dibujo' : 'capa' })
  }
  for (const l of deArriba) {
    for (const campo of camposDe(l)) {
      salida.push({ tipo: 'campo', layer_id: l.id, texto: `@${l.name}.${campo}`, campo, detalle: `campo de ${l.name}` })
    }
  }
  return salida
}

/** Las candidatas que casan con lo escrito tras `@` (sin tildes ni mayúsculas). */
export function filtrar(lista: Candidata[], parcial: string, max = 8): Candidata[] {
  const q = sinTildes(parcial)
  const casan = lista.filter((c) => sinTildes(c.texto.slice(1)).includes(q))
  // los campos solo cuando se pide un campo («Lotes.») o casan por su nombre
  const utiles = q.includes('.') ? casan
    : casan.filter((c) => c.tipo !== 'campo' || (q.length > 0 && sinTildes(c.campo ?? '').startsWith(q)))
  return [...utiles.filter((c) => sinTildes(c.texto.slice(1)).startsWith(q)),
          ...utiles.filter((c) => !sinTildes(c.texto.slice(1)).startsWith(q))].slice(0, max)
}

/** El `@…` que se está escribiendo justo antes del cursor, o null. */
export function menciónEnCurso(texto: string, cursor: number): { inicio: number; parcial: string } | null {
  const antes = texto.slice(0, cursor)
  const at = antes.lastIndexOf('@')
  if (at === -1 || (at > 0 && !/\s/.test(antes[at - 1]))) return null
  const parcial = antes.slice(at + 1)
  if (/[\n@]/.test(parcial) || parcial.length > 60) return null
  return { inicio: at, parcial }
}

/** Sustituye el `@parcial` por la mención elegida (y un espacio). */
export function insertar(texto: string, inicio: number, cursor: number, m: Mencion): { texto: string; cursor: number } {
  const nuevo = `${texto.slice(0, inicio)}${m.texto} ${texto.slice(cursor)}`
  return { texto: nuevo, cursor: inicio + m.texto.length + 1 }
}

/** Las menciones que siguen en el texto (si el usuario borró `@Vías`, ya no cuenta). */
export function vigentes(texto: string, menciones: Mencion[]): Mencion[] {
  const vistas = new Set<string>()
  return menciones.filter((m) => {
    const clave = `${m.tipo}:${m.layer_id}:${m.campo ?? ''}`
    if (vistas.has(clave) || !texto.includes(m.texto)) return false
    vistas.add(clave)
    return true
  })
}
