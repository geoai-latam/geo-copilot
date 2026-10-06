/**
 * Fase A — map_context.
 *
 * Snapshot LIGERO del estado del mapa/UI que se adjunta a cada consulta para
 * que el agente entienda "esta capa", "el predio seleccionado", "esta zona".
 * Solo metadatos + properties de la feature seleccionada; la geometría
 * completa se adjunta únicamente para la capa ACTIVA (la última cargada),
 * para poder operar sobre ella (buffer, área, etc.) — el backend la valida
 * con el tope de features (S4).
 */
import type { Mencion } from '@/lib/menciones'
import type { Predicado } from '@/contracts'
import { accionesDesdeUltimoTurno } from '@/lib/operaciones'
import { getViewBounds } from '@/lib/mapViewport'
import { useVistas } from '@/lib/vistas'
import { useComparacion } from '@/lib/comparacion'
import { fechasDeSerie, useTiempo } from '@/lib/tiempo'
import { esRaster, useMapStore, type MapLayer } from '@/stores/mapStore'
import { useResultsStore } from '@/stores/resultsStore'
import type { GeoJSONFeatureCollection } from '@/types'

export interface MapLayerContext {
  id: string
  name: string
  source: string
  geometry_type: string | null
  feature_count: number
  fields: string[]
  visible: boolean
  is_active: boolean
  data?: GeoJSONFeatureCollection
  /** T2.0a: con él no viaja `data`: el backend lee el workspace de la sesión. */
  dataset_id?: string
  /** S4.4: la capa POR REFERENCIA, también raster y MVT (antes invisibles al agente). */
  kind: string
  url?: string
  origin?: { capability: string; arguments?: Record<string, unknown> }
  legend?: { field?: string; min?: number; max?: number }
  bbox?: [number, number, number, number]
  /** FH.1: lo que el usuario VE de la capa: su opacidad, su estilo y sus etiquetas. */
  opacity?: number
  style?: EstiloResumido
  label_field?: string | null
  /** FH.2: lo seleccionado: ids (en memoria: índice; en teselas: fid) o la condición. */
  seleccion?: { ids?: number[]; where?: Predicado; count: number; origin: string }
  /** FH.5: filtro de la capa (la capa ES ese subconjunto) y cuántos quedan. */
  filtro?: Predicado[]
  filtro_count?: number
  /** FH.10: el instante que retrata (capa de una serie temporal). */
  fecha?: string
}

/** Resumen del estilo para el agente (sin repetir el StyleSpec entero). */
export interface EstiloResumido {
  symbology_type?: string
  classification_field?: string | null
  class_breaks?: { label: string; color: string }[]
  fill_color?: string
  /** FH.6: el resto del diseño y lo que el usuario fijó a mano. */
  classification_method?: string | null
  num_classes?: number | null
  color_scheme?: string | null
  pinned?: string[]
}

export interface SelectedFeatureContext {
  layer_id: string | null
  properties: Record<string, unknown>
}

export interface ViewportContext {
  bbox: [number, number, number, number] | null
  zoom: number | null
  crs: string
}

export interface MapContext {
  layers: MapLayerContext[]
  selected_feature: SelectedFeatureContext | null
  viewport: ViewportContext | null
  /** El «aquí»: último punto donde el usuario hizo click en el mapa. */
  clicked_point: { lon: number; lat: number } | null
  active_visualization: { type: string; query_id?: string; fields?: string[] } | null
  basemap: string | null
  /** FH.1: operaciones en el mapa desde la última consulta (usuario o agente, y si se deshicieron). */
  acciones: ReturnType<typeof accionesDesdeUltimoTurno>
  /** FH.4: lo que el usuario mencionó con `@` en este mensaje. */
  menciones?: Mencion[]
  /** FH.4: el chip de la selección estaba a la vista al enviar y el usuario lo dejó. */
  alcance_seleccion?: boolean
  /** FH.4: el usuario QUITÓ el chip: cuál era la selección (ella no viaja). */
  seleccion_excluida?: { layer_id: string; layer_name: string; count: number | null }
  respuesta_mapa?: { modo: string; pedido: string; layer_id?: string | null; cancelado?: boolean }
  /** FH.10: vistas guardadas (el agente va a una con zoom_to bbox). */
  vistas?: { nombre: string; bbox: [number, number, number, number] }[]
  comparacion?: { left: string; right: string }
  serie_tiempo?: { fechas: string[]; actual: string | null }
}

/** FH.4: el alcance de UN mensaje: sus menciones y si la selección entra o no. */
export interface Alcance {
  menciones?: Mencion[]
  /** El usuario quitó el chip de la selección: este mensaje va sin ella (la capa entera). */
  sinSeleccion?: boolean
  /** FH.9: este mensaje responde en el mapa a lo que pidió el agente. */
  respuestaMapa?: { modo: string; pedido: string; layer_id?: string | null; cancelado?: boolean }
  /** FH.9: cómo se ve el mensaje en el chat (la consulta que viaja es la original). */
  etiqueta?: string
}

function estiloDe(l: MapLayer): EstiloResumido | undefined {
  const s = l.symbology
  if (!s) return undefined
  const clases = (s.class_breaks ?? []).slice(0, 12).map((c) => ({ label: String(c.label), color: String(c.color) }))
  return {
    symbology_type: s.symbology_type,
    classification_field: s.classification_field ?? null,
    ...(clases.length ? { class_breaks: clases } : {}),
    ...(s.fill?.color ? { fill_color: s.fill.color } : {}),
    ...(s.classification_method ? { classification_method: s.classification_method } : {}),
    ...(s.num_classes || clases.length ? { num_classes: s.num_classes ?? clases.length } : {}),
    ...(s.color_scheme ? { color_scheme: s.color_scheme } : {}),
    ...(s.pinned?.length ? { pinned: s.pinned } : {}),
  }
}

function firstGeometryType(data: GeoJSONFeatureCollection | undefined): string | null {
  return data?.features?.[0]?.geometry?.type ?? null
}

function firstFeatureFields(data: GeoJSONFeatureCollection | undefined): string[] {
  const props = data?.features?.[0]?.properties
  return props ? Object.keys(props) : []
}

/**
 * Tamaño aproximado del geojson de una capa en el cuerpo de la petición.
 *
 * Se mide en unidades UTF-16 (`String.length`), no en bytes UTF-8: para un
 * GeoJSON —dominado por dígitos, comas y corchetes— la diferencia es de
 * decimales, y el presupuesto de 8 MB contra el límite de 50 MB de nginx deja
 * de sobra para absorberla. La alternativa (`TextEncoder`) obligaría a
 * materializar un array de decenas de MB solo para descartarlo.
 *
 * Una capa que ni siquiera se puede serializar (ciclo, geometría corrupta) se
 * declara infinita: no cabe en ningún presupuesto y viaja solo con metadatos.
 */
function payloadSize(data: GeoJSONFeatureCollection | undefined): number {
  if (!data) return 0
  try {
    return JSON.stringify(data).length
  } catch {
    return Number.POSITIVE_INFINITY
  }
}

/** Extensión de la capa sin tocar sus features (raster: su extent; MVT: la del dataset). */
function bboxDe(l: MapLayer): [number, number, number, number] | undefined {
  if (l.extent) return [l.extent.xmin, l.extent.ymin, l.extent.xmax, l.extent.ymax]
  return l.tiles?.bbox ?? undefined
}

/** Lo que el agente necesita de una capa para razonar sobre ella, sin sus datos. */
function referenciaDe(l: MapLayer): Pick<MapLayerContext, 'kind' | 'url' | 'origin' | 'legend' | 'bbox'> {
  const ley = l.legend as { field?: string; min?: number; max?: number } | null | undefined
  const bbox = bboxDe(l)
  return {
    kind: l.kind,
    ...(esRaster(l) && l.url ? { url: l.url } : {}),
    ...(l.origen ? { origin: l.origen } : {}),
    ...(ley && (ley.min !== undefined || ley.field) ? { legend: { field: ley.field, min: ley.min, max: ley.max } } : {}),
    ...(bbox ? { bbox } : {}),
  }
}

export function buildMapContext(alcance: Alcance = {}): MapContext { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const m = useMapStore.getState()
  const r = useResultsStore.getState()

  const layers = m.layers
  // FH.4: la capa con selección (la del chip de alcance)
  const conSeleccion = layers.find((l) => l.seleccion && (l.seleccion.ids?.length || l.seleccion.where))
  // Heurística v1: la última capa añadida es la "activa"/enfocada.
  const activeId = layers.length ? layers[layers.length - 1].id : null

  const sel = m.selectedFeature as
    | { properties?: Record<string, unknown>; layerName?: string }
    | null

  // FRT-04: adjuntar el geojson de TODAS las capas visibles (no sólo la activa)
  // para que el backend pueda operar sobre la capa que el usuario NOMBRE. Tope
  // TOTAL de features para no inflar el payload — se prioriza la activa y luego
  // el resto de visibles mientras quepan; las que excedan van con metadatos
  // (nombre/campos) para que el LLM igual las conozca, pero sin data.
  const FEATURE_BUDGET = 20_000
  // Auditoría 2026-09-08 §5 (5): contar FEATURES no acota el tamaño del cuerpo.
  // 20.000 predios catastrales (polígonos de decenas de vértices y una docena
  // de atributos) pasan de los 50 MB de `client_max_body_size` de nginx
  // (docker/nginx.frontend.conf:75) y el POST muere en 413 — en TODA consulta
  // posterior, no solo en la que cargó la capa. El presupuesto real es de
  // bytes; el de features se queda como corte barato previo.
  const BYTE_BUDGET = 8 * 1024 * 1024
  const includeData = new Set<string>()
  let usedFeatures = 0
  let usedBytes = 0
  const ordered = [...layers].sort((a, b) =>
    a.id === activeId ? -1 : b.id === activeId ? 1 : 0,
  )
  for (const l of ordered) {
    if (!l.visible) continue
    // T2.0a: las del workspace van por referencia, sin gastar presupuesto.
    if (l.datasetId) continue
    // S4.4: solo una vectorial en memoria tiene features que mandar (raster y MVT no).
    if (l.kind !== 'vector-geojson') continue
    const n = l.featureCount || 0
    if (usedFeatures + n > FEATURE_BUDGET) continue
    const bytes = payloadSize(l.data)
    if (usedBytes + bytes > BYTE_BUDGET) continue
    includeData.add(l.id)
    usedFeatures += n
    usedBytes += bytes
  }

  return {
    layers: layers.map((l) => ({ // eslint-disable-line complexity -- deuda congelada (F1): ESLint 10 suma `?.` y defaults; partir, no subir
      id: l.id,
      name: l.name,
      source: 'map',
      geometry_type: l.tiles ? l.tiles.geometryType : firstGeometryType(l.data),
      feature_count: l.featureCount,
      fields: l.tiles ? l.tiles.fields : firstFeatureFields(l.data),
      visible: l.visible,
      is_active: l.id === activeId,
      // FRT-04: data de las capas visibles dentro del presupuesto (activa primero).
      ...(includeData.has(l.id) ? { data: l.data } : {}),
      ...(l.datasetId ? { dataset_id: l.datasetId } : {}),
      ...referenciaDe(l),
      opacity: l.opacity ?? 1,
      ...(estiloDe(l) ? { style: estiloDe(l) } : {}),
      ...(l.labelField ? { label_field: l.labelField } : {}),
      ...(l.fecha ? { fecha: l.fecha } : {}),
      ...(l.filtro?.length ? { filtro: l.filtro, ...(l.filtroCount != null ? { filtro_count: l.filtroCount } : {}) } : {}),
      ...(l.seleccion && !alcance.sinSeleccion
        ? { seleccion: { ...(l.seleccion.ids ? { ids: l.seleccion.ids.slice(0, 5000) } : {}),
                         ...(l.seleccion.where ? { where: l.seleccion.where } : {}),
                         count: l.seleccion.count, origin: l.seleccion.origin } }
        : {}),
    })),
    selected_feature: sel?.properties
      ? { layer_id: sel.layerName ?? null, properties: sel.properties }
      : null,
    // Fase B: bbox real de la zona visible (cámara del mapa) para
    // consultas "qué hay en esta zona".
    viewport: { bbox: getViewBounds(), zoom: m.mapZoom, crs: 'EPSG:4326' },
    clicked_point: m.puntoMarcado,
    active_visualization: r.activo ? { type: 'query', query_id: r.activo } : null,
    basemap: m.baseMapId,
    acciones: accionesDesdeUltimoTurno(),
    ...(alcance.menciones?.length ? { menciones: alcance.menciones.slice(0, 20) } : {}),
    // el chip se muestra siempre que hay selección: si no se quitó, estaba en el alcance
    ...(conSeleccion && !alcance.sinSeleccion ? { alcance_seleccion: true } : {}),
    ...(conSeleccion && alcance.sinSeleccion
      ? { seleccion_excluida: { layer_id: conSeleccion.id, layer_name: conSeleccion.name,
                                count: conSeleccion.seleccion?.count ?? null } } : {}),
    ...(alcance.respuestaMapa ? { respuesta_mapa: alcance.respuestaMapa } : {}),
    ...(useComparacion.getState().actual
      ? { comparacion: { left: useComparacion.getState().actual!.left, right: useComparacion.getState().actual!.right } } : {}),
    ...(fechasDeSerie(layers.filter((l) => l.visible)).length > 1
      ? { serie_tiempo: { fechas: fechasDeSerie(layers.filter((l) => l.visible)), actual: useTiempo.getState().actual } } : {}),
    ...(useVistas.getState().vistas.length
      ? { vistas: useVistas.getState().vistas.slice(-20).map((v) => ({ nombre: v.nombre, bbox: v.bbox })) } : {}),
  }
}
