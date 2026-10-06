/**
 * FH.10 — control de tiempo: aparece cuando hay 2+ capas con fecha (una serie, p. ej. el NDVI
 * de varias escenas). Elegir una fecha muestra solo esa; ▶ anima la serie; ✕ vuelve a
 * mostrar todas. El agente lo mueve con `set_time`.
 */
import { useEffect } from 'react'
import { Pause, Play, X } from 'lucide-react'

import { fechasDeSerie, useTiempo } from '@/lib/tiempo'
import { useMapStore } from '@/stores/mapStore'

const PASO_MS = 1500

export function TiempoControl() {
  const capas = useMapStore((s) => s.layers)
  const { actual, reproduciendo, ir, reproducir } = useTiempo()
  const fechas = fechasDeSerie(capas.filter((l) => l.visible))

  useEffect(() => {
    if (!reproduciendo || fechas.length < 2) return
    const t = window.setInterval(() => {
      const i = fechas.indexOf(useTiempo.getState().actual ?? '')
      ir(fechas[(i + 1) % fechas.length])
    }, PASO_MS)
    return () => window.clearInterval(t)
  }, [reproduciendo, fechas.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  // si la fecha elegida ya no está en la serie (se quitó la capa), se muestran todas
  useEffect(() => { if (actual && !fechas.includes(actual)) ir(null) }, [fechas.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  if (fechas.length < 2) return null
  return (
    <div className="tiempo-control" data-testid="tiempo-control" role="group" aria-label="Control de tiempo">
      <button type="button" className="icon-btn" aria-label={reproduciendo ? 'Pausar la serie' : 'Animar la serie'}
              onClick={() => { if (!actual) ir(fechas[0]); reproducir(!reproduciendo) }}>
        {reproduciendo ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
      </button>
      {fechas.length <= 8 ? (
        // una serie corta: cada fecha es un botón (con «todas» elegido, el deslizador ya estaba en
        // la primera y no se podía elegir: V2 FH.10)
        <div className="tiempo-fechas">
          {fechas.map((f) => (
            <button key={f} type="button" className={`tiempo-chip${f === actual ? ' is-active' : ''}`}
                    aria-pressed={f === actual} onClick={() => { reproducir(false); ir(f) }}>{f}</button>
          ))}
        </div>
      ) : (
        <input type="range" min={0} max={fechas.length - 1} step={1} aria-label="Fecha de la serie"
               value={Math.max(0, fechas.indexOf(actual ?? ''))}
               onChange={(e) => { reproducir(false); ir(fechas[Number(e.target.value)]) }} />
      )}
      <span className="tiempo-fecha" data-testid="tiempo-fecha">{actual ?? `todas (${fechas.length})`}</span>
      {actual && (
        <button type="button" className="icon-btn" aria-label="Mostrar todas las fechas"
                onClick={() => { reproducir(false); ir(null) }}><X className="w-3 h-3" /></button>
      )}
    </div>
  )
}
