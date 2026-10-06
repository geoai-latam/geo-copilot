/**
 * FH.3 — herramientas de dibujo: punto, línea, polígono, rectángulo y círculo.
 * Cada figura es de un solo uso: al terminarla se guarda en el workspace como
 * «Área N» / «Línea N» / «Punto N» y entra al mapa como una capa más.
 * Editando vértices (desde el panel de capas): Enter guarda, Esc cancela.
 */
import { useEffect, useState } from 'react'
import { Check, Circle, MapPin, Pentagon, RectangleHorizontal, Ruler, Spline, Square, X } from 'lucide-react'

import { alError, alMedir, cancelarDibujo, empezarDibujo, empezarMedida, guardarEdicion } from '@/lib/dibujo'
import type { Medicion } from '@/services/api'
import { useMapStore, type TipoDibujo } from '@/stores/mapStore'

const HERRAMIENTAS: { tipo: TipoDibujo; label: string; Icono: typeof MapPin }[] = [
  { tipo: 'point', label: 'Dibujar un punto', Icono: MapPin },
  { tipo: 'linestring', label: 'Dibujar una línea (doble clic termina)', Icono: Spline },
  { tipo: 'polygon', label: 'Dibujar un polígono (clic en el primer vértice cierra)', Icono: Pentagon },
  { tipo: 'rectangle', label: 'Dibujar un rectángulo', Icono: RectangleHorizontal },
  { tipo: 'circle', label: 'Dibujar un círculo', Icono: Circle },
]

export function DibujoBar() {
  const modo = useMapStore((s) => s.modoDibujo)
  const editandoId = useMapStore((s) => s.editandoId)
  const editando = useMapStore((s) => s.layers.find((l) => l.id === s.editandoId)?.name)
  const [error, setError] = useState<string | null>(null)
  const [midiendo, setMidiendo] = useState<'linestring' | 'polygon' | null>(null)
  const [medicion, setMedicion] = useState<Medicion | null>(null)

  useEffect(() => {
    alError(setError)
    alMedir((m, err) => { setMedicion(m); setMidiendo(null); if (err) setError(err) })
    return () => { alError(null); alMedir(null) }
  }, [])
  useEffect(() => { if (!modo) setMidiendo((m) => (m && !medicion ? null : m)) }, [modo]) // eslint-disable-line react-hooks/exhaustive-deps

  const medir = (tipo: 'linestring' | 'polygon') => {
    if (midiendo === tipo) { cancelarDibujo(); setMidiendo(null); return }
    setMedicion(null); setError(null); setMidiendo(tipo)
    empezarMedida(tipo)
  }

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return
      const m = useMapStore.getState().modoDibujo
      if (!m) return
      if (ev.key === 'Escape') cancelarDibujo()
      else if (ev.key === 'Enter' && m === 'editar') void guardarEdicion()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="dibujo-bar" data-testid="dibujo-bar">
      {modo === 'editar' && editandoId ? (
        <>
          <span className="dibujo-estado">Editando «{editando}» · arrastra los vértices</span>
          <button className="icon-btn" onClick={() => void guardarEdicion()} title="Guardar (Enter)"
                  aria-label="Guardar la edición">
            <Check className="w-3.5 h-3.5" />
          </button>
          <button className="icon-btn" onClick={cancelarDibujo} title="Cancelar (Esc)" aria-label="Cancelar la edición">
            <X className="w-3.5 h-3.5" />
          </button>
        </>
      ) : (
        HERRAMIENTAS.map(({ tipo, label, Icono }) => (
          <button
            key={tipo}
            className={`icon-btn${modo === tipo && !midiendo ? ' is-active' : ''}`}
            title={`${label} · Esc cancela`}
            aria-label={label}
            aria-pressed={modo === tipo}
            onClick={() => (modo === tipo ? cancelarDibujo() : empezarDibujo(tipo))}
          >
            <Icono className="w-3.5 h-3.5" />
          </button>
        ))
      )}
      {modo !== 'editar' && (
        <>
          <span className="dibujo-sep" aria-hidden />
          <button className={`icon-btn${midiendo === 'linestring' ? ' is-active' : ''}`} onClick={() => medir('linestring')}
                  title="Medir una distancia (doble clic termina) · no se guarda" aria-label="Medir una distancia">
            <Ruler className="w-3.5 h-3.5" />
          </button>
          <button className={`icon-btn${midiendo === 'polygon' ? ' is-active' : ''}`} onClick={() => medir('polygon')}
                  title="Medir un área (clic en el primer vértice cierra) · no se guarda" aria-label="Medir un área">
            <Square className="w-3.5 h-3.5" />
          </button>
        </>
      )}
      {medicion && (
        <span className="dibujo-estado" data-testid="medicion" title="Geodésico exacto (PostGIS), como el agente">
          {medicion.tipo === 'area'
            ? `${(medicion.area_m2 ?? 0).toLocaleString('es-CO', { maximumFractionDigits: 2 })} m² · ${(medicion.area_ha ?? 0).toLocaleString('es-CO', { maximumFractionDigits: 4 })} ha`
            : `${(medicion.longitud_m ?? 0).toLocaleString('es-CO', { maximumFractionDigits: 2 })} m`}
          <button className="icon-btn" onClick={() => setMedicion(null)} aria-label="Quitar la medida"><X className="w-3 h-3" /></button>
        </span>
      )}
      {error && <span className="dibujo-error" role="alert">{error}</span>}
    </div>
  )
}
