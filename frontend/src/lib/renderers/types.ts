/**
 * Registro de renderers (F4, S4.2): un tipo de capa = un archivo.
 *
 * Cada renderer traduce una capa del store a specs de MapLibre con funciones
 * PURAS (sin WebGL: se prueban en vitest) y declara lo que el resto de la app
 * necesita saber de ese tipo: su leyenda, su fila en el panel de capas, su
 * extensión y cuántas features tiene en memoria. `MapLibreMap` tiene un solo
 * `sync` que aplica estos specs para cualquier tipo.
 *
 * Para añadir un tipo nuevo: un archivo aquí + su test + una entrada en `index.ts`.
 */
import type { Map as MapLibreMap } from 'maplibre-gl'
import type { MapLayer, RendererKind } from '@/stores/mapStore'

export type BBox = [number, number, number, number]

/** Spec de capa de estilo de MapLibre (JSON plano; `source` = id de la capa). */
export interface StyleLayerSpec {
  id: string
  type: 'fill' | 'line' | 'circle' | 'symbol' | 'heatmap' | 'raster'
  source: string
  'source-layer'?: string
  filter?: unknown
  layout?: Record<string, unknown>
  paint?: Record<string, unknown>
}

export interface MapSpecs {
  source: Record<string, unknown>
  layers: StyleLayerSpec[]
}

export interface RenderCtx {
  /** Origen absoluto: el worker de MapLibre no resuelve rutas relativas en teselas vectoriales. */
  origin: string
  /** Proxy del backend para servicios ArcGIS externos (CORS). */
  arcgisProxyBase: string
}

export interface LegendSpec {
  title: string
  /** Clases discretas (vectoriales clasificadas). */
  items?: { label: string; color: string; count?: number }[]
  /** Rampa continua (rasters de índices). */
  ramp?: { field?: string; min: number; max: number; nota?: string }
}

export interface PanelRow {
  /** Texto bajo el nombre ("41 033 features", "teselas · imagen"). */
  sub: string
  swatch: string
  /** ¿Tiene campos para etiquetar? (vectoriales inline). */
  labelFields: string[]
}

/** Una propiedad de pintura de MapLibre (maplibre-gl 6 tipa `setPaintProperty` por nombre). */
export type PropiedadPintura = Parameters<MapLibreMap['setPaintProperty']>[1]
/** [tipo de capa de estilo, propiedad, valor] para aplicar la opacidad de una capa. */
export type OpacidadPaint = [StyleLayerSpec['type'], PropiedadPintura, number]

export interface Renderer {
  kind: RendererKind
  buildSpecs(layer: MapLayer, ctx: RenderCtx): MapSpecs
  /** [tipo de capa de estilo, propiedad, valor] para aplicar la opacidad de la capa. */
  opacityPaint(layer: MapLayer): OpacidadPaint[]
  bounds(layer: MapLayer): BBox | null
  legend(layer: MapLayer): LegendSpec | null
  panelRow(layer: MapLayer): PanelRow
  /** Features en memoria (oráculo de los E2E y contador del panel). */
  featureCount(layer: MapLayer): number
  /** ¿Sus features se pueden identificar con queryRenderedFeatures? */
  pickable: boolean
}

/** Qué cambia la GEOMETRÍA o el estilo de una capa (si cambia, se reconstruye). */
export function firmaDeCapa(layer: MapLayer): unknown[] {
  return [layer.kind, layer.data, layer.symbology, layer.tiles?.url, layer.url, layer.wmsLayers, layer.labelField,
          JSON.stringify(layer.filtro ?? null)]
}
