/**
 * FH.5 — el filtro de una capa, visible y editable en el panel de capas.
 *
 * Cada condición es un chip («estrato = 3»): clic la edita, × la quita; «+ filtro»
 * añade otra (todas se cumplen). El agente propone filtros con la misma orden
 * (`set_filter`) y el usuario los retoca aquí: queda en el registro con su autor y
 * se deshace con Ctrl+Z. La capa filtrada ES ese subconjunto para el agente.
 */
import { useState } from 'react'
import { Filter, Plus, X } from 'lucide-react'

import type { Predicado } from '@/contracts'
import { useOperaciones } from '@/lib/operaciones'
import { camposDeCapa, ETIQUETA_OP, OPS, textoCondicion, valorDe } from './FiltroCapa.helpers'
import type { MapLayer } from '@/stores/mapStore'

function aplicar(capaId: string, where: Predicado[]) {
  useOperaciones.getState().ejecutar(
    { op: 'set_filter', layer_id: capaId, args: { where: where.slice(0, 10) as never, count: null }, reason: null }, 'user')
}

export function FiltroCapa({ capa }: { capa: MapLayer }) {
  const condiciones = capa.filtro ?? []
  const campos = camposDeCapa(capa)
  // índice en edición (-1 = nueva) y el borrador
  const [editando, setEditando] = useState<number | null>(null)
  const [borrador, setBorrador] = useState<{ field: string; op: Predicado['op']; valor: string }>(
    { field: campos[0] ?? '', op: '=', valor: '' })

  if (!campos.length) return null

  const abrir = (i: number) => {
    const c = condiciones[i]
    setBorrador(c ? { field: c.field, op: c.op, valor: Array.isArray(c.value) ? c.value.join(', ') : String(c.value) }
                  : { field: campos[0], op: '=', valor: '' })
    setEditando(i)
  }
  const guardar = () => {
    if (editando === null || !borrador.field || borrador.valor.trim() === '') return
    const nueva: Predicado = { field: borrador.field, op: borrador.op, value: valorDe(borrador.valor, borrador.op) }
    const where = editando === -1 ? [...condiciones, nueva] : condiciones.map((c, i) => (i === editando ? nueva : c))
    aplicar(capa.id, where)
    setEditando(null)
  }

  return (
    <div className="filtro-capa" data-testid="filtro-capa">
      {condiciones.map((c, i) => (
        <span key={i} className="filtro-chip">
          <button type="button" className="filtro-texto" onClick={() => abrir(i)}
                  title="Editar esta condición" aria-label={`Editar filtro ${textoCondicion(c)}`}>
            <Filter className="w-3 h-3" /> {textoCondicion(c)}
          </button>
          <button type="button" className="icon-btn" aria-label={`Quitar filtro ${textoCondicion(c)}`}
                  onClick={() => aplicar(capa.id, condiciones.filter((_, j) => j !== i))}>
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
      {condiciones.length > 0 && capa.filtroCount != null && (
        <span className="filtro-cuenta" data-testid="filtro-cuenta">{capa.filtroCount} de {capa.featureCount}</span>
      )}
      {editando === null ? (
        <button type="button" className="filtro-mas" onClick={() => abrir(-1)} aria-label={`Filtrar ${capa.name}`}>
          <Plus className="w-3 h-3" /> filtro
        </button>
      ) : (
        <form className="filtro-form" onSubmit={(e) => { e.preventDefault(); guardar() }}>
          <select aria-label="Campo del filtro" value={borrador.field}
                  onChange={(e) => setBorrador({ ...borrador, field: e.target.value })}>
            {campos.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <select aria-label="Operador del filtro" value={borrador.op}
                  onChange={(e) => setBorrador({ ...borrador, op: e.target.value as Predicado['op'] })}>
            {OPS.map((o) => <option key={o} value={o}>{ETIQUETA_OP[o] ?? o}</option>)}
          </select>
          <input aria-label="Valor del filtro" value={borrador.valor} autoFocus
                 placeholder={borrador.op === 'in' ? 'a, b, c' : 'valor'}
                 onChange={(e) => setBorrador({ ...borrador, valor: e.target.value })}
                 onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setEditando(null) } }} />
          <button type="submit" className="gc-btn gc-btn-ghost" aria-label="Aplicar filtro">OK</button>
          <button type="button" className="icon-btn" aria-label="Cancelar" onClick={() => setEditando(null)}>
            <X className="w-3 h-3" />
          </button>
        </form>
      )}
    </div>
  )
}
