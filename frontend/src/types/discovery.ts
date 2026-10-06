/**
 * Tipos del panel de descubrimiento de datos (ArcGIS Hub).
 */

export type DiscoveryServiceType =
  | 'FeatureServer'
  | 'MapServer'
  | 'ImageServer'
  | 'GeoJSON'
  | 'CSV'
  | 'Postgis'
  | 'Other';

export type DiscoverySource = 'hub' | 'arcgis_online' | 'socrata' | 'internal';

export interface HubItem {
  id: string;
  source: DiscoverySource;
  org: string;
  title: string;
  description: string;
  service_type: DiscoveryServiceType;
  service_url: string;
  layer_id?: number | null;
  owner?: string;
  source_field?: string;
  tags?: string[];
  type_raw?: string;
  modified?: string | number | null;
  created?: string | number | null;
  thumbnail_url?: string | null;
  extent?: [number, number, number, number] | null;
  hub_url?: string | null;
  redirected_from?: 'socrata' | null;
  rank_score?: number;
  /** Hechos para juzgar el candidato (rama arcgis-busqueda). */
  credits?: string;
  views?: number | null;
  completeness?: number | null;
  single_layer?: boolean | null;
  sources?: string[];
}

/** Capa con geometría de un servicio con varias (para elegir cuál cargar). */
export interface DiscoveryLayer {
  id: number;
  nombre: string;
  tipo_geometria: string;
}

export type DiscoveryIntent =
  | 'entity_focused'
  | 'topic_focused'
  | 'zone_focused'
  | 'service_focused'
  | 'imagery_focused'
  | 'recent_focused'
  | 'exploratory';

export interface DiscoveryHints {
  service_types?: DiscoveryServiceType[] | string[];
  bbox?: [number, number, number, number];
  only_official_co?: boolean;
  owners?: string[];
  /**
   * Tags-any filter para Hub (`filter[tags]=any(...)`). Más fiable que
   * bbox para acotar por zona geográfica porque los publicadores etiquetan
   * deliberadamente sus items (ej. tags=['bogota','colombia']).
   */
  tags_any?: string[];
  /**
   * Región del catálogo a usar para detección de entidades/zonas y sesgo
   * automático. None/undefined = región activa del producto. 'global' =
   * sin sesgo regional (búsqueda mundial).
   */
  region?: string | null;
  /** Atajo legible — equivalente a region='global'. */
  global_mode?: boolean;
  max_results?: number;
}

export interface DiscoveryRegion {
  key: string;
  label: string;
  country_bbox?: [number, number, number, number] | null;
  entities: Array<{
    key: string;
    aliases: string[];
    has_sources: boolean;
    has_tags: boolean;
  }>;
  zones: Array<{
    key: string;
    label: string;
    bbox?: [number, number, number, number] | null;
    tags: string[];
  }>;
}

export interface DiscoveryRegionsResponse {
  active: string;
  regions: DiscoveryRegion[];
}

export interface DiscoveryRefinement {
  action: string;
  label: string;
  [k: string]: unknown;
}

export interface DiscoverySearchResponse {
  items: HubItem[];
  intent: DiscoveryIntent;
  authority_warning: boolean;
  suggested_refinements: DiscoveryRefinement[];
  /** El juicio del LLM sobre los candidatos (null = orden solo por hechos). */
  criterio?: string | null;
  /** Otras búsquedas que hizo porque las primeras no servían. */
  otras_busquedas?: string[];
  /** Cuántos sirven de verdad para lo pedido (los primeros de la lista). */
  relevantes?: number | null;
}

export interface DiscoveryImageryDescriptor {
  type: 'imagery';
  service_url: string;
  export_url?: string | null;
  extent?: Record<string, number> | null;
}

export interface DiscoveryLoadResponse {
  type: 'geojson' | 'imagery';
  name: string;
  service_type: DiscoveryServiceType;
  service_url: string;
  geojson?: import('./index').GeoJSONFeatureCollection | null;
  feature_count?: number | null;
  imagery?: DiscoveryImageryDescriptor | null;
  extent?: [number, number, number, number] | Record<string, number> | null;
  symbology?: import('./index').LayerSymbology | null;
  /** V5: la capa ya es un dataset del workspace de la sesión (las herramientas ws_* la usan). */
  dataset_id?: string | null;
  /** Cuántos elementos tiene el servicio (si la carga trae menos, se le dice al usuario). */
  total_available?: number | null;
  /** Capa grande: teselas MVT del workspace (la capa completa no viaja al navegador). */
  tiles?: import('../services/api').WorkspaceCapa['tiles'];
}
