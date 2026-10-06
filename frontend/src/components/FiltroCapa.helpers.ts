/** FH.5 — ayudas del filtro de capa (sin JSX: se prueban sin montar nada). */
import type { Predicado } from '@/contracts'
import type { MapLayer } from '@/stores/mapStore'

export const OPS: Predicado['op'][] = ['=', '!=', '>', '>=', '<', '<=', 'in', 'contains']
export const ETIQUETA_OP: Record<string, string> = { in: 'en', contains: 'contiene' }

export function camposDeCapa(l: MapLayer): string[] {
  if (l.tiles?.fields?.length) return l.tiles.fields
  const props = l.data?.features?.[0]?.properties
  return props ? Object.keys(props) : []
}

export function textoCondicion(c: Predicado): string {
  const v = Array.isArray(c.value) ? c.value.join(', ') : String(c.value)
  return `${c.field} ${ETIQUETA_OP[c.op] ?? c.op} ${v}`
}

/** El valor como lo escribió el usuario → número si lo es; lista si es `in`. */
export function valorDe(texto: string, op: Predicado['op']): Predicado['value'] {
  const uno = (t: string) => {
    const s = t.trim()
    return s !== '' && Number.isFinite(Number(s)) ? Number(s) : s
  }
  return op === 'in' ? texto.split(',').map(uno).filter((x) => x !== '') : uno(texto)
}
