/**
 * MAP-SEAM — oráculo de paridad para los E2E de la migración (FND-E2E-HARNESS).
 *
 * AMBOS motores de mapa publican el MISMO shape en `window.__mapTestState`, de
 * modo que un test de Playwright compara el estado de render entre motores por
 * fase (mismo featureCount, mismo rendererKind) sin depender de detalles
 * internos de cada SDK. Este archivo es engine-agnóstico y sobrevive la migración.
 */
/** Sonda por-capa (FRT-04): permite a un E2E afirmar QUÉ capa fue re-estilada
 * sin depender de nombres generados por el LLM — el discriminador robusto es el
 * featureCount (p.ej. lotes=40 vs construcciones=30) + color/rendererKind. */
export interface LayerProbe {
  id: string
  name: string
  featureCount: number
  /** Color representativo (symbology.fill/stroke/marker o el default del store). */
  color: string
  /** symbology_type de la capa (single_symbol/unique_values/heatmap…). */
  rendererKind: string | null
  /** FH.2: cuántos elementos tiene seleccionados. */
  seleccionados?: number
  /** F4: tipo de capa (vector-geojson, vector-mvt, raster-xyz…). El orden de
   * `layers` es el orden de dibujo (índice 0 = abajo). */
  kind?: string
  opacity?: number
  visible?: boolean
}

export interface MapTestState {
  engine: 'maplibre'
  /** Total de features actualmente cargadas/renderizadas en el mapa. */
  featureCount: number
  /** Clase de renderer del último layer (single/heatmap/cluster/graduated…). */
  rendererKind: string | null
  /** Sonda por-capa (id, nombre, featureCount, color, rendererKind). */
  layers: LayerProbe[]
}

const KEY = '__mapTestState'

export function setMapTestState(patch: Partial<MapTestState>): void {
  if (typeof window === 'undefined') return
  const w = window as unknown as Record<string, MapTestState | undefined>
  w[KEY] = {
    engine: 'maplibre',
    featureCount: 0,
    rendererKind: null,
    layers: [],
    ...(w[KEY] ?? {}),
    ...patch,
  }
}

export function getMapTestState(): MapTestState | undefined {
  if (typeof window === 'undefined') return undefined
  return (window as unknown as Record<string, MapTestState | undefined>)[KEY]
}
