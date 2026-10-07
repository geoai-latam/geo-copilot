/**
 * La trazabilidad de un turno: qué hizo el agente para responder, paso a paso. En vivo mientras
 * trabaja (cada paso en curso, hecho o fallido, con la herramienta, sus argumentos, lo que devolvió
 * y cuánto tardó) y, en la respuesta, plegada en «Cómo lo hice».
 */
import { Brain, Check, Loader2, MessageSquareText, Wrench, X } from 'lucide-react'

import type { TrazaPaso } from '@/types'

const ICONO = { interpretar: MessageSquareText, pensar: Brain, herramienta: Wrench, agente: Wrench }

const segundos = (ms?: number) => (typeof ms === 'number' ? `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s` : '')

function Paso({ p }: { p: TrazaPaso }) {
  const Icono = ICONO[p.tipo] ?? Wrench
  return (
    <li className={`traza-paso traza-${p.tipo} traza-${p.estado}`} data-testid="traza-paso">
      <span className="traza-estado" aria-label={p.estado === 'en_curso' ? 'en curso' : p.estado}>
        {p.estado === 'en_curso' ? <Loader2 className="w-3 h-3 animate-spin" />
          : p.estado === 'ok' ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
      </span>
      <div className="traza-cuerpo">
        <div className="traza-titulo">
          <Icono className="w-3 h-3" /> <span>{p.titulo}</span>
          {p.ms !== undefined && <em className="tabular">{segundos(p.ms)}</em>}
        </div>
        {p.argumentos && Object.keys(p.argumentos).length > 0 && (
          <div className="traza-args">
            {Object.entries(p.argumentos).map(([k, v]) => <code key={k} title={`${k}: ${v}`}>{k}: {v}</code>)}
          </div>
        )}
        {p.detalle && <div className="traza-detalle">{p.detalle}</div>}
      </div>
    </li>
  )
}

export function TrazaTurno({ pasos, enVivo = false }: { pasos: TrazaPaso[]; enVivo?: boolean }) {
  if (!pasos.length) return null
  const lista = <ol className="traza-lista">{pasos.map((p) => <Paso key={p.id} p={p} />)}</ol>
  if (enVivo) return <div className="traza traza-viva" data-testid="traza-viva" aria-live="polite">{lista}</div>
  const total = pasos.reduce((s, p) => s + (p.tipo === 'pensar' || p.tipo === 'herramienta' || p.tipo === 'interpretar' ? p.ms ?? 0 : 0), 0)
  const herramientas = pasos.filter((p) => p.tipo === 'herramienta').length
  const fallos = pasos.filter((p) => p.estado === 'fallo').length
  return (
    <details className="traza" data-testid="traza-respuesta">
      <summary>
        Cómo lo hice · {pasos.length} pasos{herramientas ? ` · ${herramientas} herramienta${herramientas > 1 ? 's' : ''}` : ''}
        {fallos ? ` · ${fallos} con error` : ''}{total ? ` · ${segundos(total)}` : ''}
      </summary>
      {lista}
    </details>
  )
}
