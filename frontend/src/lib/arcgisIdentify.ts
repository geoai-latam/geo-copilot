/**
 * MAP-PICKING-IDENTIFY — Identify REST sobre capas raster ArcGIS para MapLibre.
 *
 * El motor anterior obtenía atributos de las capas imagery (MapServer /
 * FeatureServer renderizadas en servidor) llamando `pickImageryLayerFeatures`,
 * que internamente disparaba una request `/identify` al servicio ArcGIS REST.
 * MapLibre GL no tiene equivalente para capas raster, así que aquí
 * reconstruimos esa llamada `/identify` a mano.
 *
 * Paridad con el motor anterior (ArcGisMapServerImageryProvider.pickFeatures):
 *   - geometry = "lon,lat"                (motor anterior: horizontal + "," + vertical)
 *   - geometryType = "esriGeometryPoint"
 *   - sr = "4326"                          (proyección geográfica)
 *   - tolerance = 2                        (default del motor anterior)
 *   - imageDisplay = "<w>,<h>,96"          (tileWidth,tileHeight,96)
 *   - layers = "visible" | "visible:<id>"  (visible[:this._layers])
 *   - f = "json"
 *
 * Módulo PURO: sólo construye strings/URLs y parsea JSON. No importa el SDK del
 * mapa ni toca el DOM, así corre en jsdom sin WebGL.
 */

/** Tipos de servicio ArcGIS soportados por el catálogo Discovery. */
export type ArcGISServiceType = 'MapServer' | 'ImageServer' | 'FeatureServer'

/** Extent en el sistema de referencia de `sr` (por defecto 4326). */
export interface IdentifyExtent {
  xmin: number
  ymin: number
  xmax: number
  ymax: number
}

/** Parámetros para construir la URL `/identify`. */
export interface IdentifyParams {
  /** Longitud del punto clickeado (grados si sr=4326). */
  lon: number
  /** Latitud del punto clickeado (grados si sr=4326). */
  lat: number
  /** Forzar el tipo de servicio; si se omite se infiere de la forma de la URL. */
  serviceType?: ArcGISServiceType
  /** Tolerancia en píxeles. Default 2 (paridad con el motor anterior). */
  tolerance?: number
  /**
   * Extent visible del mapa, requerido por MapServer/FeatureServer para
   * resolver el punto contra la resolución de pantalla. Ignorado en ImageServer.
   */
  mapExtent?: IdentifyExtent
  /** Ancho del display en píxeles (imageDisplay). Default 256. */
  width?: number
  /** Alto del display en píxeles (imageDisplay). Default 256. */
  height?: number
  /** DPI del display (imageDisplay). Default 96 (paridad motor anterior). */
  dpi?: number
  /**
   * Selector de capas MapServer: "visible", "all", "top" opcionalmente con
   * lista ("visible:0,1"). Si se omite y la URL trae un layer id se usa
   * "visible:<id>"; de lo contrario "visible".
   */
  layers?: string
  /** WKID del sistema de referencia del punto y el extent. Default 4326. */
  sr?: number
  /** Devolver geometría de los features. Default false. */
  returnGeometry?: boolean
  /** ImageServer: devolver los items del mosaico (sus atributos). Default true. */
  returnCatalogItems?: boolean
}

/** Fila normalizada de una respuesta identify. */
export interface IdentifyResult {
  layerName: string
  attributes: Record<string, unknown>
  value?: string | number
}

/** Detecta el tipo de servicio por la forma de la URL. */
export function detectServiceType(serviceUrl: string): ArcGISServiceType {
  const u = serviceUrl.toLowerCase()
  if (u.includes('imageserver')) return 'ImageServer'
  if (u.includes('featureserver')) return 'FeatureServer'
  // Default seguro: MapServer (el más común para identify de raster).
  return 'MapServer'
}

/**
 * Separa un layer id numérico final de la URL del servicio.
 * `.../MapServer/3` → { base: '.../MapServer', layerId: '3' }
 * `.../MapServer`   → { base: '.../MapServer', layerId: null }
 * El endpoint `/identify` vive en la raíz del servicio, no bajo el layer id.
 */
export function splitServiceRoot(serviceUrl: string): { base: string; layerId: string | null } {
  const clean = serviceUrl.replace(/\/+$/, '')
  const m = /^(.*)\/(\d+)$/.exec(clean)
  if (m) return { base: m[1], layerId: m[2] }
  return { base: clean, layerId: null }
}

/**
 * Construye la URL REST `/identify` para el servicio dado.
 * - MapServer/FeatureServer: geometry "lon,lat" + mapExtent + imageDisplay + layers.
 * - ImageServer: geometry JSON con spatialReference + returnCatalogItems.
 */
export function buildIdentifyUrl(serviceUrl: string, params: IdentifyParams): string {
  const type = params.serviceType ?? detectServiceType(serviceUrl)
  const sr = params.sr ?? 4326
  const returnGeometry = params.returnGeometry ?? false

  if (type === 'ImageServer') {
    // ImageServer no lleva layer id; el servicio es atómico.
    const base = serviceUrl.replace(/\/+$/, '')
    const q = new URLSearchParams()
    q.set(
      'geometry',
      JSON.stringify({ x: params.lon, y: params.lat, spatialReference: { wkid: sr } }),
    )
    q.set('geometryType', 'esriGeometryPoint')
    q.set('returnGeometry', String(returnGeometry))
    q.set('returnCatalogItems', String(params.returnCatalogItems ?? true))
    q.set('f', 'json')
    return `${base}/identify?${q.toString()}`
  }

  // MapServer / FeatureServer.
  const { base, layerId } = splitServiceRoot(serviceUrl)
  const width = params.width ?? 256
  const height = params.height ?? 256
  const dpi = params.dpi ?? 96
  const tolerance = params.tolerance ?? 2
  const layers = params.layers ?? (layerId != null ? `visible:${layerId}` : 'visible')

  const q = new URLSearchParams()
  q.set('geometry', `${params.lon},${params.lat}`)
  q.set('geometryType', 'esriGeometryPoint')
  q.set('sr', String(sr))
  q.set('tolerance', String(tolerance))
  if (params.mapExtent) {
    const e = params.mapExtent
    q.set('mapExtent', `${e.xmin},${e.ymin},${e.xmax},${e.ymax}`)
  }
  q.set('imageDisplay', `${width},${height},${dpi}`)
  q.set('layers', layers)
  q.set('returnGeometry', String(returnGeometry))
  q.set('f', 'json')
  return `${base}/identify?${q.toString()}`
}

/** Coerce un `value` de ArcGIS a string|number|undefined sin perder tipos. */
function normalizeValue(v: unknown): string | number | undefined {
  if (typeof v === 'string' || typeof v === 'number') return v
  return undefined
}

/** Extrae un objeto de atributos seguro (siempre un Record). */
function asAttributes(v: unknown): Record<string, unknown> {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v as Record<string, unknown>
  return {}
}

/** Infere el tipo de respuesta cuando el caller no lo pasa. */
function inferResponseType(obj: Record<string, unknown>): ArcGISServiceType {
  if (Array.isArray(obj.results)) return 'MapServer'
  if ('catalogItems' in obj || 'value' in obj || 'objectId' in obj) return 'ImageServer'
  return 'MapServer'
}

function parseMapServer(obj: Record<string, unknown>): IdentifyResult[] {
  const results = Array.isArray(obj.results) ? obj.results : []
  return results.map((r): IdentifyResult => {
    const rr = asAttributes(r)
    return {
      layerName: typeof rr.layerName === 'string' ? rr.layerName : '',
      attributes: asAttributes(rr.attributes),
      value: normalizeValue(rr.value),
    }
  })
}

function parseImageServer(obj: Record<string, unknown>): IdentifyResult[] {
  const name = typeof obj.name === 'string' && obj.name.length > 0 ? obj.name : 'Pixel'
  const value = normalizeValue(obj.value)

  // Si el mosaico devolvió items del catálogo, cada uno aporta sus atributos.
  const catalog = asAttributes(obj.catalogItems)
  const feats = Array.isArray(catalog.features) ? catalog.features : null
  if (feats && feats.length > 0) {
    return feats.map((f): IdentifyResult => {
      const ff = asAttributes(f)
      return { layerName: name, attributes: asAttributes(ff.attributes), value }
    })
  }

  // Sin catálogo: un único resultado con el valor del píxel y `properties`.
  const props = asAttributes(obj.properties)
  return [{ layerName: name, attributes: props, value }]
}

/**
 * Normaliza la respuesta cruda de `/identify` a filas homogéneas.
 * - MapServer/FeatureServer: `{ results: [{ layerName, attributes, value }] }`.
 * - ImageServer: `{ value, name, properties?, catalogItems? }`.
 * Devuelve `[]` para respuestas de error o formas desconocidas.
 */
export function parseIdentifyResponse(
  json: unknown,
  serviceType?: ArcGISServiceType,
): IdentifyResult[] {
  if (!json || typeof json !== 'object' || Array.isArray(json)) return []
  const obj = json as Record<string, unknown>
  if (obj.error) return []

  const type = serviceType ?? inferResponseType(obj)
  if (type === 'ImageServer') return parseImageServer(obj)
  return parseMapServer(obj)
}
