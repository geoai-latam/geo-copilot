/**
 * FH.2 — herramientas de selección sobre el mapa: caja, lazo, cuántos hay
 * seleccionados y quitar la selección (también con Esc).
 *
 * Seleccionar se hace con clic / Shift+clic directamente en el mapa; la caja y
 * el lazo son de un solo uso (se traza arrastrando; Shift al soltar suma).
 */
import { useEffect } from 'react'
import { Lasso, BoxSelect, X } from 'lucide-react'

import { useOperaciones } from '@/lib/operaciones'
import { useMapStore } from '@/stores/mapStore'

const ORIGEN: Record<string, string> = {
  click: 'clic', box: 'caja', lasso: 'lazo', table: 'tabla', query: 'consulta', agent: 'agente',
}

function quitarSeleccion() {
  useOperaciones.getState().ejecutar({ op: 'clear_selection', layer_id: null, args: {}, reason: null }, 'user')
}

export function SeleccionBar() {
  const modo = useMapStore((s) => s.modoSeleccion)
  const setModo = useMapStore((s) => s.setModoSeleccion)
  const layers = useMapStore((s) => s.layers)
  const seleccionadas = layers.filter((l) => l.seleccion)

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape') return
      const t = ev.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return
      if (useMapStore.getState().modoDibujo) return // Esc es de la herramienta de dibujo
      if (useMapStore.getState().modoSeleccion) useMapStore.getState().setModoSeleccion(null)
      else if (useMapStore.getState().layers.some((l) => l.seleccion)) quitarSeleccion()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const total = seleccionadas.reduce((n, l) => n + (l.seleccion?.count ?? 0), 0)
  const detalle = seleccionadas
    .map((l) => `${l.seleccion?.count ?? 0} de «${l.name}» (${ORIGEN[l.seleccion?.origin ?? ''] ?? '?'})`)
    .join(' · ')

  return (
    <div className="seleccion-bar" data-testid="seleccion-bar">
      <button
        className={`icon-btn${modo === 'box' ? ' is-active' : ''}`}
        title="Seleccionar con una caja (arrastra en el mapa; Shift suma)"
        aria-label="Seleccionar con caja"
        aria-pressed={modo === 'box'}
        onClick={() => setModo(modo === 'box' ? null : 'box')}
      >
        <BoxSelect className="w-3.5 h-3.5" />
      </button>
      <button
        className={`icon-btn${modo === 'lasso' ? ' is-active' : ''}`}
        title="Seleccionar con un lazo (dibuja en el mapa; Shift suma)"
        aria-label="Seleccionar con lazo"
        aria-pressed={modo === 'lasso'}
        onClick={() => setModo(modo === 'lasso' ? null : 'lasso')}
      >
        <Lasso className="w-3.5 h-3.5" />
      </button>
      {total > 0 && (
        <span className="seleccion-chip" data-testid="seleccion-chip" title={detalle}>
          {total.toLocaleString('es-CO')} seleccionado{total === 1 ? '' : 's'}
          <button className="icon-btn" onClick={quitarSeleccion} title="Quitar la selección (Esc)"
                  aria-label="Quitar la selección">
            <X className="w-3 h-3" />
          </button>
        </span>
      )}
    </div>
  )
}
