/**
 * Estado del mapa (F4, S4.2): UNA lista de capas para todos los tipos.
 *
 * Antes había tres (`layers` vectoriales, `imageryLayers` raster, `tiledLayers`
 * MVT de tablas), cada una con su orden: un raster no podía ir encima de un
 * vector. Ahora cada capa es un `MapLayer` con su `kind` de renderer
 * (`lib/renderers`); el orden del array es el orden de dibujo (índice 0 =
 * abajo) y la opacidad y la visibilidad son comunes a todos los tipos.
 */
import type { Predicado } from '@/contracts';
import type { SeleccionCapa } from '@/lib/seleccion';
import { create } from 'zustand';
import type { CogSpec } from '@/lib/cogNavegador';
import type { GeoJSONFeatureCollection, LayerSymbology } from '@/types';

/** Tipos de capa que sabe dibujar el mapa (uno por archivo en `lib/renderers`). */
export type RendererKind = 'vector-geojson' | 'vector-mvt' | 'raster-xyz' | 'arcgis-image' | 'wms';

export const esVectorial = (l: Pick<MapLayer, 'kind'>) =>
  l.kind === 'vector-geojson' || l.kind === 'vector-mvt';
export const esRaster = (l: Pick<MapLayer, 'kind'>) => !esVectorial(l);

export type Extent = { xmin: number; ymin: number; xmax: number; ymax: number };

export interface LayerTiles {
  /** Plantilla `/api/v1/tiles/…/{z}/{x}/{y}.pbf` (workspace o tabla de la BD). */
  url: string;
  sourceLayer: string;
  geometryType: string | null;
  bbox: [number, number, number, number] | null;
  featureCount: number;
  fields: string[];
}

/** Leyenda de un raster tal como la da el productor (p. ej. rampa NDVI de un MCP). */
export interface RasterLegend {
  type?: string;
  field?: string;
  min?: number;
  max?: number;
  nota?: string;
  /** Paradas de la rampa (si el servidor la declara; si no, la del índice). */
  colores?: string[];
  /** Raster de clases (la SCL de Sentinel-2): cada valor con su etiqueta y color. */
  clases?: { valor: number; etiqueta: string; color: string }[];
}

export interface MapLayer {
  id: string;
  name: string;
  kind: RendererKind;
  /** Vectorial inline: las features. Vacío en MVT y raster. */
  data: GeoJSONFeatureCollection;
  visible: boolean;
  color: string;
  symbology?: LayerSymbology;
  featureCount: number;
  addedAt: Date;
  /** T2.0a: dataset del workspace que respalda la capa. */
  datasetId?: string;
  /** S2.3 / tablas de la BD: la capa se dibuja desde teselas MVT. */
  tiles?: LayerTiles;
  /** Raster: URL XYZ (con {z}), URL base de un ArcGIS MapServer/ImageServer, o WMS. */
  url?: string;
  /** WMS: capas a pedir. */
  wmsLayers?: string;
  extent?: Extent | null;
  legend?: RasterLegend | null;
  /** Multiplicador 0..1 sobre la opacidad propia del estilo. 1 = sin cambio. */
  opacity?: number;
  /** Propiedad a mostrar como etiqueta (vectoriales). */
  labelField?: string | null;
  /** S4.4: de dónde salió (tool + argumentos): viaja al agente en el map_context. */
  origen?: OrigenCapa | null;
  /** Pintarla en el navegador desde sus COG (raster-xyz de un servidor de confianza). */
  cog?: CogSpec | null;
  /** FH.2: lo seleccionado en esta capa (ids o condición). Lo cambia el reducer (`select`). */
  seleccion?: SeleccionCapa | null;
  /** FH.10: el instante que retrata (YYYY-MM-DD): las capas con fecha forman una serie temporal. */
  fecha?: string | null;
  /** FH.7: lo que señala el enlace de la respuesta bajo el ratón. Transitorio: no pasa
   * por el reducer, no va al agente ni se guarda. */
  resaltado?: SeleccionCapa | null;
  /** FH.5: filtro de la capa (todas se cumplen). La capa filtrada ES ese subconjunto:
   * en el mapa, en la tabla y para el agente. */
  filtro?: Predicado[] | null;
  /** Cuántos quedan con el filtro (en memoria se cuentan; en teselas lo dice quien filtra). */
  filtroCount?: number | null;
}

/** Procedencia compacta de una capa (la del contrato, sin sql/código). */
export interface OrigenCapa {
  capability: string;
  arguments?: Record<string, unknown>;
}

/** Punto que el usuario marcó en el mapa: el «aquí» de sus preguntas. */
export interface PuntoMarcado {
  lon: number;
  lat: number;
}

// Base map types
export type BaseMapId =
  | 'osm'
  | 'osm-hot'
  | 'arcgis-imagery'
  | 'arcgis-imagery-labels'
  | 'arcgis-streets'
  | 'arcgis-topo'
  | 'arcgis-dark'
  | 'arcgis-ocean'
  | 'carto-positron'
  | 'carto-dark'
  | 'carto-voyager';

// Available colors for layers
export const LAYER_COLORS = [
  '#3b82f6', // blue
  '#10b981', // green
  '#f59e0b', // amber
  '#ef4444', // red
  '#8b5cf6', // purple
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#f97316', // orange
];

// Base map configuration
export interface BaseMapConfig {
  id: BaseMapId;
  name: string;
  url: string;
  overlayUrl?: string;
  attribution?: string;
  maxZoom?: number;
  category: 'street' | 'satellite' | 'terrain' | 'dark';
}

// Available base maps - all free/open
export const BASE_MAPS: BaseMapConfig[] = [
  // OpenStreetMap variants
  {
    id: 'osm',
    name: 'OpenStreetMap',
    url: 'https://tile.openstreetmap.org/',
    attribution: '© OpenStreetMap contributors',
    category: 'street',
  },
  {
    id: 'osm-hot',
    name: 'OSM Humanitarian',
    url: 'https://tile-{s}.openstreetmap.fr/hot/',
    attribution: '© OpenStreetMap contributors, Humanitarian style',
    category: 'street',
  },
  // ArcGIS free tile services
  {
    id: 'arcgis-imagery',
    name: 'ArcGIS Satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri, Maxar, Earthstar Geographics',
    category: 'satellite',
  },
  {
    id: 'arcgis-imagery-labels',
    name: 'ArcGIS Satellite + Labels',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    overlayUrl: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri, Maxar, Earthstar Geographics',
    category: 'satellite',
  },
  {
    id: 'arcgis-streets',
    name: 'ArcGIS Streets',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
    category: 'street',
  },
  {
    id: 'arcgis-topo',
    name: 'ArcGIS Topographic',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
    category: 'terrain',
  },
  {
    id: 'arcgis-dark',
    name: 'ArcGIS Dark Gray',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
    category: 'dark',
  },
  {
    id: 'arcgis-ocean',
    name: 'ArcGIS Ocean',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
    category: 'terrain',
  },
  // CartoDB/Carto free basemaps
  {
    id: 'carto-positron',
    name: 'CartoDB Positron',
    url: 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors, © CARTO',
    category: 'street',
  },
  {
    id: 'carto-dark',
    name: 'CartoDB Dark Matter',
    url: 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors, © CARTO',
    category: 'dark',
  },
  {
    id: 'carto-voyager',
    name: 'CartoDB Voyager',
    url: 'https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors, © CARTO',
    category: 'street',
  },
];

export const EMPTY_FC: GeoJSONFeatureCollection = { type: 'FeatureCollection', features: [] };

export interface RasterInput {
  url: string;
  name: string;
  extent?: Extent | null;
  legend?: RasterLegend | null;
  kind?: 'raster-xyz' | 'arcgis-image' | 'wms';
  wmsLayers?: string;
  origen?: OrigenCapa | null;
  /** FH.10: el instante que retrata (serie temporal). */
  fecha?: string | null;
  /** Cómo pintarla en el navegador desde sus COG (las teselas de `url` quedan de respaldo). */
  cog?: CogSpec | null;
}

interface MapState {
  /** Todas las capas, en orden de dibujo (índice 0 = abajo). */
  layers: MapLayer[];
  baseMapId: BaseMapId;
  mapCenter: [number, number];
  mapZoom: number;
  selectedFeature: unknown | null;
  /** Último punto donde el usuario hizo click en el mapa (S4.4, E4.5). */
  puntoMarcado: PuntoMarcado | null;
  /** Globo o plano (Mercator). */
  proyeccion: 'mercator' | 'globe';
  setProyeccion: (p: 'mercator' | 'globe') => void;
  flyToLayerId: string | null;
  /** FH.1: `zoom_to` sin capa — encuadrar esta extensión (EPSG:4326). */
  encuadrePedido: [number, number, number, number] | null;
  /** FH.2: herramienta de selección activa (un solo uso: vuelve a `null` al soltar). */
  modoSeleccion: 'box' | 'lasso' | null;
  /** FH.3: dibujando (qué figura) o editando los vértices de la capa `editandoId`. */
  modoDibujo: TipoDibujo | 'editar' | null;
  editandoId: string | null;

  // Actions
  /** Capa vectorial (inline, o MVT si llega `tiles`). Devuelve su id. */
  addLayer: (
    data: GeoJSONFeatureCollection,
    name?: string,
    symbology?: LayerSymbology,
    datasetId?: string,
    tiles?: LayerTiles
  ) => string;
  /** Capa raster (XYZ, ArcGIS o WMS): entra debajo de los vectores. Devuelve su id. */
  addRasterLayer: (input: RasterInput) => string;
  /** Tabla de la BD como teselas MVT (idempotente por tabla). */
  addTableTiles: (schema: string, table: string, geomType: 'polygon' | 'line' | 'point', name?: string) => string;
  removeLayer: (id: string) => void;
  toggleLayerVisibility: (id: string) => void;
  setLayerOpacity: (id: string, opacity: number) => void;
  setLayerLabelField: (id: string, field: string | null) => void;
  /** Re-estilo in situ (misma capa, mismo lugar en el orden). */
  setLayerStyle: (id: string, symbology: LayerSymbology) => void;
  /** El `cog` de una escena pintada en el navegador (p. ej. otro contraste: se repinta al instante). */
  setLayerCog: (id: string, cog: CogSpec) => void;
  clearAllLayers: () => void;
  /** Mueve una capa a la posición `toIndex` del orden de dibujo (entre todos los tipos). */
  moveLayer: (id: string, toIndex: number) => void;
  setBaseMap: (id: BaseMapId) => void;
  setMapView: (center: [number, number], zoom: number) => void;
  setSelectedFeature: (feature: unknown | null) => void;
  /** FH.7: resaltar (o dejar de resaltar, null) lo que señala un enlace de la respuesta. */
  setResaltado: (layerId: string, resaltado: SeleccionCapa | null) => void;
  setPuntoMarcado: (p: PuntoMarcado | null) => void;
  flyToLayer: (id: string) => void;
  clearFlyToLayer: () => void;
  pedirEncuadre: (bbox: [number, number, number, number] | null) => void;
  setModoSeleccion: (modo: 'box' | 'lasso' | null) => void;
  setModoDibujo: (modo: TipoDibujo | 'editar' | null, editandoId?: string | null) => void;
}

/** FH.3: las figuras que se pueden dibujar (modos de terra-draw). */
export type TipoDibujo = 'point' | 'linestring' | 'polygon' | 'rectangle' | 'circle'

let layerCounter = 0;

const colorDe = (symbology: LayerSymbology | undefined, fallback: string) =>
  symbology?.fill?.color || symbology?.stroke?.color || symbology?.marker?.color || fallback;

/** Índice donde entra un raster: justo debajo del vector más bajo (o arriba si no hay). */
function indiceRaster(layers: MapLayer[]): number {
  const i = layers.findIndex(esVectorial);
  return i === -1 ? layers.length : i;
}

const GEOM_TABLA = { polygon: 'Polygon', line: 'LineString', point: 'Point' } as const;

export const useMapStore = create<MapState>((set, get) => ({
  layers: [],
  baseMapId: 'osm',
  mapCenter: [-74.0721, 4.711], // Bogota default
  mapZoom: 10,
  selectedFeature: null,
  puntoMarcado: null,
  proyeccion: 'mercator',
  flyToLayerId: null,
  encuadrePedido: null,
  modoSeleccion: null,
  modoDibujo: null,
  editandoId: null,

  addLayer: (data, name, symbology, datasetId, tiles) => {
    const id = `layer-${++layerCounter}-${Date.now()}`;
    const vectoriales = get().layers.filter(esVectorial).length;
    const newLayer: MapLayer = {
      id,
      kind: tiles ? 'vector-mvt' : 'vector-geojson',
      name: symbology?.layer_title || name || `Consulta ${layerCounter}`,
      data,
      visible: true,
      color: colorDe(symbology, LAYER_COLORS[vectoriales % LAYER_COLORS.length]),
      symbology,
      featureCount: tiles ? tiles.featureCount : data.features?.length || 0,
      addedAt: new Date(),
      opacity: 1,
      ...(datasetId ? { datasetId } : {}),
      ...(tiles ? { tiles } : {}),
    };
    set((state) => ({ layers: [...state.layers, newLayer] }));
    return id;
  },

  addRasterLayer: ({ url, name, extent, legend, kind, wmsLayers, origen, fecha, cog }) => {
    const id = `raster-${++layerCounter}-${Date.now()}`;
    const layer: MapLayer = {
      id,
      kind: kind ?? (url.includes('{z}') ? 'raster-xyz' : 'arcgis-image'),
      name: name || 'Imagen',
      data: EMPTY_FC,
      visible: true,
      color: '#8a4c8a',
      featureCount: 0,
      addedAt: new Date(),
      opacity: 1,
      url,
      extent: extent ?? null,
      legend: legend ?? null,
      origen: origen ?? null,
      fecha: fecha ?? null,
      ...(cog ? { cog } : {}),
      ...(wmsLayers ? { wmsLayers } : {}),
    };
    set((state) => {
      const i = indiceRaster(state.layers);
      return { layers: [...state.layers.slice(0, i), layer, ...state.layers.slice(i)] };
    });
    return id;
  },

  addTableTiles: (schema, table, geomType, name) => {
    const id = `tile-${schema}.${table}`;
    if (get().layers.some((l) => l.id === id)) return id; // idempotente por tabla
    const vectoriales = get().layers.filter(esVectorial).length;
    const layer: MapLayer = {
      id,
      kind: 'vector-mvt',
      name: name || `${schema}.${table}`,
      data: EMPTY_FC,
      visible: true,
      color: LAYER_COLORS[vectoriales % LAYER_COLORS.length],
      featureCount: 0,
      addedAt: new Date(),
      opacity: 1,
      tiles: {
        url: `/api/v1/tiles/${schema}/${table}/{z}/{x}/{y}.pbf`,
        sourceLayer: `${schema}.${table}`,
        geometryType: GEOM_TABLA[geomType],
        bbox: null,
        featureCount: 0,
        fields: [],
      },
    };
    set((state) => ({ layers: [...state.layers, layer] }));
    return id;
  },

  removeLayer: (id) =>
    set((state) => ({ layers: state.layers.filter((l) => l.id !== id) })),

  toggleLayerVisibility: (id) =>
    set((state) => ({
      layers: state.layers.map((l) => (l.id === id ? { ...l, visible: !l.visible } : l)),
    })),

  setLayerOpacity: (id, opacity) =>
    set((state) => ({
      layers: state.layers.map((l) =>
        l.id === id ? { ...l, opacity: Math.max(0, Math.min(1, opacity)) } : l
      ),
    })),

  setLayerLabelField: (id, field) =>
    set((state) => ({
      layers: state.layers.map((l) => (l.id === id ? { ...l, labelField: field || null } : l)),
    })),

  setLayerStyle: (id, symbology) =>
    set((state) => ({
      layers: state.layers.map((l) =>
        l.id === id ? { ...l, symbology, color: colorDe(symbology, l.color) } : l
      ),
    })),

  setLayerCog: (id, cog) => set((state) => ({
    layers: state.layers.map((l) => (l.id === id ? { ...l, cog } : l)),
  })),
  clearAllLayers: () => set({ layers: [] }),

  moveLayer: (id, toIndex) =>
    set((state) => {
      const from = state.layers.findIndex((l) => l.id === id);
      if (from === -1) return state;
      const to = Math.max(0, Math.min(state.layers.length - 1, toIndex));
      if (to === from) return state;
      const next = [...state.layers];
      const [capa] = next.splice(from, 1);
      next.splice(to, 0, capa);
      return { layers: next };
    }),

  setBaseMap: (id) => set({ baseMapId: id }),
  setMapView: (center, zoom) => set({ mapCenter: center, mapZoom: zoom }),
  setSelectedFeature: (feature) => set({ selectedFeature: feature }),
  setResaltado: (layerId, resaltado) => set((s) => ({
    layers: s.layers.map((l) => (l.id === layerId ? { ...l, resaltado } : l)),
  })),
  setPuntoMarcado: (p) => set({ puntoMarcado: p }),
  setProyeccion: (p) => set({ proyeccion: p }),
  flyToLayer: (id) => set({ flyToLayerId: id }),
  clearFlyToLayer: () => set({ flyToLayerId: null }),
  pedirEncuadre: (bbox) => set({ encuadrePedido: bbox }),
  // Seleccionar y dibujar se excluyen: el mismo arrastre no puede ser las dos cosas.
  setModoSeleccion: (modo) => set(modo ? { modoSeleccion: modo, modoDibujo: null, editandoId: null } : { modoSeleccion: null }),
  setModoDibujo: (modo, editandoId = null) =>
    set(modo ? { modoDibujo: modo, editandoId, modoSeleccion: null } : { modoDibujo: null, editandoId: null }),
}));

// Selectors
export const useLayers = () => useMapStore((s) => s.layers);
export const useBaseMapId = () => useMapStore((s) => s.baseMapId);
export const useMapCenter = () => useMapStore((s) => s.mapCenter);
export const useMapZoom = () => useMapStore((s) => s.mapZoom);
export const useSelectedFeature = () => useMapStore((s) => s.selectedFeature);
export const useFlyToLayerId = () => useMapStore((s) => s.flyToLayerId);
