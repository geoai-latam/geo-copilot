/**
 * Renderers raster: `raster-xyz` (plantilla {z}/{x}/{y}, p. ej. las teselas de
 * un servidor MCP vía /api/v1/proxy/mcp/…), `arcgis-image` (MapServer /
 * ImageServer pedidos por extensión a través del proxy del backend) y `wms`.
 */
import type { MapLayer } from '@/stores/mapStore'
import { imageryRasterSource } from '@/lib/maplibreImagery'
import type { BBox, LegendSpec, MapSpecs, OpacidadPaint, PanelRow, Renderer, StyleLayerSpec } from './types'

function capaRaster(layer: MapLayer): StyleLayerSpec {
  return { id: `${layer.id}-raster`, type: 'raster', source: layer.id, paint: {} }
}

function extension(layer: MapLayer): BBox | null {
  const e = layer.extent
  return e ? [e.xmin, e.ymin, e.xmax, e.ymax] : null
}

function leyenda(layer: MapLayer): LegendSpec | null {
  const l = layer.legend
  if (!l || typeof l.min !== 'number' || typeof l.max !== 'number') return null
  return { title: layer.name, ramp: { field: l.field, min: l.min, max: l.max, nota: l.nota } }
}

const fila =
  (sub: string) =>
  (layer: MapLayer): PanelRow => ({ sub, swatch: layer.color, labelFields: [] })

const base = {
  opacityPaint: (layer: MapLayer): OpacidadPaint[] => [
    ['raster', 'raster-opacity', layer.opacity ?? 1],
  ],
  bounds: extension,
  legend: leyenda,
  featureCount: () => 0,
  pickable: false,
}

export const rasterXyz: Renderer = {
  ...base,
  kind: 'raster-xyz',
  buildSpecs: (layer): MapSpecs => ({
    source: { type: 'raster', tiles: [layer.url ?? ''], tileSize: 256 },
    layers: [capaRaster(layer)],
  }),
  panelRow: fila('imagen · teselas'),
}

export const arcgisImage: Renderer = {
  ...base,
  kind: 'arcgis-image',
  buildSpecs: (layer, ctx): MapSpecs => ({
    source: imageryRasterSource({ service_url: layer.url ?? '' }, ctx.arcgisProxyBase) as never,
    layers: [capaRaster(layer)],
  }),
  panelRow: fila('imagen · ArcGIS'),
}

/** GetMap WMS 1.3.0 por tesela (MapLibre sustituye {bbox-epsg-3857}). */
export function wmsTileUrl(url: string, layers: string): string {
  const sep = url.includes('?') ? '&' : '?'
  return (
    `${url}${sep}SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=${encodeURIComponent(layers)}` +
    '&STYLES=&FORMAT=image/png&TRANSPARENT=true&CRS=EPSG:3857&WIDTH=256&HEIGHT=256&BBOX={bbox-epsg-3857}'
  )
}

export const wms: Renderer = {
  ...base,
  kind: 'wms',
  buildSpecs: (layer): MapSpecs => ({
    source: { type: 'raster', tiles: [wmsTileUrl(layer.url ?? '', layer.wmsLayers ?? '')], tileSize: 256 },
    layers: [capaRaster(layer)],
  }),
  panelRow: fila('imagen · WMS'),
}
