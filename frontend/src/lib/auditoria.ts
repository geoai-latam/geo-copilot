/** F6 (E6.5): cómo se lee una entrada de la auditoría. */
import type { EntradaAuditoria } from '@/services/api'

/** Lo que dice cada entrada en una línea: la consulta, el título de la aprobación o los argumentos. */
export function resumenDe(e: EntradaAuditoria): string {
  const d = e.detalle || {}
  if (typeof d.consulta === 'string') return `«${d.consulta}»`
  if (typeof d.titulo === 'string') {
    const vista = typeof d.vista_previa === 'string' ? `: ${d.vista_previa.replace(/\s+/g, ' ').slice(0, 140)}` : ''
    return `${d.titulo}${vista}`
  }
  if (d.argumentos && typeof d.argumentos === 'object') {
    const txt = JSON.stringify(d.argumentos)
    return txt === '{}' ? '' : txt.slice(0, 160)
  }
  return ''
}
