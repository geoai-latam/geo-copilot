/**
 * FH.10 — identificar multi-capa: un clic lista TODAS las capas en ese punto (de encima a
 * abajo): el elemento de encima de cada capa vectorial, el valor de cada raster ArcGIS (su
 * /identify) y, para un raster de un servicio MCP (teselas sin /identify), dónde se mide.
 * La primera sección es la que viaja al agente como `selected_feature` (como antes).
 */
import type * as maplibregl from 'maplibre-gl'

import { identificarRasters, type SeccionIdentificar } from '@/lib/imageryIdentify'
import { useMapStore } from '@/stores/mapStore'

/** Las secciones vectoriales: por capa, el elemento de encima (orden de dibujo, arriba primero). */
export function seccionesVectoriales(
  hits: { source?: unknown; properties?: Record<string, unknown> | null }[],
  capas: { id: string; name: string }[],
): SeccionIdentificar[] {
  const vistas = new Set<string>()
  const salida: SeccionIdentificar[] = []
  for (const h of hits) {
    const id = typeof h.source === 'string' ? h.source : null
    const capa = id ? capas.find((c) => c.id === id) : undefined
    if (!capa || vistas.has(capa.id) || !h.properties) continue
    vistas.add(capa.id)
    salida.push({ layerId: capa.id, layerName: capa.name, source: 'geojson', properties: h.properties })
  }
  return salida
}

/** Los rasters de servicios MCP que cubren el punto: su valor se mide con una herramienta. */
export function rastersSinIdentify(lng: number, lat: number): SeccionIdentificar[] {
  return useMapStore.getState().layers
    .filter((l) => l.kind === 'raster-xyz' && l.visible)
    .filter((l) => { const ex = l.extent; return !ex || (lng >= ex.xmin && lng <= ex.xmax && lat >= ex.ymin && lat <= ex.ymax) })
    .reverse()
    .map((l) => ({
      layerId: l.id, layerName: l.name, source: 'imagery' as const, properties: {},
      description: 'Su valor aquí se mide con el servicio que la produjo: pídeselo al copiloto («¿cuánto vale aquí?») o clic derecho sobre un elemento.',
    }))
}

/** Identifica en el punto del clic y abre el popup con todas las secciones. */
export async function identificar(
  map: maplibregl.Map,
  e: maplibregl.MapMouseEvent,
  hits: { source?: unknown; properties?: Record<string, unknown> | null }[],
): Promise<void> {
  const store = useMapStore.getState()
  const { lng, lat } = e.lngLat
  const vectores = seccionesVectoriales(hits, store.layers)
  const rasters = await identificarRasters(map, lng, lat)
  const secciones = [...vectores, ...rasters, ...rastersSinIdentify(lng, lat)]
  if (!secciones.length) {
    store.setSelectedFeature(null)
    return
  }
  const [primera] = secciones
  store.setSelectedFeature({
    properties: primera.properties,
    source: primera.source,
    layerName: primera.layerName,
    description: primera.description,
    secciones,
    screenX: e.originalEvent.clientX,
    screenY: e.originalEvent.clientY,
  })
}
