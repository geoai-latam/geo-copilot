/**
 * Explorador Sentinel-2 — helpers PUROS del panel (sin DOM ni mapa, para probarlos solos).
 *
 * El panel usa las MISMAS tools que el agente (imagery-mcp): `imagery_catalog_grid` (teselas
 * MGRS con su disponibilidad), `imagery_catalog_scenes` (escenas de una tesela) y, para ver o
 * medir una escena, `imagery_composite` / `imagery_ndvi` con su `scene_id`.
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

/** Simbología de la cuadrícula por la métrica elegida (cortes fijos: comparables entre búsquedas). */
export function simbologiaCuadricula(m: Metrica): LayerSymbology {
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
    fill: { color: colores[0], opacity: 0.45 },
    stroke: { color: '#1f2937', width: 0.6, opacity: 0.7 },
  }
}

export interface TeselaS2 {
  tile: string
  escenas: number
  nubes_min: number
  nubes_mediana: number
  cobertura_max: number
  mejor_escena: string
  mejor_fecha: string
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

/** Lado de la caja con la que se pide ver una escena: ~44 km, bajo el tope de 2.500 km² del
 * servicio por análisis. Las teselas se sirven dentro de esa caja. */
const LADO_GRADOS = 0.4

/**
 * Caja (polígono GeoJSON) donde ver la escena: centrada en el centro de la vista si cae dentro
 * de la tesela, si no en el centro de la tesela; recortada a la tesela.
 */
export function cajaParaVer(
  tesela: [number, number, number, number],
  vista: [number, number, number, number] | null,
): { type: 'Polygon'; coordinates: number[][][] } {
  const [tw, ts, te, tn] = tesela
  let cx = (tw + te) / 2, cy = (ts + tn) / 2
  if (vista) {
    const vx = (vista[0] + vista[2]) / 2, vy = (vista[1] + vista[3]) / 2
    if (vx >= tw && vx <= te && vy >= ts && vy <= tn) { cx = vx; cy = vy }
  }
  const h = LADO_GRADOS / 2
  const w = Math.max(tw, cx - h), e = Math.min(te, cx + h)
  const s = Math.max(ts, cy - h), n = Math.min(tn, cy + h)
  return { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] }
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
