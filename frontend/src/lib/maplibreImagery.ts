/**
 * MAP-IMAGERY-DISCOVERY — monta las capas de imagery dinámicas del panel
 * Discovery (ArcGIS MapServer / ImageServer) como raster sources de MapLibre GL.
 *
 * El motor anterior (ver components/motor-anterior.tsx:788-811 y
 * agents/.../connectors/arcgis.py:304-318) resolvía esto así:
 *   - ImageServer  → UrlTemplateImageryProvider contra `${base}/exportImage`
 *     (motor-anterior.tsx:794-802). ArcGisMapServerImageryProvider NO soporta
 *     ImageServer, por eso se usa /exportImage que TODO ImageServer expone.
 *   - MapServer    → ArcGisMapServerImageryProvider.fromUrl en modo dinámico
 *     `usePreCachedTilesIfAvailable:false` (motor-anterior.tsx:808-811), que
 *     internamente pide teselas a `${base}/export` on-demand.
 *
 * La detección ImageServer vs MapServer es idéntica a la del motor anterior:
 * regex `/imageserver/i` sobre la URL (motor-anterior.tsx:788), que a su vez
 * coincide con el backend `"imageserver" in clean.lower()` (arcgis.py:316).
 *
 * Aquí replicamos esa semántica como specs planos de MapLibre: un raster
 * source cuyo `tiles[0]` es un urlTemplate con el token nativo de MapLibre
 * `{bbox-epsg-3857}` (equivalente al bbox proyectado
 * `{westProjected},{southProjected},{eastProjected},{northProjected}` que el
 * motor anterior expandía — motor-anterior.tsx:797). Los parámetros
 * bboxSR/imageSR/size/format/transparent/f son los mismos que en
 * motor-anterior.tsx:798.
 *
 * FUNCIONES PURAS: devuelven objetos JSON. No importan 'maplibre-gl' en
 * runtime (jsdom no tiene WebGL). Los tipos son locales/loose a propósito.
 */

/** Tipo de servicio ArcGIS que sabe montar este módulo. */
export type ImageryServiceType = 'MapServer' | 'ImageServer'

/**
 * Descriptor de imagery tal como llega del panel Discovery / backend.
 * `type`/`service_type` pueden venir ausentes; en ese caso se infiere de la URL
 * igual que el motor anterior (regex `/imageserver/i`).
 */
export interface ImageryDescriptor {
  service_url: string
  /** 'MapServer' | 'ImageServer' — opcional; se infiere de la URL si falta. */
  type?: ImageryServiceType | string | null
  /** Alias que usa el catálogo de Discovery (DiscoveryServiceType). */
  service_type?: ImageryServiceType | string | null
}

/** Raster source plano de MapLibre GL (spec JSON, sin instanciar el motor). */
export interface RasterSourceSpec {
  type: 'raster'
  tiles: string[]
  tileSize: number
}

/** Raster layer plano de MapLibre GL. */
export interface RasterLayerSpec {
  id: string
  type: 'raster'
  source: string
  paint: Record<string, never>
}

/**
 * Parámetros comunes del request /export|/exportImage.
 * Idénticos a motor-anterior.tsx:798 — bboxSR=3857, imageSR=3857, size=256,256,
 * format=png, transparent=true, f=image. El bbox usa el token nativo de
 * MapLibre {bbox-epsg-3857} en lugar del bbox proyectado del motor anterior.
 */
const EXPORT_QUERY =
  'bbox={bbox-epsg-3857}' +
  '&bboxSR=3857' +
  '&imageSR=3857' +
  '&size=256,256' +
  '&format=png' +
  '&transparent=true' +
  '&f=image'

/** Tamaño de tesela — el motor anterior usa 256x256 (motor-anterior.tsx:800-801). */
const TILE_SIZE = 256

/**
 * ¿La URL apunta a un ImageServer? Réplica exacta de motor-anterior.tsx:788
 * (`/imageserver/i.test(layer.url)`).
 */
export function isImageServerUrl(url: string): boolean {
  return /imageserver/i.test(url)
}

/**
 * Resuelve si el descriptor es ImageServer.
 * - Si trae `type`/`service_type` explícito, se respeta.
 * - Si no, se infiere de la URL igual que el motor anterior.
 */
export function resolveIsImageServer(descriptor: ImageryDescriptor): boolean {
  const declared = (descriptor.type ?? descriptor.service_type)
  if (typeof declared === 'string') {
    const t = declared.toLowerCase()
    if (t === 'imageserver') return true
    if (t === 'mapserver') return false
  }
  // Sin tipo explícito: misma heurística que motor-anterior.tsx:788 / arcgis.py:316.
  return isImageServerUrl(descriptor.service_url)
}

/**
 * Normaliza la base del servicio quitando UNA barra final, igual que el motor
 * anterior en la ruta de ImageServer (motor-anterior.tsx:796 —
 * `layer.url.replace(/\/$/, '')`).
 */
export function cleanServiceUrl(url: string): string {
  return url.replace(/\/$/, '')
}

/**
 * Construye el urlTemplate del raster tile a partir del descriptor.
 * - ImageServer → `${base}/exportImage?…` (motor-anterior.tsx:796)
 * - MapServer   → `${base}/export?…`      (modo dinámico, motor-anterior.tsx:804-811)
 */
export function imageryTileUrl(descriptor: ImageryDescriptor): string {
  const base = cleanServiceUrl(descriptor.service_url)
  const endpoint = resolveIsImageServer(descriptor) ? 'exportImage' : 'export'
  return `${base}/${endpoint}?${EXPORT_QUERY}`
}

/**
 * urlTemplate del tile RUTEADO por el proxy de teselas del backend
 * (MAP-IMAGERY-PROXY). Necesario porque muchos ImageServer gubernamentales
 * (p. ej. IGAC) no envían cabeceras CORS y MapLibre no puede cargar sus
 * teselas directamente.
 *
 * Clave: `{bbox-epsg-3857}` se deja como token LITERAL y separado para que
 * MapLibre lo sustituya por tesela; solo `service` (la base del servicio, sin
 * placeholder) se codifica. El backend añade el resto de parámetros de
 * /exportImage y valida el host contra la allowlist anti-SSRF.
 */
export function proxiedTileUrl(descriptor: ImageryDescriptor, proxyBase: string): string {
  const base = cleanServiceUrl(descriptor.service_url)
  const endpoint = resolveIsImageServer(descriptor) ? 'exportImage' : 'export'
  const service = encodeURIComponent(base)
  return `${proxyBase}?service=${service}&endpoint=${endpoint}&bbox={bbox-epsg-3857}`
}

/**
 * ¿La URL es una plantilla XYZ nativa (lleva el token `{z}`)? Las teselas
 * NDVI/cambio de imagery-mcp lo son —MapLibre las consume directo, con su
 * `?rescale` íntegro— mientras que una URL de ImageServer/MapServer (sin `{z}`)
 * va por el proxy ArcGIS `/exportImage`. Extraído de syncImagery para testear la
 * decisión sin DOM (#37). */
export function isXyzTemplate(url: string): boolean {
  return url.includes('{z}')
}

/**
 * Raster source de MapLibre para una capa de imagery del panel Discovery.
 * Devuelve un objeto plano `{ type:'raster', tiles:[urlTemplate], tileSize:256 }`.
 *
 * Si `proxyBase` se pasa, las teselas se piden al proxy del backend (evita el
 * bloqueo CORS de servidores externos); si no, se va directo al servicio
 * (comportamiento por defecto, usado por los tests puros).
 */
export function imageryRasterSource(
  descriptor: ImageryDescriptor,
  proxyBase?: string,
): RasterSourceSpec {
  const tile = proxyBase
    ? proxiedTileUrl(descriptor, proxyBase)
    : imageryTileUrl(descriptor)
  return {
    type: 'raster',
    tiles: [tile],
    tileSize: TILE_SIZE,
  }
}

/**
 * Raster layer de MapLibre que dibuja el source dado.
 * El id de la capa deriva del sourceId para trazabilidad 1:1.
 */
export function imageryLayerSpec(sourceId: string): RasterLayerSpec {
  return {
    id: `${sourceId}-layer`,
    type: 'raster',
    source: sourceId,
    paint: {},
  }
}

/**
 * Prefijo COMÚN de todas las rutas del proxy protegido del backend. Cubre tanto
 * `/api/v1/proxy/imagery` (teselas ArcGIS de Discovery) como
 * `/api/v1/proxy/mcp/<servidor>/…` (teselas de los servidores MCP).
 */
export const PROXY_BASE = '/api/v1/proxy/'

/** Prefijo de TODA la API protegida (proxy, teselas del workspace, …). */
export const API_BASE = '/api/v1/'

/**
 * `transformRequest` de MapLibre: adjunta las cabeceras de autenticación (F6: el token del
 * usuario) a las peticiones a la API del MISMO origen — proxy de imágenes y teselas del
 * workspace. Los basemaps de otros orígenes van sin cabecera: no se les manda el token y no
 * se rompe su CORS.
 *
 * Antes solo se miraba el proxy de imágenes: las teselas NDVI (`/api/v1/proxy/mcp/…`) llegaron
 * a quedar sin cabecera (401, capa en blanco) y las del workspace nunca la llevaron.
 */
export function transformRequestApi(
  url: string,
  cabeceras: Record<string, string>,
  origen: string = typeof window !== 'undefined' ? window.location.origin : 'http://localhost',
): { url: string; headers?: Record<string, string> } {
  if (!Object.keys(cabeceras).length) return { url }
  let destino: URL
  try {
    destino = new URL(url, origen)
  } catch {
    return { url }
  }
  if (destino.origin === origen && destino.pathname.startsWith(API_BASE)) {
    return { url, headers: cabeceras }
  }
  return { url }
}
