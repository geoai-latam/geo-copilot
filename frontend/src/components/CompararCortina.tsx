/**
 * FH.10 — comparación con cortina (swipe). El mapa principal dibuja todo menos la capa de la
 * derecha (ver `visibleEfectiva`); este segundo mapa, encima y recortado a la derecha de la
 * cortina, dibuja el mapa base y esa capa, con la MISMA cámara (sigue al principal). Arrastrar
 * la cortina la mueve; ✕ cierra la comparación.
 */
import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import '@/lib/maplibreWorker'
import { X } from 'lucide-react'

import { useComparacion } from '@/lib/comparacion'
import { alCambiarMapa, mapaActivo } from '@/lib/mapaActivo'
import { basemapRasterSpec } from '@/lib/maplibreBasemap'
import { transformRequestApi } from '@/lib/maplibreImagery'
import { cabecerasAuth } from '@/lib/auth'
import { rendererDe } from '@/lib/renderers'
import { BASE_MAPS, useMapStore } from '@/stores/mapStore'

export function CompararCortina() {
  const comp = useComparacion((s) => s.actual)
  const capas = useMapStore((s) => s.layers)
  const baseMapId = useMapStore((s) => s.baseMapId)
  const contRef = useRef<HTMLDivElement | null>(null)
  const mapaRef = useRef<maplibregl.Map | null>(null)
  const izq = comp ? capas.find((l) => l.id === comp.left) : undefined
  const der = comp ? capas.find((l) => l.id === comp.right) : undefined

  // el mapa de la derecha: se crea al abrir, sigue la cámara del principal
  useEffect(() => {
    if (!comp || !contRef.current) return
    const principal = mapaActivo()
    if (!principal) return
    const cfg = BASE_MAPS.find((b) => b.id === baseMapId) ?? BASE_MAPS[0]
    const spec = basemapRasterSpec(cfg)
    const m = new maplibregl.Map({
      container: contRef.current, interactive: false, attributionControl: false,
      center: principal.getCenter(), zoom: principal.getZoom(), bearing: principal.getBearing(), pitch: principal.getPitch(),
      transformRequest: (url) => transformRequestApi(url, cabecerasAuth()),
      style: { version: 8, sources: { base: { type: 'raster', tiles: spec.tiles, tileSize: 256, maxzoom: spec.maxzoom } },
               layers: [{ id: 'base', type: 'raster', source: 'base' }] },
    })
    mapaRef.current = m
    ;(window as unknown as { __mlmapCortina?: maplibregl.Map }).__mlmapCortina = m
    const seguir = () => m.jumpTo({ center: principal.getCenter(), zoom: principal.getZoom(),
                                     bearing: principal.getBearing(), pitch: principal.getPitch() })
    principal.on('move', seguir)
    const fin = alCambiarMapa((nuevo) => { if (!nuevo) m.remove() })
    return () => { principal.off('move', seguir); fin(); m.remove(); mapaRef.current = null }
  }, [comp?.left, comp?.right, baseMapId]) // eslint-disable-line react-hooks/exhaustive-deps

  // la capa de la derecha en el mapa de la cortina
  useEffect(() => {
    const m = mapaRef.current
    if (!m || !der) return
    const poner = () => {
      if (m.getSource(der.id)) return
      const { source, layers } = rendererDe(der.kind).buildSpecs(der, {
        origin: window.location.origin, arcgisProxyBase: '/api/v1/proxy/imagery' })
      m.addSource(der.id, source as never)
      for (const l of layers) {
        if ((l as { id: string }).id.includes('-sel-')) continue
        m.addLayer(l as unknown as maplibregl.LayerSpecification)
      }
    }
    if (m.isStyleLoaded()) poner()
    else m.once('load', poner)
  }, [der])

  if (!comp) return null
  const pct = `${Math.round(comp.pos * 1000) / 10}%`
  const arrastrar = (e: React.PointerEvent<HTMLDivElement>) => {
    const caja = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect()
    const mover = (ev: PointerEvent) => useComparacion.getState().mover((ev.clientX - caja.left) / caja.width)
    const soltar = () => { window.removeEventListener('pointermove', mover); window.removeEventListener('pointerup', soltar) }
    window.addEventListener('pointermove', mover)
    window.addEventListener('pointerup', soltar)
  }
  return (
    <div className="cortina" data-testid="cortina">
      <div ref={contRef} className="cortina-mapa" style={{ clipPath: `inset(0 0 0 ${pct})` }} />
      <div className="cortina-linea" style={{ left: pct }} onPointerDown={arrastrar} role="separator"
           aria-label="Cortina de comparación" aria-valuenow={Math.round(comp.pos * 100)} />
      <div className="cortina-etiqueta izquierda">{izq?.name ?? comp.left}</div>
      <div className="cortina-etiqueta derecha">{der?.name ?? comp.right}</div>
      <button type="button" className="cortina-cerrar icon-btn" aria-label="Cerrar la comparación"
              onClick={() => useComparacion.getState().cerrar()}><X className="w-3.5 h-3.5" /></button>
    </div>
  )
}
