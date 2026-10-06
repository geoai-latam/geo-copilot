/**
 * MAP-CLUSTER-HEATMAP — clustering y heatmap NATIVOS de MapLibre GL.
 *
 * Funciones PURAS: devuelven specs de estilo MapLibre (objetos JSON planos:
 * source geojson con `cluster:true`, layers `circle`/`symbol`/`heatmap`). No
 * importa 'maplibre-gl' en runtime — sólo produce el JSON que MapLibre consume,
 * así el test corre en jsdom sin WebGL.
 *
 * Paridad PIXEL-PERFECT con el motor anterior (el motor anterior). Replica la semántica de
 * `setupClustering` / `buildHeatmapGrid` / `paletteFromSymbology` de
 * frontend/src/lib/la simbología anterior sin importar motor anterior:
 *   - clustering: pixelRange=60 → clusterRadius=60 (la simbología anterior:346),
 *     minimumClusterSize=3 → clusterMinPoints=3 (la simbología anterior:347),
 *     tamaño del badge (la simbología anterior:358) y color por count
 *     (la simbología anterior:361-362) vía sampleRamp sobre la MISMA paleta.
 *   - heatmap: la paleta viene del backend (class_breaks) — NO viridis fijo.
 *     Ese era el bug conocido; aquí la rampa se deriva de opts.palette.
 */
import type { LayerSymbology, ClassBreak, GeoJSONFeatureCollection } from '@/types'

// ──────────────────────────────────────────────────────────────────────────
// Tipos loose de specs MapLibre (evitamos importar 'maplibre-gl' → sin WebGL).
// ──────────────────────────────────────────────────────────────────────────

/** Expresión de estilo MapLibre (array-of-arrays JSON). */
export type MapLibreExpression = unknown[]

/** Promedio de todas las posiciones [x,y] de una estructura de coordenadas
 * (point/line/polygon/multi*). Punto representativo suficiente para clustering
 * y densidad (no es el centroide geométrico exacto, pero sí estable y barato). */
function averagePosition(coords: unknown): [number, number] | null {
  let sx = 0
  let sy = 0
  let n = 0
  const walk = (c: unknown): void => {
    if (!Array.isArray(c)) return
    if (typeof c[0] === 'number' && typeof c[1] === 'number') {
      sx += c[0] as number
      sy += c[1] as number
      n += 1
    } else {
      for (const child of c) walk(child)
    }
  }
  walk(coords)
  return n > 0 ? [sx / n, sy / n] : null
}

/**
 * Convierte una FeatureCollection a PUNTOS (centroides representativos).
 *
 * Cluster y heatmap son operaciones de PUNTOS: sobre polígonos/líneas MapLibre
 * las ignora en silencio (no-op). Cuando el usuario pide cluster/heatmap sobre
 * datos no-puntuales, degradamos usando el centroide de cada geometría en vez
 * de no mostrar nada. Los Point se conservan tal cual.
 */
export function toCentroidPoints(geojson: GeoJSONFeatureCollection): GeoJSONFeatureCollection {
  const features = (geojson.features ?? [])
    .map((f) => {
      const g = f.geometry as { type?: string; coordinates?: unknown } | undefined
      if (g?.type === 'Point') return f
      const pos = averagePosition(g?.coordinates)
      if (!pos) return null
      return {
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates: pos },
        properties: f.properties,
      }
    })
    .filter((f): f is NonNullable<typeof f> => f !== null)
  return { type: 'FeatureCollection', features: features as never }
}

export interface ClusterGeoJSONSourceSpec {
  type: 'geojson'
  data: unknown
  cluster: true
  clusterRadius: number
  clusterMaxZoom: number
  clusterMinPoints: number
}

export interface CircleLayerSpec {
  id: string
  type: 'circle'
  source: string
  filter: MapLibreExpression
  paint: Record<string, number | string | MapLibreExpression>
}

export interface SymbolLayerSpec {
  id: string
  type: 'symbol'
  source: string
  filter: MapLibreExpression
  layout: Record<string, number | string | boolean | MapLibreExpression>
  paint: Record<string, number | string | MapLibreExpression>
}

export interface HeatmapLayerSpec {
  id: string
  type: 'heatmap'
  source: string
  maxzoom?: number
  paint: Record<string, number | string | MapLibreExpression>
}

// ──────────────────────────────────────────────────────────────────────────
// PALETA — réplica de paletteFromSymbology (la simbología anterior:312-318) sin motor anterior.
// ──────────────────────────────────────────────────────────────────────────

/** Rampa viridis por defecto (idéntica a DEFAULT_VIRIDIS del motor anterior). */
export const DEFAULT_VIRIDIS: readonly string[] = [
  '#440154', '#482878', '#3e4989', '#31688e',
  '#26838f', '#1f9e89', '#6cce5a', '#b5de2c',
]

/**
 * Extrae la paleta del SymbologyConfig (class_breaks → colores). Equivalente a
 * `paletteFromSymbology` del motor anterior (la simbología anterior:312).
 */
export function paletteFrom(symb: LayerSymbology | undefined): string[] {
  const breaks = symb?.class_breaks
  if (breaks && breaks.length > 0) {
    return breaks.map((b: ClassBreak) => b.color)
  }
  return [...DEFAULT_VIRIDIS]
}

/**
 * Interpolación lineal en una rampa de colores hex. t ∈ [0,1].
 * Réplica exacta de sampleRamp (la simbología anterior:287-296).
 */
export function sampleRamp(palette: string[], t: number): string {
  if (palette.length === 0) return '#888'
  if (palette.length === 1) return palette[0]
  const clamped = Math.max(0, Math.min(1, t))
  const scaled = clamped * (palette.length - 1)
  const i = Math.floor(scaled)
  const frac = scaled - i
  if (i >= palette.length - 1) return palette[palette.length - 1]
  return lerpHex(palette[i], palette[i + 1], frac)
}

/** Réplica exacta de lerpHex (la simbología anterior:298-309). */
function lerpHex(a: string, b: string, t: number): string {
  const ar = parseInt(a.slice(1, 3), 16)
  const ag = parseInt(a.slice(3, 5), 16)
  const ab = parseInt(a.slice(5, 7), 16)
  const br = parseInt(b.slice(1, 3), 16)
  const bg = parseInt(b.slice(3, 5), 16)
  const bb = parseInt(b.slice(5, 7), 16)
  const r = Math.round(ar + (br - ar) * t)
  const g = Math.round(ag + (bg - ag) * t)
  const bl = Math.round(ab + (bb - ab) * t)
  return '#' + [r, g, bl].map((n) => n.toString(16).padStart(2, '0')).join('')
}

// ──────────────────────────────────────────────────────────────────────────
// CLUSTER SOURCE — geojson con cluster:true (nativo de MapLibre).
// ──────────────────────────────────────────────────────────────────────────

export interface ClusterSourceOpts {
  /**
   * Radio de agrupación en px. Default 60 para igualar el `pixelRange` del
   * motor anterior (la simbología anterior:346), NO el ~50 default de MapLibre.
   */
  radius?: number
  /** Zoom máximo al que se agrupa. Default 14 (default nativo de MapLibre). */
  maxZoom?: number
  /**
   * Mínimo de puntos para formar un cluster. Default 3 para igualar
   * `minimumClusterSize` del motor anterior (la simbología anterior:347).
   *
   * DIFERENCIA de motores: MapLibre agrupa desde 2 puntos por defecto; el motor
   * anterior exigía 3. Exponemos el parámetro (clusterMinPoints) para conservar
   * la paridad; súbelo/bájalo según necesites.
   */
  minPoints?: number
}

/**
 * Construye el source geojson con clustering nativo de MapLibre.
 * `geojson` es la FeatureCollection cruda (misma `layer.data` del store).
 */
export function clusterSourceSpec(
  geojson: unknown,
  opts: ClusterSourceOpts = {},
): ClusterGeoJSONSourceSpec {
  const { radius = 60, maxZoom = 14, minPoints = 3 } = opts
  return {
    type: 'geojson',
    data: geojson,
    cluster: true,
    clusterRadius: radius,
    clusterMaxZoom: maxZoom,
    clusterMinPoints: minPoints,
  }
}

// ──────────────────────────────────────────────────────────────────────────
// CLUSTER LAYERS — circle (radio+color por point_count) + symbol (label count).
// ──────────────────────────────────────────────────────────────────────────

/**
 * Tamaño (diámetro px) del badge del cluster según el count.
 * Réplica exacta de la fórmula del motor anterior (la simbología anterior:358):
 *   size = min(64, max(28, 16 + log2(count) * 6))
 */
export function clusterBadgeSize(count: number): number {
  return Math.min(64, Math.max(28, 16 + Math.log2(count) * 6))
}

/**
 * Posición t ∈ [0,1] en la paleta según el count.
 * Réplica exacta del motor anterior (la simbología anterior:361):
 *   t = min(1, log10(count) / 2)   // 1 punto = 0, 100 = 1
 */
export function clusterColorT(count: number): number {
  return Math.min(1, Math.log10(count) / 2)
}

/**
 * Breakpoints de count para las expresiones `step`. El primer valor de un
 * `step` aplica a count < STOPS[0]; usamos 2 como base porque un cluster
 * MapLibre existe desde 2 puntos.
 */
const CLUSTER_BASE_COUNT = 2
const CLUSTER_COUNT_STOPS: readonly number[] = [10, 100, 750]

/**
 * Construye una expresión `['step', ['get','point_count'], base, stop, val, …]`
 * evaluando `fn` (color o radio) en cada breakpoint, de modo que en cada stop el
 * valor coincide con el que producía el motor anterior.
 */
function stepByPointCount(
  fn: (count: number) => number | string,
): MapLibreExpression {
  const expr: unknown[] = ['step', ['get', 'point_count'], fn(CLUSTER_BASE_COUNT)]
  for (const stop of CLUSTER_COUNT_STOPS) {
    expr.push(stop, fn(stop))
  }
  return expr
}

/**
 * Layers de cluster: [circle, symbol].
 * - circle: `circle-radius` y `circle-color` por `['step', ['get','point_count'], …]`,
 *   los colores tomados de `palette` vía sampleRamp (misma semántica que
 *   la simbología anterior:362).
 * - symbol: `text-field` = `['get','point_count_abbreviated']`.
 *
 * `sourceId` es el id del source creado con `clusterSourceSpec`.
 */
export function clusterLayerSpecs(
  sourceId: string,
  palette: string[],
): [CircleLayerSpec, SymbolLayerSpec] {
  const ramp = palette.length > 0 ? palette : [...DEFAULT_VIRIDIS]

  const circle: CircleLayerSpec = {
    id: `${sourceId}-clusters`,
    type: 'circle',
    source: sourceId,
    filter: ['has', 'point_count'],
    paint: {
      // Radio = badge/2 (el badge de el motor anterior es el diámetro del SVG circular).
      'circle-radius': stepByPointCount((c) => Math.round(clusterBadgeSize(c) / 2)),
      'circle-color': stepByPointCount((c) => sampleRamp(ramp, clusterColorT(c))),
      // Contorno blanco + grosor 2, igual que el badge del motor anterior
      // (la simbología anterior:367-368).
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2,
    },
  }

  const symbol: SymbolLayerSpec = {
    id: `${sourceId}-cluster-count`,
    type: 'symbol',
    source: sourceId,
    filter: ['has', 'point_count'],
    layout: {
      'text-field': ['get', 'point_count_abbreviated'],
      'text-font': ['literal', ['Open Sans Bold', 'Arial Unicode MS Bold']],
      // Tamaño de texto ~0.4 del badge (la simbología anterior:379), escalado por count.
      'text-size': stepByPointCount((c) => Math.round(clusterBadgeSize(c) * 0.4)),
      'text-allow-overlap': true,
      'text-ignore-placement': true,
    },
    paint: {
      // Fill blanco + halo negro (la simbología anterior:380-383).
      'text-color': '#ffffff',
      'text-halo-color': '#000000',
      'text-halo-width': 1.5,
    },
  }

  return [circle, symbol]
}

// ──────────────────────────────────────────────────────────────────────────
// HEATMAP — layer heatmap nativo con rampa derivada de la PALETA del backend.
// ──────────────────────────────────────────────────────────────────────────

export interface HeatmapLayerOpts {
  /**
   * Paleta (bajo→alto). La rampa `heatmap-color` se deriva de aquí — NUNCA de
   * un viridis hardcodeado (bug conocido). Toma la paleta del backend con
   * `paletteFrom(symb)`.
   */
  palette: string[]
  /** Radio del kernel en px. Default toma symb.heatmap_radius o 30. */
  radius?: number
  /** Intensidad global. Default 1. */
  intensity?: number
  /** Opacidad del layer. Default 0.8. */
  opacity?: number
  /**
   * Campo numérico para ponderar cada punto (symb.heatmap_intensity_field). Si
   * se omite, cada punto pesa 1 (equivalente a contar puntos, como el hex grid
   * del motor anterior cuando no hay intensityField — la simbología anterior:247).
   */
  intensityField?: string | null
  /**
   * Valor del campo que corresponde a peso 1 (para normalizar `heatmap-weight`).
   * Sólo se usa si hay intensityField. Default 100.
   */
  weightMax?: number
  /** Zoom máximo al que se muestra el heatmap. */
  maxzoom?: number
}

/**
 * Construye la expresión `heatmap-color` a partir de la paleta: transparente en
 * densidad 0 y luego la rampa repartida uniformemente hasta densidad 1. Los
 * colores salen de `palette` (no de viridis fijo).
 */
export function heatmapColorExpression(palette: string[]): MapLibreExpression {
  const ramp = palette.length > 0 ? palette : [...DEFAULT_VIRIDIS]
  const expr: unknown[] = ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(0, 0, 0, 0)']
  // La rampa arranca en densidad 0.1 para que el fondo quede limpio; el primer
  // color de la paleta entra ahí y el último en densidad 1.
  const start = 0.1
  const n = ramp.length
  for (let i = 0; i < n; i++) {
    const d = n === 1 ? 1 : start + (1 - start) * (i / (n - 1))
    expr.push(Number(d.toFixed(4)), ramp[i])
  }
  return expr
}

/**
 * Layer heatmap nativo de MapLibre. `heatmap-color` respeta la PALETA de opts.
 * `sourceId` apunta al source geojson de puntos (sin cluster).
 */
export function heatmapLayerSpec(
  sourceId: string,
  opts: HeatmapLayerOpts,
): HeatmapLayerSpec {
  const {
    palette,
    radius = 30,
    intensity = 1,
    opacity = 0.8,
    intensityField = null,
    weightMax = 100,
    maxzoom,
  } = opts

  // Peso por punto: 1 por defecto (cuenta puntos); si hay campo de intensidad,
  // normaliza get(field) al rango [0, weightMax] → [0, 1].
  const weight: number | MapLibreExpression = intensityField
    ? [
        'interpolate',
        ['linear'],
        ['to-number', ['get', intensityField], 0],
        0, 0,
        weightMax, 1,
      ]
    : 1

  const paint: Record<string, number | string | MapLibreExpression> = {
    'heatmap-weight': weight,
    'heatmap-intensity': intensity,
    'heatmap-color': heatmapColorExpression(palette),
    'heatmap-radius': radius,
    'heatmap-opacity': opacity,
  }

  const spec: HeatmapLayerSpec = {
    id: `${sourceId}-heatmap`,
    type: 'heatmap',
    source: sourceId,
    paint,
  }
  if (typeof maxzoom === 'number') spec.maxzoom = maxzoom
  return spec
}
