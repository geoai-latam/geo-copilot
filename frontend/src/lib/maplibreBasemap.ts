/**
 * MAP-SETUP-BASEMAPS — normaliza un BaseMapConfig del store a los tiles raster
 * que espera MapLibre GL.
 *
 * Los basemaps del store vienen en 3 formatos distintos:
 *   - base sin plantilla (OSM):        'https://tile.openstreetmap.org/'
 *   - plantilla ArcGIS {z}/{y}/{x}:    '.../MapServer/tile/{z}/{y}/{x}'
 *   - plantilla estándar {z}/{x}/{y}:  'https://basemaps.cartocdn.com/.../{z}/{x}/{y}.png'
 * y algunos con subdominio {s} ('tile-{s}.openstreetmap.fr').
 *
 * MapLibre sustituye {z}/{x}/{y} donde aparezcan (así el orden {z}/{y}/{x} de
 * ArcGIS funciona directo) pero NO expande {s}: lo expandimos a a/b/c.
 */
import type { BaseMapConfig } from '@/stores/mapStore'

export interface RasterSpec {
  tiles: string[]
  attribution: string
  maxzoom: number
}

export function toTileTemplate(url: string): string {
  if (url.includes('{z}')) return url // ya es plantilla (ArcGIS / Carto)
  const base = url.endsWith('/') ? url : `${url}/`
  return `${base}{z}/{x}/{y}.png` // base tipo OSM
}

export function expandSubdomains(url: string): string[] {
  if (!url.includes('{s}')) return [url]
  return ['a', 'b', 'c'].map((s) => url.replace('{s}', s))
}

export function basemapRasterSpec(cfg: BaseMapConfig): RasterSpec {
  return {
    tiles: expandSubdomains(toTileTemplate(cfg.url)),
    attribution: cfg.attribution ?? '',
    maxzoom: cfg.maxZoom ?? 19,
  }
}
