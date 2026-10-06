/**
 * E2.4 — las capas del workspace sobreviven a recargar la página.
 *
 * La sesión del backend y sus datasets viven 24 h; lo que se perdía al recargar
 * era el lado del navegador: cada carga abría una sesión NUEVA (que no ve el
 * workspace de la anterior) y el mapa arrancaba vacío. Aquí:
 *  - el id de sesión se guarda en `sessionStorage` (por pestaña: sobrevive a la
 *    recarga, no se comparte con otras pestañas);
 *  - las capas respaldadas por un dataset se recuerdan por id + estilo, y las
 *    raster (NDVI de un MCP, ImageServer…) por su URL (F4: están en la misma
 *    lista, y antes se perdían al recargar);
 *  - al volver, se piden al workspace de ESA sesión y se re-añaden en el MISMO
 *    orden de dibujo que tenían, entre todos los tipos.
 *
 * El almacenamiento puede fallar (modo privado, bloqueado): todo va en
 * try/catch y, sin él, la app funciona como antes (sesión nueva, mapa vacío).
 */
import type { Predicado } from '@/contracts'
import { esRaster, useMapStore, type Extent, type LayerTiles, type MapLayer, type OrigenCapa, type RasterLegend } from '@/stores/mapStore'
import { sessionApi, workspaceApi } from '@/services/api'
import { logger } from '@/utils/logger'
import type { ChatMessage, GeoJSONFeatureCollection, LayerSymbology } from '@/types'
import type { SeleccionCapa } from '@/lib/seleccion'

const CLAVE_SESION = 'geo.session'
const clavesCapas = (sid: string) => `geo.layers.${sid}`

export interface CapaGuardada {
  /** El id de la capa en el mapa: se conserva al restaurar (los enlaces [[layer:id]] de la
   *  conversación restaurada, y el historial que ve el agente, lo nombran). */
  id?: string
  /** Ausente en lo guardado antes de F4 (= dataset). */
  tipo?: 'dataset' | 'raster'
  datasetId?: string
  name: string
  symbology?: LayerSymbology
  visible: boolean
  opacity?: number
  labelField?: string | null
  /** FH.5: el filtro de la capa (es parte de lo que la capa ES: se restaura). */
  filtro?: Predicado[] | null
  filtroCount?: number | null
  // raster
  kind?: 'raster-xyz' | 'arcgis-image' | 'wms'
  url?: string
  extent?: Extent | null
  legend?: RasterLegend | null
  wmsLayers?: string
  origen?: OrigenCapa | null
  /** FH.10: fecha del raster (serie temporal). */
  fecha?: string | null
  /** FH.11: lo seleccionado en la capa (un proyecto lo conserva). */
  seleccion?: SeleccionCapa | null
}

function leer(clave: string): string | null {
  try {
    return window.sessionStorage.getItem(clave)
  } catch {
    return null
  }
}

function escribir(clave: string, valor: string): void {
  try {
    window.sessionStorage.setItem(clave, valor)
  } catch {
    /* sin almacenamiento: no se restaura, nada más */
  }
}

let enCurso: Promise<{ sessionId: string; recuperada: boolean }> | null = null

/**
 * La sesión de esta pestaña si sigue viva en el backend; si no, una nueva.
 *
 * Una sola vez por carga: StrictMode (dev) monta los efectos dos veces y cada
 * init creaba su propia sesión; la guardada y la del estado podían divergir y la
 * restauración buscaba las capas en la equivocada.
 */
export function sesionDeLaPestana(): Promise<{ sessionId: string; recuperada: boolean }> {
  enCurso ??= resolverSesion().catch((error) => {
    enCurso = null  // un fallo de red no deja la pestaña sin poder reintentar
    throw error
  })
  return enCurso
}

/** Solo para tests: olvidar la resolución de esta carga. */
export function _reiniciarSesionDeLaPestana(): void {
  enCurso = null
  restauraciones.clear()
}

async function resolverSesion(): Promise<{ sessionId: string; recuperada: boolean }> {
  const previa = leer(CLAVE_SESION)
  if (previa) {
    try {
      if (await sessionApi.exists(previa)) return { sessionId: previa, recuperada: true }
    } catch (error) {
      logger.warn('[workspace] no se pudo verificar la sesión previa:', error)
    }
  }
  const { session_id } = await sessionApi.create()
  recordarSesion(session_id)
  return { sessionId: session_id, recuperada: false }
}

export function recordarSesion(sessionId: string): void {
  escribir(CLAVE_SESION, sessionId)
}

/** Guarda, en orden de dibujo, las capas con dataset (id + estilo) y las raster (URL). */
export function recordarCapas(sessionId: string, layers: MapLayer[]): void {
  escribir(clavesCapas(sessionId), JSON.stringify(capasGuardables(layers)))
}

/** FH.11: lo que se guarda de cada capa (la pestaña al recargar y un proyecto): en orden de dibujo. */
export function capasGuardables(layers: MapLayer[]): CapaGuardada[] { // eslint-disable-line complexity -- deuda congelada (F1): ESLint 10 suma `?.` y defaults; partir, no subir
  const guardadas: CapaGuardada[] = []
  for (const l of layers) {
    const comun = { id: l.id, name: l.name, visible: l.visible, opacity: l.opacity }
    if (esRaster(l) && l.url) {
      guardadas.push({
        ...comun, tipo: 'raster', kind: l.kind as CapaGuardada['kind'], url: l.url,
        extent: l.extent ?? null, legend: l.legend ?? null, origen: l.origen ?? null,
        ...(l.wmsLayers ? { wmsLayers: l.wmsLayers } : {}),
        ...(l.fecha ? { fecha: l.fecha } : {}),
      })
    } else if (l.datasetId) {
      guardadas.push({ ...comun, tipo: 'dataset', datasetId: l.datasetId, symbology: l.symbology,
                       labelField: l.labelField ?? null, origen: l.origen ?? null,
                       ...(l.filtro?.length ? { filtro: l.filtro, filtroCount: l.filtroCount ?? null } : {}),
                       ...(l.seleccion ? { seleccion: l.seleccion } : {}) })
    }
  }
  return guardadas
}

// ---------------------------------------------------------------------------
// FH.11 — la conversación y la cámara de la sesión (la pestaña y los proyectos)
// ---------------------------------------------------------------------------

const claveChat = (sid: string) => `geo.chat.${sid}`
const claveCamara = (sid: string) => `geo.camara.${sid}`
const MAX_MENSAJES = 60

/** Lo que se guarda de un mensaje: su texto (la respuesta completa —capas, tablas— ya vive en el mapa). */
export interface MensajeGuardado {
  id: string; role: ChatMessage['role']; content: string; timestamp: string
  /** Las capas que dejó ese turno: `activa` en sus enlaces se resuelve con ellas (V5 EH.11). */
  capas?: string[]
  /** F7: turno que seguía en el servidor cuando la página se recargó (su resultado viene por WS). */
  turnoPendiente?: string
}

export function mensajesGuardables(mensajes: ChatMessage[]): MensajeGuardado[] {
  return mensajes.filter((m) => m.status !== 'sending' && m.content)
    .slice(-MAX_MENSAJES)
    .map((m) => ({ id: m.id, role: m.role, content: m.content, timestamp: new Date(m.timestamp).toISOString(),
                   ...(m.capas?.length ? { capas: m.capas } : {}),
                   ...(m.turnoPendiente ? { turnoPendiente: m.turnoPendiente } : {}) }))
}

export function recordarChat(sessionId: string, mensajes: ChatMessage[]): void {
  escribir(claveChat(sessionId), JSON.stringify(mensajesGuardables(mensajes)))
}

export function leerChat(sessionId: string): ChatMessage[] {
  try {
    const v = JSON.parse(leer(claveChat(sessionId)) ?? '[]') as MensajeGuardado[]
    return Array.isArray(v) ? v.map((m) => ({ ...m, timestamp: new Date(m.timestamp), status: 'sent' as const })) : []
  } catch {
    return []
  }
}

export interface Camara { center: [number, number]; zoom: number }

export function recordarCamara(sessionId: string, camara: Camara): void {
  escribir(claveCamara(sessionId), JSON.stringify(camara))
}

export function leerCamara(sessionId: string): Camara | null {
  try {
    const c = JSON.parse(leer(claveCamara(sessionId)) ?? 'null') as Camara | null
    return c && Array.isArray(c.center) && typeof c.zoom === 'number' ? c : null
  } catch {
    return null
  }
}

/** FH.11: el estado de un proyecto abierto pasa a ser el de esta pestaña (lo restaura el arranque). */
export function escribirEstadoDeSesion(sessionId: string, estado: {
  capas?: CapaGuardada[]; vistas?: unknown[]; chat?: MensajeGuardado[]; camara?: Camara | null
}): void {
  recordarSesion(sessionId)
  escribir(clavesCapas(sessionId), JSON.stringify(estado.capas ?? []))
  escribir(`geo.vistas.${sessionId}`, JSON.stringify(estado.vistas ?? []))
  escribir(claveChat(sessionId), JSON.stringify(estado.chat ?? []))
  if (estado.camara) recordarCamara(sessionId, estado.camara)
}

const VACIO: GeoJSONFeatureCollection = { type: 'FeatureCollection', features: [] }

const restauraciones = new Map<string, Promise<number>>()

/**
 * V5 EH.7: la capa restaurada vuelve con SU id (si está libre). Con uno nuevo, los enlaces
 * de la conversación quedaban rotos y el agente, que lee el historial, citaba el id viejo.
 */
function conservarId(nuevo: string, guardado: string | undefined): string {
  if (!guardado || guardado === nuevo || useMapStore.getState().layers.some((l) => l.id === guardado)) return nuevo
  useMapStore.setState((st) => ({ layers: st.layers.map((l) => (l.id === nuevo ? { ...l, id: guardado } : l)) }))
  return guardado
}

/**
 * Re-añade al mapa las capas guardadas, leyéndolas del workspace de la sesión.
 * Una vez por sesión y carga (StrictMode duplicaba las capas restauradas).
 */
export function restaurarCapas(sessionId: string): Promise<number> {
  let p = restauraciones.get(sessionId)
  if (!p) {
    p = restaurar(sessionId)
    restauraciones.set(sessionId, p)
  }
  return p
}

async function restaurar(sessionId: string): Promise<number> { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  let guardadas: CapaGuardada[]
  try {
    guardadas = JSON.parse(leer(clavesCapas(sessionId)) ?? '[]')
  } catch {
    return 0
  }
  const map = useMapStore.getState()
  let restauradas = 0
  const idsEnOrden: string[] = []
  // Idempotente: lo que ya está en el mapa (por su dataset o su URL) no se añade otra vez
  // (V5 en Chrome: una segunda restauración duplicó la capa y el duplicado quedó guardado).
  const yaEsta = (g: CapaGuardada) => useMapStore.getState().layers.some((l) =>
    (g.datasetId && l.datasetId === g.datasetId) || (g.tipo === 'raster' && g.url && l.url === g.url))
  const vistas = new Set<string>()
  for (const g of guardadas) {
    const clave = g.datasetId ?? g.url ?? ''
    if (!clave || vistas.has(clave) || yaEsta(g)) continue
    vistas.add(clave)
    if (g.tipo === 'raster' && g.url) {
      const id = conservarId(map.addRasterLayer({ url: g.url, name: g.name, extent: g.extent, legend: g.legend,
                                                  kind: g.kind, wmsLayers: g.wmsLayers, origen: g.origen, fecha: g.fecha ?? null }), g.id)
      if (!g.visible) useMapStore.getState().toggleLayerVisibility(id)
      if (g.opacity !== undefined) useMapStore.getState().setLayerOpacity(id, g.opacity)
      idsEnOrden.push(id)
      restauradas += 1
      continue
    }
    if (!g.datasetId) continue
    try {
      const capa = await workspaceApi.capa(sessionId, g.datasetId)
      const tiles: LayerTiles | undefined = capa.tiles
        ? {
            url: capa.tiles.url,
            sourceLayer: capa.tiles.source_layer,
            geometryType: capa.tiles.geometry_type,
            bbox: capa.tiles.bbox,
            featureCount: capa.tiles.feature_count ?? 0,
            fields: capa.tiles.fields ?? [],
          }
        : undefined
      const id = conservarId(map.addLayer(capa.geojson ?? VACIO, g.name, g.symbology, g.datasetId, tiles), g.id)
      // El nombre es el que tenía la capa, no el `layer_title` de su estilo: si no,
      // un re-estilo del agente la renombraba al recargar (V5 FH.1).
      // FH.3: y su procedencia (un dibujo sigue siendo un dibujo: editable, y así lo ve el agente).
      useMapStore.setState((st) => ({
        layers: st.layers.map((l) => (l.id === id ? { ...l, name: g.name, ...(g.origen ? { origen: g.origen } : {}) } : l)),
      }))
      if (!g.visible) useMapStore.getState().toggleLayerVisibility(id)
      if (g.opacity !== undefined) useMapStore.getState().setLayerOpacity(id, g.opacity)
      if (g.labelField) useMapStore.getState().setLayerLabelField(id, g.labelField)
      if (g.filtro?.length) {
        useMapStore.setState((st) => ({
          layers: st.layers.map((l) => (l.id === id ? { ...l, filtro: g.filtro, filtroCount: g.filtroCount ?? null } : l)),
        }))
      }
      if (g.seleccion) {
        useMapStore.setState((st) => ({ layers: st.layers.map((l) => (l.id === id ? { ...l, seleccion: g.seleccion } : l)) }))
      }
      idsEnOrden.push(id)
      restauradas += 1
    } catch (error) {
      // Vencido (TTL) o ajeno: se omite; el resto se restaura igual.
      logger.warn(`[workspace] no se restauró ${g.datasetId}:`, error)
    }
  }
  // El orden guardado manda (un raster pudo estar encima de un vector).
  const base = useMapStore.getState().layers.length - idsEnOrden.length
  idsEnOrden.forEach((id, i) => useMapStore.getState().moveLayer(id, base + i))
  return restauradas
}
