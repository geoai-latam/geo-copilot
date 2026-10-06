/**
 * Popup flotante que muestra las propiedades de un feature seleccionado
 * en el mapa.
 *
 * Antes (bug reportado): el click del mapa SÍ capturaba las propiedades
 * y las guardaba en `mapStore.selectedFeature`, pero NINGÚN componente
 * las leía → "los popups nunca funcionan". Este componente lee del store
 * y renderiza un card posicionado cerca del click.
 *
 * Soporta dos fuentes:
 * - GeoJSON entities (DataSource): properties del feature.
 * - ImageryLayer features (MapServer/FeatureServer tiles): resultado de
 *   pickImageryLayerFeatures() — incluye `name`, `data`, `description`.
 */

import DOMPurify from 'dompurify'
import { X } from 'lucide-react'
import { useMapStore } from '@/stores'

interface FeaturePopupData {
  properties: Record<string, unknown>
  source: 'geojson' | 'imagery'
  layerName?: string
  description?: string
  /** FH.10: todas las capas en el punto (la primera = properties/layerName). */
  secciones?: { layerName: string; source: 'geojson' | 'imagery'; properties: Record<string, unknown>; description?: string }[]
  screenX: number
  screenY: number
}

const OCULTOS = ['geom', 'geometry', 'shape', 'the_geom']

function Filas({ props }: { props: Record<string, unknown> }) {
  const entries = Object.entries(props).filter(([key]) => !OCULTOS.includes(key.toLowerCase()))
  if (!entries.length) return null
  return (
    <dl className="feature-popup-list">
      {entries.map(([key, value]) => (
        <div key={key} className="feature-popup-row">
          <dt title={key}>{key}</dt>
          <dd title={String(value)}>{formatValue(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function formatValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number') {
    // Números grandes → separador de miles. Decimales → 2-4 dígitos.
    if (Number.isInteger(value)) return value.toLocaleString('es-CO')
    return value.toFixed(value < 1 ? 4 : 2)
  }
  if (typeof value === 'boolean') return value ? 'Sí' : 'No'
  if (value instanceof Date) return value.toISOString().slice(0, 19).replace('T', ' ')
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export function FeaturePopup() { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const selectedFeature = useMapStore((s) => s.selectedFeature) as FeaturePopupData | null
  const setSelectedFeature = useMapStore((s) => s.setSelectedFeature)

  if (!selectedFeature || !selectedFeature.properties) return null
  const secciones = selectedFeature.secciones ?? []

  const entries = Object.entries(selectedFeature.properties).filter(
    ([key]) => !['geom', 'geometry', 'shape', 'the_geom'].includes(key.toLowerCase()),
  )

  if (entries.length === 0 && !selectedFeature.description && secciones.length < 2) return null

  // Posicionamiento: ancla el popup cerca del click pero ajustado para
  // no salirse del viewport. Si el click está muy a la derecha/abajo,
  // el popup se renderiza a la izquierda/arriba.
  const POPUP_W = 320
  const POPUP_H_ESTIMATE = 360
  const margin = 12
  const vw = window.innerWidth
  const vh = window.innerHeight
  let left = selectedFeature.screenX + margin
  let top = selectedFeature.screenY + margin
  if (left + POPUP_W > vw) left = selectedFeature.screenX - POPUP_W - margin
  if (top + POPUP_H_ESTIMATE > vh) top = selectedFeature.screenY - POPUP_H_ESTIMATE - margin
  if (left < margin) left = margin
  if (top < margin) top = margin

  return (
    <div
      className="feature-popup"
      style={{ left, top, width: POPUP_W }}
    >
      <div className="feature-popup-head">
        <span className="feature-popup-source">
          {selectedFeature.source === 'imagery' ? '🌍' : '📍'}
        </span>
        <span className="feature-popup-title" title={selectedFeature.layerName}>
          {selectedFeature.layerName || 'Feature'}
        </span>
        <button
          className="feature-popup-close"
          onClick={() => setSelectedFeature(null)}
          title="Cerrar"
          aria-label="Cerrar popup"
        >
          <X size={12} />
        </button>
      </div>

      <div className="feature-popup-body">
        {secciones.length > 1 ? (
          // FH.10: todas las capas que hay en el punto, de encima a abajo
          secciones.map((sec, i) => (
            <section key={i} className="feature-popup-seccion" data-testid="identificar-seccion">
              {i > 0 && <div className="feature-popup-seccion-titulo">{sec.source === 'imagery' ? '🌍' : '📍'} {sec.layerName}</div>}
              <Filas props={sec.properties} />
              {sec.description && !Object.keys(sec.properties).length && (
                <p className="feature-popup-nota">{sec.description.replace(/<[^>]*>/g, '')}</p>
              )}
            </section>
          ))
        ) : entries.length > 0 ? (
          <dl className="feature-popup-list">
            {entries.map(([key, value]) => (
              <div key={key} className="feature-popup-row">
                <dt title={key}>{key}</dt>
                <dd title={String(value)}>{formatValue(value)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          // Algunos servicios ArcGIS solo devuelven `description` (HTML).
          selectedFeature.description && (
            <div
              className="feature-popup-description"
              // SEC-XSS-POPUP: el `description` es HTML de servicios ArcGIS
              // EXTERNOS (identify). El saneo previo por regex solo quitaba
              // <script> y dejaba pasar <img onerror>, <svg onload>,
              // javascript:, etc. — un servicio malicioso cargado vía Discovery
              // podía ejecutar JS y robar tokens. DOMPurify elimina todo vector
              // ejecutable (handlers on*, javascript:, <script>/<iframe>...)
              // manteniendo el HTML de presentación (tablas, enlaces).
              dangerouslySetInnerHTML={{
                __html: DOMPurify.sanitize(selectedFeature.description, {
                  USE_PROFILES: { html: true },
                }),
              }}
            />
          )
        )}
      </div>
    </div>
  )
}
