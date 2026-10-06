/**
 * Helpers puros del DataDiscoveryPanel — extraídos para poder testear sin
 * tener que renderizar el componente entero (no usamos
 * @testing-library/react en este repo).
 *
 * La función central es `buildSearchRequest` que toma el estado de los
 * chips + el query del input y devuelve `{effectiveQuery, hints}` que es
 * lo que se manda a `POST /discovery/search`.
 */

import type { DiscoveryHints, DiscoveryServiceType, HubItem } from '@/types/discovery'

export interface ZonePreset {
  id: string
  label: string
  /** Tags-any que se mandan a Hub como filtro espacial reliable. */
  tags: string[]
  /** Texto que se concatena al q= del usuario para evitar colisiones léxicas. */
  extraQuery: string
}

export const ZONE_PRESETS: ZonePreset[] = [
  { id: 'colombia',     label: 'Colombia',     tags: ['colombia'],                                       extraQuery: 'Colombia' },
  { id: 'bogota',       label: 'Bogotá',       tags: ['bogota', 'bogotá', 'colombia'],                   extraQuery: 'Bogotá Colombia' },
  { id: 'cundinamarca', label: 'Cundinamarca', tags: ['cundinamarca', 'colombia'],                       extraQuery: 'Cundinamarca Colombia' },
  { id: 'medellin',     label: 'Medellín',     tags: ['medellin', 'medellín', 'antioquia', 'colombia'],  extraQuery: 'Medellín Colombia' },
  { id: 'cali',         label: 'Cali',         tags: ['cali', 'valle del cauca', 'colombia'],            extraQuery: 'Cali Colombia' },
]

const SERVICE_TYPE_MAP: Partial<Record<DiscoveryServiceType, string>> = {
  FeatureServer: 'Feature Service',
  MapServer:     'Map Service',
  ImageServer:   'Image Service',
}

export interface SearchInputs {
  query: string
  zone: string | null
  serviceFilter: DiscoveryServiceType | null
  officialOnly: boolean
  globalMode: boolean
  maxResults?: number
}

export interface SearchRequest {
  effectiveQuery: string
  hints: DiscoveryHints
}

/**
 * Devuelve `null` si los inputs no producen una búsqueda válida (todos
 * los filtros y el texto vacíos). El caller debe interpretar null como
 * "no buscar — limpiar resultados".
 */
export function buildSearchRequest(inputs: SearchInputs): SearchRequest | null {
  const trimmed = inputs.query.trim()
  const hasAnyFilter =
    !!trimmed ||
    inputs.officialOnly ||
    !!inputs.zone ||
    !!inputs.serviceFilter ||
    inputs.globalMode
  if (!hasAnyFilter) return null

  const hints: DiscoveryHints = { max_results: inputs.maxResults ?? 60 }

  const zonePreset = ZONE_PRESETS.find((z) => z.id === inputs.zone)
  const queryTokens: string[] = []
  if (trimmed) queryTokens.push(trimmed)

  // En modo Global NO añadimos "Colombia" al q ni tags_any de zona —
  // el usuario quiere búsqueda mundial. Si hay chip de zona junto a
  // global, usamos solo el nombre, sin extraQuery ni tags.
  if (zonePreset) {
    queryTokens.push(inputs.globalMode ? zonePreset.label : zonePreset.extraQuery)
    if (!inputs.globalMode) hints.tags_any = zonePreset.tags
  }
  const effectiveQuery = queryTokens.join(' ').trim()

  // Service type: solo los 3 cargables se mapean a la API. GeoJSON/CSV/
  // Postgis/Other no son tipos de Hub y se ignoran.
  if (inputs.serviceFilter) {
    const apiName = SERVICE_TYPE_MAP[inputs.serviceFilter]
    if (apiName) hints.service_types = [apiName]
  }

  if (inputs.officialOnly) hints.only_official_co = true
  if (inputs.globalMode) hints.global_mode = true

  return { effectiveQuery, hints }
}


/**
 * ¿Hay que preguntar qué capa cargar? Un FeatureServer sin capa en la URL, sin capa indicada y que
 * no se sabe que tenga una sola. Antes se cargaba la 0: en la cartografía de Cota eran PUNTOS.
 */
export function necesitaElegirCapa(item: HubItem): boolean {
  if (item.service_type !== 'FeatureServer' || item.layer_id != null || item.single_layer === true) return false
  return /\/featureserver\/?$/i.test(item.service_url)
}

/** Los hechos que la tarjeta muestra para juzgar el candidato: de quién es y cuánto se usa. */
export function hechosDeTarjeta(item: HubItem): { fuente: string; uso: string | null } {
  const fuente = (item.credits || '').trim() || item.org || item.owner || ''
  const uso = typeof item.views === 'number' && item.views > 0
    ? `${item.views.toLocaleString('es-CO')} ${item.views === 1 ? 'vista' : 'vistas'}`
    : null
  return { fuente, uso }
}

const GEOMETRIAS: Record<string, string> = {
  esriGeometryPoint: 'puntos', esriGeometryMultipoint: 'puntos', esriGeometryPolyline: 'líneas',
  esriGeometryPolygon: 'polígonos', Point: 'puntos', MultiPoint: 'puntos', Multipoint: 'puntos', Polyline: 'líneas',
  Polygon: 'polígonos',
}

export function nombreGeometria(tipo: string): string {
  return GEOMETRIAS[tipo] ?? tipo
}
