import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { leyendaDe, type Leyenda } from '@/lib/leyenda'
import { useLayers, useMapStore } from '@/stores/mapStore'

/**
 * Leyenda de la clasificación de las capas activas.
 *
 * Lee `symbology.class_breaks` (unique_values / graduated_colors /
 * graduated_symbols) y dibuja swatch+label por clase; para single_symbol o sin
 * simbología muestra un único swatch con el color de la capa. Solo se renderiza
 * si hay capas VISIBLES con algo que leyendar (la tabla ya mostraba la
 * clasificación, pero el mapa no — este overlay lo cierra).
 */

export function MapLegend() {
  const layers = useLayers()
  const [open, setOpen] = useState(true)
  // V5 (FH.3): al dibujar o editar, la leyenda tapaba vértices → se contrae (la cabecera
  // sigue para abrirla) y vuelve como estaba al terminar.
  const dibujando = useMapStore((s) => !!s.modoDibujo)
  const antes = useRef(open)
  useEffect(() => {
    if (dibujando) {
      antes.current = open
      setOpen(false)
    } else {
      setOpen(antes.current)
    }
  }, [dibujando]) // eslint-disable-line react-hooks/exhaustive-deps
  // FH.6: leyenda viva de todo lo que se ve (clases, color único, calor, cluster, raster)
  const leyendas = layers.map((l) => ({ id: l.id, ley: leyendaDe(l) }))
    .filter((x): x is { id: string; ley: Leyenda } => x.ley !== null)
  if (leyendas.length === 0) return null

  return (
    <div className="map-legend" aria-label="Leyenda">
      <button
        className="map-legend-head"
        onClick={() => setOpen((o) => !o)}
        title={open ? 'Contraer leyenda' : 'Expandir leyenda'}
      >
        <span>Leyenda</span>
        {open ? <ChevronDown size={13} aria-hidden /> : <ChevronUp size={13} aria-hidden />}
      </button>
      {open && (
        <div className="map-legend-body">
          {leyendas.map(({ id, ley }) => (
            <div key={id} className="map-legend-layer" data-testid="leyenda-capa">
              <div className="map-legend-title" title={ley.titulo}>{ley.titulo}</div>
              {ley.filas?.map((b, i) => (
                <div key={i} className="map-legend-row">
                  <span className="map-legend-swatch" style={{ background: b.color }} aria-hidden />
                  <span className="map-legend-label" title={b.label}>
                    {b.label}
                    {typeof b.count === 'number' ? ` (${b.count})` : ''}
                  </span>
                </div>
              ))}
              {ley.rampa && (
                <div className="map-legend-rampa" aria-label={`Rampa ${ley.rampa.campo ?? ''}`}>
                  <div className="map-legend-rampa-barra"
                       style={{ background: `linear-gradient(to right, ${ley.rampa.colores.join(', ')})` }} />
                  {typeof ley.rampa.min === 'number' && typeof ley.rampa.max === 'number' && (
                    <div className="map-legend-rampa-ticks">
                      <span>{ley.rampa.min.toFixed(2)}</span>
                      <span>{ley.rampa.campo ?? ''}</span>
                      <span>{ley.rampa.max.toFixed(2)}</span>
                    </div>
                  )}
                </div>
              )}
              {(ley.nota || ley.rampa?.nota) && (
                <div className="map-legend-nota">{ley.nota ?? ley.rampa?.nota}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
