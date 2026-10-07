/**
 * Pintar una escena EN EL NAVEGADOR desde sus COG públicos (como GeoLibre): MapLibre pide la
 * tesela `s2cog://{clave}/{z}/{x}/{y}` y este módulo lee por HTTP range, con geotiff.js, solo los
 * bloques de cada banda que la tocan (de la vista reducida que toque al zoom), la reproyecta de UTM
 * a Web Mercator y la compone igual que el servidor (mismas bandas, escala, rangos, rampa y máscara
 * de nubes: el `cog` que declara la herramienta). Sin ida y vuelta al servidor: el contraste cambia
 * al instante y el servidor no compone teselas. Si algo falla (CORS, red), la tesela sale del
 * servidor (`respaldo`), así que el mapa nunca se queda sin imagen.
 */
import { fromUrl, Pool, type GeoTIFF } from 'geotiff'
import proj4 from 'proj4'

import { cabecerasAuth } from '@/lib/auth'
import { logger } from '@/utils/logger'

export interface CogBanda { banda?: string; url: string; escala?: number; offset?: number }

export interface CogSpec {
  version: number
  tipo: 'rgb8' | 'rgb' | 'indice' | 'banda' | 'clases'
  nodata?: number
  bandas: CogBanda[]
  rangos?: [number, number][]
  colores?: string[]
  clases?: Record<string, string>
  mascara?: { url: string; excluir: number[] }
}

export const PROTOCOLO = 's2cog'
const LADO = 256
const MARGEN = 1.15            // se lee un poco más que la tesela: UTM y Mercator no son paralelos
const R = 6378137

/** Lo que pinta cada capa (por clave): su spec y la tesela del servidor de respaldo. */
const capas = new Map<string, { cog: CogSpec; respaldo: string }>()

let pool: Pool | null = null
const tiffs = new Map<string, Promise<GeoTIFF>>()

function abrir(url: string): Promise<GeoTIFF> {
  let t = tiffs.get(url)
  if (!t) {
    t = fromUrl(url, { allowFullFile: false, cacheSize: 200 })
    t.catch(() => tiffs.delete(url))
    tiffs.set(url, t)
    if (tiffs.size > 120) tiffs.delete(tiffs.keys().next().value as string)
  }
  return t
}

function huella(texto: string): string {
  let h = 5381
  for (let i = 0; i < texto.length; i++) h = ((h << 5) + h + texto.charCodeAt(i)) | 0
  return (h >>> 0).toString(36)
}

/** La plantilla de teselas de una capa pintada en el cliente (cambia si cambia su spec). */
export function plantillaCog(idCapa: string, cog: CogSpec, respaldo: string): string {
  const clave = `${idCapa}-${huella(JSON.stringify(cog))}`
  capas.set(clave, { cog, respaldo })
  return `${PROTOCOLO}://${clave}/{z}/{x}/{y}`
}

/** El EPSG de un COG (UTM de Sentinel-2) → su definición de proj4. */
function proyeccionDe(epsg: number): string {
  if (epsg >= 32601 && epsg <= 32660) return `+proj=utm +zone=${epsg - 32600} +datum=WGS84 +units=m +no_defs`
  if (epsg >= 32701 && epsg <= 32760) return `+proj=utm +zone=${epsg - 32700} +south +datum=WGS84 +units=m +no_defs`
  if (epsg === 4326) return 'EPSG:4326'
  throw new Error(`CRS no soportado en el cliente: EPSG:${epsg}`)
}

const MALLA = 16   // 17×17 puntos reproyectados por tesela; el resto se interpola (como GeoLibre)

/** Las coordenadas en el CRS del COG de cada píxel de la tesela (centros) y su caja: se reproyecta
 * una malla de 17×17 y cada píxel se interpola en ella (en una tesela, UTM es casi lineal). */
export function rejilla(z: number, x: number, y: number, crs: string) {
  const tam = (2 * Math.PI * R) / 2 ** z
  const x0 = -Math.PI * R + x * tam
  const y0 = Math.PI * R - y * tam
  const a = proj4('EPSG:3857', crs)
  const mx = new Float64Array((MALLA + 1) ** 2), my = new Float64Array((MALLA + 1) ** 2)
  for (let j = 0; j <= MALLA; j++) {
    for (let i = 0; i <= MALLA; i++) {
      const [ux, uy] = a.forward([x0 + (i / MALLA) * tam, y0 - (j / MALLA) * tam])
      mx[j * (MALLA + 1) + i] = ux; my[j * (MALLA + 1) + i] = uy
    }
  }
  const xs = new Float64Array(LADO * LADO), ys = new Float64Array(LADO * LADO)
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity
  for (let j = 0; j < LADO; j++) {
    const v = ((j + 0.5) / LADO) * MALLA, j0 = Math.min(MALLA - 1, Math.floor(v)), fy = v - j0
    for (let i = 0; i < LADO; i++) {
      const u = ((i + 0.5) / LADO) * MALLA, i0 = Math.min(MALLA - 1, Math.floor(u)), fx = u - i0
      const p = j0 * (MALLA + 1) + i0, q = p + MALLA + 1
      const ux = (mx[p] * (1 - fx) + mx[p + 1] * fx) * (1 - fy) + (mx[q] * (1 - fx) + mx[q + 1] * fx) * fy
      const uy = (my[p] * (1 - fx) + my[p + 1] * fx) * (1 - fy) + (my[q] * (1 - fx) + my[q + 1] * fx) * fy
      const k = j * LADO + i
      xs[k] = ux; ys[k] = uy
      if (ux < minx) minx = ux; if (ux > maxx) maxx = ux
      if (uy < miny) miny = uy; if (uy > maxy) maxy = uy
    }
  }
  const px = (maxx - minx) / LADO, py = (maxy - miny) / LADO
  return { xs, ys, caja: [minx - px, miny - py, maxx + px, maxy + py] as [number, number, number, number] }
}

type Ventana = { datos: ArrayLike<number>[]; caja: [number, number, number, number]; ancho: number; alto: number }

async function leer(url: string, caja: [number, number, number, number], signal: AbortSignal): Promise<Ventana | null> {
  const tiff = await abrir(url)
  const img = await tiff.getImage()
  const [bx0, by0, bx1, by1] = img.getBoundingBox()
  if (caja[2] < bx0 || caja[0] > bx1 || caja[3] < by0 || caja[1] > by1) return null   // fuera de la escena
  const ancho = Math.ceil(LADO * MARGEN), alto = Math.ceil(LADO * MARGEN)
  pool ??= new Pool()
  const datos = await tiff.readRasters({ bbox: caja, width: ancho, height: alto, pool, signal,
                                         resampleMethod: 'nearest', interleave: false }) as unknown as ArrayLike<number>[]
  return { datos, caja, ancho, alto }
}

function muestra(v: Ventana, banda: number, ux: number, uy: number): number | null {
  const [x0, y0, x1, y1] = v.caja
  const c = Math.floor(((ux - x0) / (x1 - x0)) * v.ancho)
  const f = Math.floor(((y1 - uy) / (y1 - y0)) * v.alto)
  if (c < 0 || f < 0 || c >= v.ancho || f >= v.alto) return null
  return v.datos[banda][f * v.ancho + c]
}

const hexARgb = (h: string): [number, number, number] =>
  [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]

/** Color de una rampa (paradas equiespaciadas) en t∈[0,1]. */
export function rampa(colores: [number, number, number][], t: number): [number, number, number] {
  const u = Math.min(1, Math.max(0, t)) * (colores.length - 1)
  const i = Math.min(colores.length - 2, Math.floor(u))
  const f = u - i, a = colores[i], b = colores[i + 1]
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]
}

const reflect = (dn: number, b: CogBanda) => dn * (b.escala ?? 1) + (b.offset ?? 0)
const a8 = (v: number, [lo, hi]: [number, number]) => Math.round(Math.min(1, Math.max(0, (v - lo) / (hi - lo || 1))) * 255)

type Rgb = [number, number, number]
type Pintor = (v: number[], k: number) => Rgb | null

/** El color de un píxel según el tipo del spec (lo mismo que hace el servidor en /tiles*). */
function pintorDe(cog: CogSpec, mascara: ((k: number) => number | null) | null): Pintor {
  const rangos = cog.rangos ?? []
  const colores = (cog.colores ?? ['#000000', '#ffffff']).map(hexARgb)
  const b0 = cog.bandas[0], b1 = cog.bandas[1]
  switch (cog.tipo) {
    case 'rgb8':
      return (v) => [v[0], v[1], v[2]]
    case 'rgb':
      return (v) => [0, 1, 2].map((i) => a8(reflect(v[i], cog.bandas[i]), rangos[i] ?? [0, 0.4])) as Rgb
    case 'clases': {
      const clases = new Map(Object.entries(cog.clases ?? {}).map(([k, c]) => [Number(k), hexARgb(c)]))
      return (v) => clases.get(v[0]) ?? null
    }
    case 'banda':
      return (v) => rampa(colores, a8(reflect(v[0], b0), rangos[0] ?? [0, 0.4]) / 255)
    case 'indice': {
      const excluir = new Set(cog.mascara?.excluir ?? [])
      return (v, k) => {
        const m = mascara?.(k)
        if (m !== null && m !== undefined && excluir.has(m)) return null      // nube: como el servidor
        const a = reflect(v[0], b0), b = reflect(v[1], b1)
        return a + b === 0 ? null : rampa(colores, a8((a - b) / (a + b), rangos[0] ?? [-1, 1]) / 255)
      }
    }
  }
}

/** Compone la tesela RGBA: transparente donde no hay dato (nodata) o el pintor no da color. */
export function componer(cog: CogSpec, valores: (k: number) => (number | null)[],
                         mascara: ((k: number) => number | null) | null): Uint8ClampedArray<ArrayBuffer> {
  const out = new Uint8ClampedArray(LADO * LADO * 4)
  const nodata = cog.nodata ?? 0
  const pintor = pintorDe(cog, mascara)
  for (let k = 0; k < LADO * LADO; k++) {
    const v = valores(k)
    if (v.some((x) => x === null) || v.every((x) => x === nodata)) continue
    const rgb = pintor(v as number[], k)
    if (rgb) out.set([rgb[0], rgb[1], rgb[2], 255], k * 4)
  }
  return out
}

async function aPng(rgba: Uint8ClampedArray<ArrayBuffer>): Promise<ArrayBuffer> {
  const lienzo = new OffscreenCanvas(LADO, LADO)
  lienzo.getContext('2d')!.putImageData(new ImageData(rgba, LADO, LADO), 0, 0)
  return (await lienzo.convertToBlob({ type: 'image/png' })).arrayBuffer()
}

async function pintar(cog: CogSpec, z: number, x: number, y: number, signal: AbortSignal): Promise<ArrayBuffer | null> {
  const primera = await abrir(cog.bandas[0].url)
  const epsg = (await primera.getImage()).geoKeys.ProjectedCSTypeGeoKey ?? (await primera.getImage()).geoKeys.GeographicTypeGeoKey
  const { xs, ys, caja } = rejilla(z, x, y, proyeccionDe(Number(epsg)))
  const ventanas = await Promise.all(cog.bandas.map((b) => leer(b.url, caja, signal)))
  if (ventanas.some((v) => v === null)) return null
  const vs = ventanas as Ventana[]
  const mascara = cog.mascara ? await leer(cog.mascara.url, caja, signal) : null
  const valores = cog.tipo === 'rgb8'
    ? (k: number) => [0, 1, 2].map((s) => muestra(vs[0], s, xs[k], ys[k]))
    : (k: number) => vs.map((v) => muestra(v, 0, xs[k], ys[k]))
  const rgba = componer(cog, valores, mascara ? (k) => muestra(mascara, 0, xs[k], ys[k]) : null)
  return aPng(rgba)
}

const VACIA = new Uint8ClampedArray(LADO * LADO * 4)

/** El manejador del protocolo para MapLibre (`maplibregl.addProtocol(PROTOCOLO, cargarTesela)`). */
export async function cargarTesela(params: { url: string }, abort: AbortController): Promise<{ data: ArrayBuffer }> {
  const m = /^s2cog:\/\/([^/]+)\/(\d+)\/(\d+)\/(\d+)$/.exec(params.url)
  const capa = m ? capas.get(m[1]) : undefined
  if (!m || !capa) return { data: await aPng(VACIA) }
  const [z, x, y] = [Number(m[2]), Number(m[3]), Number(m[4])]
  try {
    return { data: (await pintar(capa.cog, z, x, y, abort.signal)) ?? await aPng(VACIA) }
  } catch (e) {
    if (abort.signal.aborted) throw e
    logger.warn('[cog] la tesela se pinta en el servidor:', e)
    const url = capa.respaldo.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y))
    const r = await fetch(url, { headers: cabecerasAuth(), signal: abort.signal })
    return { data: await r.arrayBuffer() }
  }
}
