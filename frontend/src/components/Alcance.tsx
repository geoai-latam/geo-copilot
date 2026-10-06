/**
 * FH.4 — el alcance del mensaje, a la vista antes de enviarlo:
 *   - chips con lo que el usuario mencionó con `@` (se quitan con ×);
 *   - un chip automático con la selección del mapa: si se quita, ESTE mensaje va
 *     sin ella (el agente trabaja sobre la capa entera);
 *   - la lista de `@` mientras se escribe (↑↓ para moverse, Enter/Tab elige, Esc cierra).
 */
import { AtSign, BoxSelect, RotateCcw, X } from 'lucide-react'

import type { Candidata, Mencion } from '@/lib/menciones'
import { useMapStore } from '@/stores/mapStore'

export function ChipsDeAlcance({
  menciones, onQuitar, sinSeleccion, onSinSeleccion,
}: {
  menciones: Mencion[]
  onQuitar: (m: Mencion) => void
  sinSeleccion: boolean
  onSinSeleccion: (v: boolean) => void
}) {
  const capaSel = useMapStore((s) => s.layers.find((l) => l.seleccion && (l.seleccion.ids?.length || l.seleccion.where)))
  if (!menciones.length && !capaSel) return null
  return (
    <div className="alcance" data-testid="alcance" aria-label="Lo que usará el agente">
      {capaSel && !sinSeleccion && (
        <span className="alcance-chip is-seleccion" data-testid="alcance-seleccion"
              title="El agente sabrá que tienes esto seleccionado. Quítalo para que trabaje sobre la capa entera.">
          <BoxSelect className="w-3 h-3" />
          {capaSel.seleccion?.count ?? 0} seleccionados de {capaSel.name}
          <button type="button" className="icon-btn" aria-label="No usar la selección en este mensaje"
                  onClick={() => onSinSeleccion(true)}>
            <X className="w-3 h-3" />
          </button>
        </span>
      )}
      {capaSel && sinSeleccion && (
        <button type="button" className="alcance-chip is-quitado" data-testid="alcance-sin-seleccion"
                onClick={() => onSinSeleccion(false)} title="Volver a incluir la selección">
          <RotateCcw className="w-3 h-3" /> sin selección: {capaSel.name} entera
        </button>
      )}
      {menciones.map((m) => (
        <span key={`${m.tipo}:${m.layer_id}:${m.campo ?? ''}`} className="alcance-chip" data-testid="alcance-mencion">
          <AtSign className="w-3 h-3" />
          {m.texto.slice(1)}
          <button type="button" className="icon-btn" aria-label={`Quitar ${m.texto}`} onClick={() => onQuitar(m)}>
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
    </div>
  )
}

export function ListaMenciones({
  opciones, activa, onElegir,
}: {
  opciones: Candidata[]
  activa: number
  onElegir: (c: Candidata) => void
}) {
  if (!opciones.length) return null
  return (
    <ul className="menciones-lista" role="listbox" aria-label="Mencionar">
      {opciones.map((c, i) => (
        <li
          key={`${c.tipo}:${c.layer_id}:${c.campo ?? ''}`}
          role="option"
          aria-selected={i === activa}
          className={i === activa ? 'is-activa' : ''}
          // mousedown (no click): que el textarea no pierda el foco antes de elegir
          onMouseDown={(e) => { e.preventDefault(); onElegir(c) }}
        >
          <span className="menciones-texto">{c.texto}</span>
          <span className="menciones-detalle">{c.detalle}</span>
        </li>
      ))}
    </ul>
  )
}
