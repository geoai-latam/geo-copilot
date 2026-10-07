/**
 * Explorador Sentinel-2 — helpers PUROS del panel (sin DOM ni mapa, para probarlos solos).
 *
 * El panel usa las MISMAS tools que el agente (imagery-mcp): `imagery_catalog_world` (las teselas
 * del mundo por meses) e `imagery_catalog_grid` (las de la vista, fechas exactas),
 * `imagery_catalog_scenes` (escenas de una tesela), `imagery_scene_view` (ver una escena entera
 * en cualquier producto), `imagery_band_histogram` (contraste) e `imagery_pixel`.
 */
import type { McpRunResult } from '@/services/api'
import type { LayerSymbology } from '@/types'

/** Id del servidor de imagery en config/mcp_servers*.yaml. */
export const SERVIDOR_IMAGERY = 'imagery'

export type Metrica = 'nubes_min' | 'nubes_mediana' | 'escenas' | 'cobertura_max'

export const METRICAS: { id: Metrica; etiqueta: string }[] = [
  { id: 'nubes_min', etiqueta: 'Escena más despejada' },
  { id: 'nubes_mediana', etiqueta: 'Nubes medianas' },
  { id: 'escenas', etiqueta: 'Número de escenas' },
  { id: 'cobertura_max', etiqueta: 'Cobertura' },
]

// Rampa despejado → nublado (verde → rojo); para «más es mejor» se invierte.
const VERDE_A_ROJO = ['#1a9850', '#91cf60', '#fee08b', '#fc8d59', '#d73027']

const CORTES: Record<Metrica, { cortes: number[]; unidad: string; masEsMejor: boolean }> = {
  nubes_min: { cortes: [0, 5, 15, 30, 60, 100], unidad: '%', masEsMejor: false },
  nubes_mediana: { cortes: [0, 20, 40, 60, 80, 100], unidad: '%', masEsMejor: false },
  escenas: { cortes: [0, 5, 10, 20, 40, 1000], unidad: '', masEsMejor: true },
  cobertura_max: { cortes: [0, 50, 80, 95, 99.9, 100], unidad: '%', masEsMejor: true },
}

/** Simbología de la cuadrícula por la métrica elegida (cortes fijos: comparables entre búsquedas).
 * `relleno: false` deja solo el contorno: mientras se ve una escena, el relleno la teñiría. */
export function simbologiaCuadricula(m: Metrica, relleno = true): LayerSymbology {
  const { cortes, unidad, masEsMejor } = CORTES[m]
  const colores = masEsMejor ? [...VERDE_A_ROJO].reverse() : VERDE_A_ROJO
  const ultimo = cortes.length - 2
  return {
    symbology_type: 'graduated_colors',
    classification_field: m,
    classification_method: 'manual',
    class_breaks: colores.map((color, i) => ({
      min_value: cortes[i],
      max_value: cortes[i + 1],
      label: i === ultimo && m === 'escenas' ? `${cortes[i]}+` : `${cortes[i]}–${cortes[i + 1]}${unidad}`,
      color,
    })),
    fill: { color: colores[0], opacity: relleno ? 0.45 : 0 },
    stroke: { color: '#1f2937', width: relleno ? 0.6 : 1.2, opacity: 0.7 },
  }
}

export interface TeselaS2 {
  tile: string
  escenas: number
  nubes_min: number
  nubes_mediana: number
  cobertura_max: number
  /** Solo con fechas exactas (`imagery_catalog_grid`); el mundo agrega por meses. */
  mejor_escena?: string
  mejor_fecha?: string
  bbox: [number, number, number, number] | null
}

export interface EscenaS2 {
  id: string
  tile: string
  fecha: string
  nubes: number
  cobertura: number
  plataforma: string
  miniatura: string
}

function bboxDe(geom: { coordinates?: unknown } | null | undefined): [number, number, number, number] | null {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity
  const walk = (c: unknown): void => {
    if (!Array.isArray(c)) return
    if (typeof c[0] === 'number' && typeof c[1] === 'number') {
      minx = Math.min(minx, c[0]); maxx = Math.max(maxx, c[0])
      miny = Math.min(miny, c[1]); maxy = Math.max(maxy, c[1])
      return
    }
    for (const h of c) walk(h)
  }
  walk(geom?.coordinates)
  return Number.isFinite(minx) ? [minx, miny, maxx, maxy] : null
}

/** Teselas de un resultado de `imagery_catalog_grid`, de la más despejada a la más nublada. */
export function teselasDe(res: McpRunResult): TeselaS2[] {
  const feats = (res.results.geojson?.features ?? []) as { properties?: Record<string, unknown>; geometry?: { coordinates?: unknown } }[]
  return feats
    .map((f) => ({ ...(f.properties as unknown as Omit<TeselaS2, 'bbox'>), bbox: bboxDe(f.geometry) }))
    .sort((a, b) => a.nubes_min - b.nubes_min || b.escenas - a.escenas)
}

/** Escenas de un resultado de `imagery_catalog_scenes` (la tabla viaja en `data.results`). */
export function escenasDe(res: McpRunResult): EscenaS2[] {
  return (res.results.data?.results ?? []) as unknown as EscenaS2[]
}

/** Ventana por defecto: los últimos `dias` días hasta `hoy` (YYYY-MM-DD). */
export function ventanaPorDefecto(hoy: Date, dias = 90): { desde: string; hasta: string } {
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { desde: iso(new Date(hoy.getTime() - dias * 86_400_000)), hasta: iso(hoy) }
}

/** Centro y zoom que encuadran un bbox (aprox. web-mercator, para `setMapView`). */
export function vistaDeBbox(b: [number, number, number, number]): { centro: [number, number]; zoom: number } {
  const ancho = Math.max(b[2] - b[0], (b[3] - b[1]) * 1.4, 1e-6)
  const zoom = Math.max(1, Math.min(16, Math.log2(360 / ancho) + 0.6))
  return { centro: [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2], zoom: Math.round(zoom * 10) / 10 }
}

/** Fecha corta legible de un ISO (2026-08-10T15:31:43Z → 10 ago 2026). */
export function fechaCorta(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('es-CO', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
}

/** Las teselas más despejadas del mundo (los hechos de `imagery_catalog_world`: la capa entera
 * va al mapa como teselas; cualquier otra se abre con un clic en ella). */
export function teselasDelMundo(res: McpRunResult): TeselaS2[] {
  const top = (res.facts?.mas_despejadas ?? []) as { tile: string; nubes_min: number; escenas: number }[]
  return top.map((t) => ({ ...t, nubes_mediana: NaN, cobertura_max: NaN, bbox: null }))
}

export type GrupoProducto = 'color' | 'indice' | 'banda' | 'calidad'

export interface ProductoS2 { id: string; etiqueta: string; grupo: GrupoProducto; bandas: string[] }

/** Todo lo que se puede ver de una escena (`product` de `imagery_scene_view`). */
export const PRODUCTOS_S2: ProductoS2[] = [
  { id: 'true_color', etiqueta: 'Color real (B04, B03, B02)', grupo: 'color', bandas: ['red', 'green', 'blue'] },
  { id: 'false_color', etiqueta: 'Falso color infrarrojo (B08, B04, B03)', grupo: 'color', bandas: ['nir', 'red', 'green'] },
  { id: 'agriculture', etiqueta: 'Agricultura (B11, B08, B02)', grupo: 'color', bandas: ['swir16', 'nir', 'blue'] },
  { id: 'swir', etiqueta: 'SWIR (B12, B11, B04)', grupo: 'color', bandas: ['swir22', 'swir16', 'red'] },
  { id: 'ndvi', etiqueta: 'NDVI · vegetación', grupo: 'indice', bandas: [] },
  { id: 'ndwi', etiqueta: 'NDWI · agua', grupo: 'indice', bandas: [] },
  { id: 'ndmi', etiqueta: 'NDMI · humedad de la vegetación', grupo: 'indice', bandas: [] },
  { id: 'ndbi', etiqueta: 'NDBI · construido', grupo: 'indice', bandas: [] },
  ...([
    ['coastal', 'B01 · aerosol costero 443 nm'], ['blue', 'B02 · azul 490 nm'], ['green', 'B03 · verde 560 nm'],
    ['red', 'B04 · rojo 665 nm'], ['rededge1', 'B05 · borde rojo 705 nm'], ['rededge2', 'B06 · borde rojo 740 nm'],
    ['rededge3', 'B07 · borde rojo 783 nm'], ['nir', 'B08 · infrarrojo cercano 842 nm'],
    ['nir08', 'B8A · infrarrojo estrecho 865 nm'], ['nir09', 'B09 · vapor de agua 945 nm'],
    ['swir16', 'B11 · SWIR 1610 nm'], ['swir22', 'B12 · SWIR 2190 nm'],
    ['aot', 'AOT · espesor de aerosoles'], ['wvp', 'WVP · vapor de agua (g/cm²)'],
  ] as const).map(([id, etiqueta]) => ({ id, etiqueta, grupo: 'banda' as const, bandas: [id] })),
  { id: 'scl', etiqueta: 'SCL · clasificación de la escena', grupo: 'calidad', bandas: ['scl'] },
  { id: 'cloud', etiqueta: 'Probabilidad de nubes', grupo: 'calidad', bandas: ['cloud'] },
  { id: 'snow', etiqueta: 'Probabilidad de nieve', grupo: 'calidad', bandas: ['snow'] },
]

export const GRUPOS: { id: GrupoProducto; etiqueta: string }[] = [
  { id: 'color', etiqueta: 'Color' }, { id: 'indice', etiqueta: 'Índices' },
  { id: 'banda', etiqueta: 'Bandas' }, { id: 'calidad', etiqueta: 'Calidad' },
]

export const productoS2 = (id: string): ProductoS2 =>
  PRODUCTOS_S2.find((p) => p.id === id) ?? PRODUCTOS_S2[0]

/** El rango con que el servicio pinta un producto por defecto (el de `imagery_scene_view`). */
export function rangoPorDefecto(p: ProductoS2): [number, number] {
  if (p.grupo === 'indice') return [-1, 1]
  if (p.id === 'cloud' || p.id === 'snow') return [0, 100]
  if (p.id === 'aot') return [0, 1]
  if (p.id === 'wvp') return [0, 5]
  return [0, 0.4]
}

/** ¿Admite ajustar el contraste? (la SCL es de clases: no). */
export const ajustable = (p: ProductoS2) => p.id !== 'scl'

export interface DescargaS2 { banda: string; codigo: string; nombre: string; resolucion_m: number | null; url: string }

export interface HistogramaBanda {
  banda: string; p2?: number; p98?: number; min?: number; max?: number
  bordes?: number[]; conteos?: number[]; unidad?: string
  clases?: { valor: number; etiqueta: string; pct: number }[]
}

export interface PixelS2 {
  bandas: Record<string, { codigo: string; nombre: string; valor: number | null; unidad?: string; clase?: string }>
  indices: Record<string, { nombre: string; valor: number; lectura: string }>
}
