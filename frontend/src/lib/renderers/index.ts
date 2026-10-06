/**
 * Registro de renderers por `kind` de capa (F4, S4.2). Ver `types.ts`.
 */
import type { RendererKind } from '@/stores/mapStore'
import { arcgisImage, rasterXyz, wms } from './raster'
import type { Renderer } from './types'
import { vectorGeojson, vectorMvt } from './vector'

export const RENDERERS: Record<RendererKind, Renderer> = {
  'vector-geojson': vectorGeojson,
  'vector-mvt': vectorMvt,
  'raster-xyz': rasterXyz,
  'arcgis-image': arcgisImage,
  wms,
}

export const rendererDe = (kind: RendererKind): Renderer => RENDERERS[kind]

export * from './types'
