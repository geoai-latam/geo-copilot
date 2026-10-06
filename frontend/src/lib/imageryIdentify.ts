/**
 * FRT-03 — identify de capas raster ArcGIS (MapServer / ImageServer / NDVI) para
 * MapLibre.
 *
 * `queryRenderedFeatures` no ve capas raster, así que al hacer click resolvemos
 * la capa imagery visible más arriba cuyo `extent` contiene el punto, armamos el
 * `/identify` (buildIdentifyUrl) y lo pedimos por el proxy same-origin del
 * backend (CORS + SSRF + IP-pinning). Con el resultado poblamos el popup
 * `source: 'imagery'`. Best-effort: cualquier fallo limpia la selección — nunca
 * rompe el click.
 *
 * Aislado de `MapLibreMap.tsx` (que importa el SDK del mapa con WebGL/CSS) para
 * poder testearlo en jsdom: aquí `maplibregl` es sólo un tipo (erasado en
 * runtime), y las dependencias son puras (arcgisIdentify) + el store (zustand).
 */
import type * as maplibregl from 'maplibre-gl'
import { useMapStore } from '@/stores/mapStore'
import { buildIdentifyUrl, parseIdentifyResponse } from '@/lib/arcgisIdentify'
import { cabecerasAuth } from '@/lib/auth'

// identify de capas raster vía el proxy same-origin del backend.
export const IDENTIFY_PROXY_BASE = '/api/v1/proxy/imagery-identify'

/** FH.10: una sección del identificar (una capa en el punto del clic). */
export interface SeccionIdentificar {
  layerId: string | null
  layerName: string
  source: 'geojson' | 'imagery'
  properties: Record<string, unknown>
  description?: string
}

/**
 * FH.10: el valor en el punto de cada raster ArcGIS visible (de encima a abajo), por su
 * /identify. Best-effort: una capa que falla no aparece.
 */
export async function identificarRasters(map: maplibregl.Map, lng: number, lat: number): Promise<SeccionIdentificar[]> {
  const store = useMapStore.getState()
  const capas = store.layers
    .filter((l) => l.kind === 'arcgis-image' && l.visible && l.url)
    .filter((l) => {
      const ex = l.extent
      return !ex || (lng >= ex.xmin && lng <= ex.xmax && lat >= ex.ymin && lat <= ex.ymax)
    })
    .reverse()
  const b = map.getBounds()
  const canvas = map.getCanvas()
  const salida: SeccionIdentificar[] = []
  for (const capa of capas) {
    const service = capa.url as string
    const full = buildIdentifyUrl(service, {
      lon: lng, lat,
      mapExtent: { xmin: b.getWest(), ymin: b.getSouth(), xmax: b.getEast(), ymax: b.getNorth() },
      width: canvas.width || 256, height: canvas.height || 256,
    })
    const url = `${IDENTIFY_PROXY_BASE}?service=${encodeURIComponent(service)}&${full.slice(full.indexOf('?') + 1)}`
    try {
      const resp = await fetch(url, { headers: cabecerasAuth() })
      if (!resp.ok) continue
      const results = parseIdentifyResponse(await resp.json())
      if (!results.length) continue
      const first = results[0]
      const properties: Record<string, unknown> = { ...first.attributes }
      if (first.value !== undefined) properties['Valor'] = first.value
      salida.push({ layerId: capa.id, layerName: first.layerName || capa.name, source: 'imagery', properties })
    } catch {
      // una capa que no responde no tapa a las demás
    }
  }
  return salida
}

export async function identifyImageryAt(
  map: maplibregl.Map,
  e: maplibregl.MapMouseEvent,
): Promise<void> {
  const store = useMapStore.getState()
  const { lng, lat } = e.lngLat
  // Solo los servicios ArcGIS saben responder /identify; un raster XYZ (MCP) no.
  // El orden del store es el de dibujo: la última que cumple es la de encima.
  const target = store.layers
    .filter((l) => l.kind === 'arcgis-image' && l.visible && l.url)
    .filter((l) => {
      const ex = l.extent
      return !ex || (lng >= ex.xmin && lng <= ex.xmax && lat >= ex.ymin && lat <= ex.ymax)
    })
    .slice(-1)[0] // la última añadida se dibuja encima

  if (!target) {
    store.setSelectedFeature(null)
    return
  }

  const b = map.getBounds()
  const canvas = map.getCanvas()
  const service = target.url as string
  const full = buildIdentifyUrl(service, {
    lon: lng,
    lat,
    mapExtent: {
      xmin: b.getWest(), ymin: b.getSouth(), xmax: b.getEast(), ymax: b.getNorth(),
    },
    width: canvas.width || 256,
    height: canvas.height || 256,
  })
  const query = full.slice(full.indexOf('?') + 1)
  const url = `${IDENTIFY_PROXY_BASE}?service=${encodeURIComponent(service)}&${query}`

  try {
    const resp = await fetch(url, { headers: cabecerasAuth() })
    if (!resp.ok) {
      store.setSelectedFeature(null)
      return
    }
    const json = await resp.json()
    const results = parseIdentifyResponse(json)
    if (results.length === 0) {
      store.setSelectedFeature(null)
      return
    }
    const first = results[0]
    const properties: Record<string, unknown> = { ...first.attributes }
    if (first.value !== undefined) properties['Valor'] = first.value
    store.setSelectedFeature({
      properties,
      source: 'imagery',
      layerName: first.layerName || target.name,
      screenX: e.originalEvent.clientX,
      screenY: e.originalEvent.clientY,
    })
  } catch {
    store.setSelectedFeature(null)
  }
}
