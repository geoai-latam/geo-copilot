/**
 * Renderers vectoriales: `vector-geojson` (features en memoria) y `vector-mvt`
 * (teselas del workspace o de una tabla de la BD). Comparten el estilo: la
 * misma simbología (single/unique/graduada/símbolos/heatmap/cluster) se dibuja
 * igual venga de donde venga la geometría.
 */
import type { MapLayer } from '@/stores/mapStore'
import { baseLayerSpecs, boundsOf, geoJsonSourceSpec, layerGeometryKind } from '@/lib/maplibreGeoJson'
import {
  circleRadiusExpression,
  extractClassification,
  fillColorExpression,
  lineColorExpression,
} from '@/lib/maplibreSymbology'
import { clusterLayerSpecs, clusterSourceSpec, heatmapLayerSpec, paletteFrom, toCentroidPoints } from '@/lib/maplibreCluster'
import { cumpleTodas, filtroDeCapa, filtroResaltado, yTambien } from '@/lib/seleccion'
import type { LegendSpec, MapSpecs, OpacidadPaint, PanelRow, RenderCtx, Renderer, StyleLayerSpec } from './types'

const fmt = (n: number) => n.toLocaleString('es-CO')

function capaDeEtiquetas(layer: MapLayer, sourceLayer: Record<string, string>): StyleLayerSpec | null {
  const tipo = layer.symbology?.symbology_type
  const campo = tipo === 'cluster' || tipo === 'heatmap' ? null : layer.labelField
  if (!campo) return null
  return {
    id: `${layer.id}-label`, type: 'symbol', source: layer.id, ...sourceLayer,
    layout: {
      'text-field': ['get', campo], 'text-font': ['Open Sans Bold'], 'text-size': 11,
      'text-anchor': 'center',
      'symbol-placement': layerGeometryKind(layer as never) === 'line' ? 'line' : 'point',
    },
    paint: { 'text-color': '#101914', 'text-halo-color': '#ffffff', 'text-halo-width': 1.6 },
  }
}

/**
 * FH.2 — resaltado de la selección, igual para todos los tipos vectoriales. Las
 * capas existen siempre; lo que cambia con la selección es su FILTRO (`setFilter`
 * desde el mapa), así seleccionar no reconstruye la capa.
 */
export const RESALTADO = '#ffb000'
export function idsDeResaltado(layerId: string): string[] {
  return [`${layerId}-sel-fill`, `${layerId}-sel-line`, `${layerId}-sel-circle`]
}
function capasDeResaltado(layer: MapLayer, sourceLayer: Record<string, string>): StyleLayerSpec[] {
  const geom = layer.tiles ? (layer.tiles.geometryType ?? '') : layerGeometryKind(layer as never)
  const esPunto = /point/i.test(String(geom))
  const esLinea = /line/i.test(String(geom))
  const filtro = filtroResaltado(layer)
  const [fill, line, circle] = idsDeResaltado(layer.id)
  const capas: StyleLayerSpec[] = []
  if (!esPunto && !esLinea) {
    capas.push({ id: fill, type: 'fill', source: layer.id, ...sourceLayer, filter: filtro,
                 paint: { 'fill-color': RESALTADO, 'fill-opacity': 0.35 } })
  }
  if (!esPunto) {
    capas.push({ id: line, type: 'line', source: layer.id, ...sourceLayer, filter: filtro,
                 paint: { 'line-color': RESALTADO, 'line-width': 3 } })
  } else {
    capas.push({ id: circle, type: 'circle', source: layer.id, ...sourceLayer, filter: filtro,
                 paint: { 'circle-radius': 8, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': RESALTADO,
                          'circle-stroke-width': 3 } })
  }
  return capas
}

function specsVectoriales(layer: MapLayer, ctx: RenderCtx): MapSpecs { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const teselada = layer.kind === 'vector-mvt' && !!layer.tiles
  const sourceLayer: Record<string, string> = teselada ? { 'source-layer': layer.tiles!.sourceLayer } : {}
  // Sobre teselas no hay cluster (necesita un source GeoJSON): se dibuja normal.
  const tipo = teselada && layer.symbology?.symbology_type === 'cluster' ? undefined : layer.symbology?.symbology_type
  const fuenteTeselas = teselada
    ? { type: 'vector', tiles: [`${ctx.origin}${layer.tiles!.url}`], minzoom: 0, maxzoom: 16 }
    : null
  // Cluster y heatmap son de PUNTOS: sobre polígonos/líneas se usan centroides
  // (degradación honesta: se ve algo en vez de nada).
  // FH.5: cluster y heatmap agregan en la fuente: su filtro va sobre los datos (no
  // necesitan ids). El resto filtra capa a capa, sin tocar los ids de la selección.
  const filtro = filtroDeCapa(layer.filtro)
  const visibles = filtro && !teselada && (tipo === 'cluster' || tipo === 'heatmap')
    ? { ...layer.data, features: (layer.data.features ?? []).filter((f) =>
        cumpleTodas(layer.filtro, (f.properties ?? {}) as Record<string, unknown>)) }
    : layer.data
  const puntos =
    !teselada && (tipo === 'cluster' || tipo === 'heatmap') && layerGeometryKind(layer as never) !== 'point'
      ? toCentroidPoints(visibles as never)
      : visibles

  if (tipo === 'cluster') {
    const [circulo, texto] = clusterLayerSpecs(layer.id, paletteFrom(layer.symbology))
    return {
      source: clusterSourceSpec(puntos as never) as never,
      layers: [
        circulo as StyleLayerSpec,
        texto as StyleLayerSpec,
        {
          id: `${layer.id}-unclustered`, type: 'circle', source: layer.id,
          filter: ['!', ['has', 'point_count']],
          paint: { 'circle-color': layer.color, 'circle-radius': 5, 'circle-stroke-color': '#ffffff',
                   'circle-stroke-width': 1 },
        },
      ],
    }
  }
  if (tipo === 'heatmap') {
    const hm = heatmapLayerSpec(layer.id, { palette: paletteFrom(layer.symbology) })
    return {
      source: (fuenteTeselas ?? geoJsonSourceSpec(puntos as never)) as never,
      layers: [{ ...(hm as StyleLayerSpec), ...sourceLayer,
                 ...(teselada && filtro ? { filter: yTambien((hm as StyleLayerSpec).filter, filtro) } : {}) }],
    }
  }

  const clasificacion = extractClassification(layer.symbology)
  const tamano = layer.symbology?.marker?.size ?? 10
  const capas: StyleLayerSpec[] = baseLayerSpecs(layer.id, layer as never).map((spec) => {
    const paint: Record<string, unknown> = { ...spec.paint }
    if (clasificacion) {
      if (spec.type === 'fill') paint['fill-color'] = fillColorExpression(layer.symbology, layer.color)
      if (spec.type === 'line') paint['line-color'] = lineColorExpression(layer.symbology, layer.color)
      if (spec.type === 'circle') {
        paint['circle-color'] = fillColorExpression(layer.symbology, layer.color)
        // circleRadiusExpression devuelve el DIÁMETRO (paridad con el motor anterior) → /2.
        if (tipo === 'graduated_symbols') paint['circle-radius'] = ['/', circleRadiusExpression(layer.symbology, tamano), 2]
      }
    }
    const base = spec as StyleLayerSpec
    return { ...base, ...sourceLayer, paint, ...(filtro ? { filter: yTambien(base.filter, filtro) } : {}) }
  })
  const etiquetasBase = capaDeEtiquetas(layer, sourceLayer)
  const etiquetas = etiquetasBase && filtro ? { ...etiquetasBase, filter: filtro } : etiquetasBase
  return {
    source: (fuenteTeselas ?? geoJsonSourceSpec(layer.data as never)) as never,
    layers: [...capas, ...capasDeResaltado(layer, sourceLayer), ...(etiquetas ? [etiquetas] : [])],
  }
}

function opacidad(layer: MapLayer): OpacidadPaint[] {
  const f = layer.opacity ?? 1
  return [
    ['fill', 'fill-opacity', (layer.symbology?.fill?.opacity ?? 0.4) * f],
    ['line', 'line-opacity', (layer.symbology?.stroke?.opacity ?? 1) * f],
    ['circle', 'circle-opacity', (layer.symbology?.marker?.opacity ?? 1) * f],
    ['heatmap', 'heatmap-opacity', f],
    ['symbol', 'text-opacity', f],
  ]
}

function leyenda(layer: MapLayer): LegendSpec | null {
  const s = layer.symbology
  if (!s) return null
  const items = (s.class_breaks ?? []).map((b) => ({ label: b.label, color: b.color, count: b.count }))
  return { title: s.layer_title || layer.name, ...(items.length ? { items } : {}) }
}

/** La muestra de color del panel: la rampa de la capa si está clasificada (V5 FH.6: con
 * la rampa «Reds» el panel seguía mostrando el azul de antes). */
export function muestraDeColor(layer: MapLayer): string {
  const s = layer.symbology
  const colores = s?.symbology_type === 'heatmap' ? paletteFrom(s) : (s?.class_breaks ?? []).map((b) => b.color)
  const distintos = [...new Set(colores.filter(Boolean))]
  if (distintos.length > 1) return `linear-gradient(to right, ${distintos.join(', ')})`
  return distintos[0] ?? s?.fill?.color ?? s?.marker?.color ?? layer.color
}

function fila(layer: MapLayer): PanelRow {
  const teselada = layer.kind === 'vector-mvt'
  const n = teselada ? layer.tiles?.featureCount ?? 0 : layer.data.features?.length ?? 0
  return {
    sub: teselada ? (n ? `${fmt(n)} features · teselas` : 'teselas') : `${fmt(n)} features`,
    swatch: muestraDeColor(layer),
    labelFields: teselada ? layer.tiles?.fields ?? [] : Object.keys(layer.data.features?.[0]?.properties ?? {}),
  }
}

const base = {
  buildSpecs: specsVectoriales,
  opacityPaint: opacidad,
  legend: leyenda,
  panelRow: fila,
  pickable: true,
}

export const vectorGeojson: Renderer = {
  ...base,
  kind: 'vector-geojson',
  bounds: (layer) => boundsOf(layer.data as never),
  featureCount: (layer) => layer.data.features?.length ?? 0,
}

export const vectorMvt: Renderer = {
  ...base,
  kind: 'vector-mvt',
  bounds: (layer) => layer.tiles?.bbox ?? null,
  featureCount: (layer) => layer.tiles?.featureCount ?? layer.featureCount ?? 0,
}
