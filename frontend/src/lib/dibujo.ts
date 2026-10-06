/**
 * FH.3 — dibujos del usuario sobre el mapa (terra-draw).
 *
 * terra-draw solo vive MIENTRAS se dibuja o se editan vértices. Al terminar, el
 * dibujo se guarda como dataset del workspace (`provider: sketch`) y entra al
 * mapa como una capa más, por el reducer: la ven el panel de capas, la selección,
 * el estilo y el agente («[layer-…] "Área 1" (dataset ds_…) — DIBUJADA por el
 * usuario»), y cualquier capacidad la usa como área de interés por su id.
 *
 * Editar: la capa se oculta, sus features pasan a terra-draw (modo select, con
 * vértices arrastrables) y al guardar se envían por su `id` (= fid del
 * workspace): el dataset conserva su id.
 */
import type { Map as MapLibreMap } from 'maplibre-gl'
import {
  TerraDraw,
  TerraDrawCircleMode,
  TerraDrawLineStringMode,
  TerraDrawPointMode,
  TerraDrawPolygonMode,
  TerraDrawRectangleMode,
  TerraDrawSelectMode,
  type GeoJSONStoreFeatures,
} from 'terra-draw'
import { TerraDrawMapLibreGLAdapter } from 'terra-draw-maplibre-gl-adapter'

import { capaDelUsuario, useOperaciones } from '@/lib/operaciones'
import { usePedidoMapa } from '@/lib/pedidoMapa'
import { identidad } from '@/lib/seleccion'
import { workspaceApi, type Medicion } from '@/services/api'
import { useMapStore, type MapLayer, type TipoDibujo } from '@/stores/mapStore'
import { useSessionStore } from '@/stores/sessionStore'
import type { GeoJSONFeatureCollection } from '@/types'

/** Procedencia de una capa dibujada (la misma que guarda el backend). */
export const CAPACIDAD_DIBUJO = 'user.sketch'

const COLOR = '#c2410c'
const VERTICES = { midpoints: true, draggable: true, deletable: true }

/** Nombre por defecto: «Área N», «Línea N», «Punto N» (N = siguiente libre entre los dibujos). */
export function nombreParaDibujo(tipo: TipoDibujo, capas: Pick<MapLayer, 'name'>[]): string {
  const base = tipo === 'point' ? 'Punto' : tipo === 'linestring' ? 'Línea' : 'Área'
  const usados = new Set(capas.map((c) => c.name))
  let n = 1
  while (usados.has(`${base} ${n}`)) n++
  return `${base} ${n}`
}

/** El modo de terra-draw con el que se edita una geometría ya guardada. */
export function modoDeEdicion(tipoGeometria: string): 'point' | 'linestring' | 'polygon' | null {
  if (tipoGeometria === 'Point') return 'point'
  if (tipoGeometria === 'LineString') return 'linestring'
  if (tipoGeometria === 'Polygon') return 'polygon'
  return null // las Multi* no se editan vértice a vértice (sí se renombran y borran)
}

export const esDibujo = (l: Pick<MapLayer, 'origen'>) => l.origen?.capability === CAPACIDAD_DIBUJO

let mapa: MapLibreMap | null = null
let draw: TerraDraw | null = null
/** Edición en curso: id de feature de terra-draw → fid del workspace. */
let edicion: { capaId: string; fids: Map<string | number, number>; visibleAntes: boolean } | null = null
let errorListener: ((msg: string | null) => void) | null = null

/** El mapa en el que se dibuja (lo registra MapLibreMap al crearse). */
export function registrarMapa(m: MapLibreMap | null) {
  if (draw) {
    try { draw.stop() } catch { /* el mapa ya no existe */ }
  }
  draw = null
  edicion = null
  mapa = m
}

/** Quién muestra los errores al usuario (la barra de dibujo). */
export function alError(fn: ((msg: string | null) => void) | null) {
  errorListener = fn
}

function avisar(msg: string | null) {
  errorListener?.(msg)
}

function instancia(): TerraDraw | null {
  if (!mapa) return null
  if (draw) return draw
  const estilo = { fillColor: COLOR, outlineColor: COLOR, fillOpacity: 0.2, outlineWidth: 2 } as const
  draw = new TerraDraw({
    adapter: new TerraDrawMapLibreGLAdapter({ map: mapa }),
    modes: [
      new TerraDrawPointMode({ styles: { pointColor: COLOR, pointWidth: 6 } }),
      new TerraDrawLineStringMode({ styles: { lineStringColor: COLOR, lineStringWidth: 3 } }),
      new TerraDrawPolygonMode({ styles: estilo }),
      new TerraDrawRectangleMode({ styles: estilo }),
      new TerraDrawCircleMode({ styles: estilo }),
      new TerraDrawSelectMode({
        flags: {
          polygon: { feature: { draggable: true, coordinates: VERTICES } },
          linestring: { feature: { draggable: true, coordinates: VERTICES } },
          point: { feature: { draggable: true } },
        },
      }),
    ],
  })
  draw.start()
  if (import.meta.env.DEV) (window as unknown as { __terraDraw?: TerraDraw }).__terraDraw = draw
  draw.on('finish', (id, ctx) => {
    if (ctx.mode === 'select' || ctx.action !== 'draw') return
    const f = draw?.getSnapshotFeature(id)
    // clear(): también los auxiliares del modo (el marcador de cierre del polígono
    // se quedaba pintado en el primer vértice con removeFeatures).
    draw?.clear()
    draw?.setMode('static')
    const tipo = useMapStore.getState().modoDibujo
    // El modo se apaga DESPUÉS del `click` que cerró la figura: si no, MapLibre lo
    // recibía ya sin modo y marcaba ahí un «punto» (el «aquí» del agente) espurio.
    setTimeout(() => useMapStore.getState().setModoDibujo(null), 0)
    if (f && medida) { void medirFigura(f); return }
    if (f && tipo && tipo !== 'editar') void guardarDibujo(tipo, f)
  })
  return draw
}

/** Empieza a dibujar una figura (un solo uso: al terminarla se guarda y el modo se apaga). */
export function empezarDibujo(tipo: TipoDibujo) {
  cancelarEdicion()
  const d = instancia()
  if (!d) return
  avisar(null)
  useMapStore.getState().setModoDibujo(tipo)
  d.setMode(tipo)
}

/** FH.10: medir (línea o área) sin guardar nada. El resultado lo escucha la barra. */
let medida = false
let medicionListener: ((m: Medicion | null, error?: string) => void) | null = null
export function alMedir(fn: typeof medicionListener) {
  medicionListener = fn
}

export function empezarMedida(tipo: 'linestring' | 'polygon') {
  medida = true
  empezarDibujo(tipo)
}

async function medirFigura(f: GeoJSONStoreFeatures) {
  medida = false
  const sessionId = useSessionStore.getState().sessionId
  if (!sessionId) return medicionListener?.(null, 'No hay sesión para medir.')
  try {
    medicionListener?.(await workspaceApi.medir(sessionId, f.geometry))
  } catch (err) {
    medicionListener?.(null, `No se pudo medir: ${err instanceof Error ? err.message : String(err)}`)
  }
}

/** Deja de dibujar sin guardar nada. */
export function cancelarDibujo() {
  medida = false
  if (useMapStore.getState().modoDibujo === 'editar') return cancelarEdicion()
  if (draw) {
    draw.clear()
    draw.setMode('static')
  }
  useMapStore.getState().setModoDibujo(null)
}

async function guardarDibujo(tipo: TipoDibujo, f: GeoJSONStoreFeatures) {
  const sessionId = useSessionStore.getState().sessionId
  if (!sessionId) return avisar('No hay sesión: el dibujo no se pudo guardar.')
  const nombre = nombreParaDibujo(tipo, useMapStore.getState().layers)
  const fc: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: f.geometry, properties: {} }],
  } as GeoJSONFeatureCollection
  try {
    const r = await workspaceApi.crearDibujo(sessionId, nombre, fc)
    const store = useMapStore.getState()
    const id = store.addLayer(r.geojson ?? fc, r.layer_ref?.name ?? nombre, undefined, r.layer_ref?.id)
    useMapStore.setState((s) => ({
      layers: s.layers.map((l) => (l.id === id
        ? { ...l, origen: { capability: CAPACIDAD_DIBUJO, arguments: { geometria: f.geometry.type } } }
        : l)),
    }))
    capaDelUsuario(id, { dibujo: f.geometry.type })
    // FH.9: si el agente había pedido un área, este dibujo es la respuesta
    if (usePedidoMapa.getState().pedido?.modo === 'draw_area') {
      void import('@/lib/responderPedido').then((m) => m.responder({ layerId: id, nombre: r.layer_ref?.name ?? nombre }))
    }
  } catch (err) {
    avisar(`No se pudo guardar el dibujo: ${err instanceof Error ? err.message : String(err)}`)
  }
}

/** Pasa los vértices de un dibujo a terra-draw para editarlos. */
export function editarDibujo(capaId: string) {
  const capa = useMapStore.getState().layers.find((l) => l.id === capaId)
  const d = instancia()
  if (!capa || !d) return
  cancelarEdicion()
  const fids = new Map<string | number, number>()
  const features: GeoJSONStoreFeatures[] = []
  ;(capa.data.features ?? []).forEach((f, i) => {
    const modo = f.geometry ? modoDeEdicion(f.geometry.type) : null
    if (!modo) return
    const id = d.getFeatureId()
    fids.set(id, identidad(f, i))
    features.push({ type: 'Feature', id, geometry: f.geometry, properties: { mode: modo } } as GeoJSONStoreFeatures)
  })
  if (!features.length) return avisar('Este dibujo no tiene vértices editables.')
  d.setMode('select')
  const validos = d.addFeatures(features).filter((v) => v.valid)
  if (!validos.length) return avisar('terra-draw no pudo cargar la geometría para editarla.')
  edicion = { capaId, fids, visibleAntes: capa.visible }
  // Oculta la capa mientras se edita (no es una operación del usuario: no va al registro).
  useMapStore.setState((s) => ({ layers: s.layers.map((l) => (l.id === capaId ? { ...l, visible: false } : l)) }))
  useMapStore.getState().setModoDibujo('editar', capaId)
  d.selectFeature(validos[0].id as string)
  avisar(null)
}

function terminarEdicion() {
  if (!edicion) return
  const { capaId, visibleAntes } = edicion
  edicion = null
  if (draw) {
    draw.clear()
    draw.setMode('static')
  }
  useMapStore.setState((s) => ({ layers: s.layers.map((l) => (l.id === capaId ? { ...l, visible: visibleAntes } : l)) }))
  useMapStore.getState().setModoDibujo(null)
}

export function cancelarEdicion() {
  terminarEdicion()
}

/** Guarda los vértices editados en el workspace y en el mapa (misma capa, mismo dataset). */
export async function guardarEdicion() {
  if (!edicion || !draw) return
  const { capaId, fids } = edicion
  const capa = useMapStore.getState().layers.find((l) => l.id === capaId)
  const sessionId = useSessionStore.getState().sessionId
  if (!capa?.datasetId || !sessionId) return avisar('Este dibujo no está en el workspace: no se puede guardar.')
  const features = draw.getSnapshot()
    .filter((f) => fids.has(f.id as string))
    .map((f) => ({ type: 'Feature', id: fids.get(f.id as string), geometry: f.geometry, properties: {} }))
  try {
    const r = await workspaceApi.editarDataset(sessionId, capa.datasetId, {
      geojson: { type: 'FeatureCollection', features } as GeoJSONFeatureCollection,
    })
    terminarEdicion()
    if (r.geojson) useOperaciones.getState().ejecutar({ op: 'edit_geometry', layer_id: capaId, data: r.geojson }, 'user')
  } catch (err) {
    avisar(`No se pudo guardar la edición: ${err instanceof Error ? err.message : String(err)}`)
  }
}

/** Renombra una capa: en el workspace si tiene dataset (lo ve el agente ahí también) y en el mapa. */
export async function renombrarCapa(capaId: string, nombre: string): Promise<string | null> {
  const capa = useMapStore.getState().layers.find((l) => l.id === capaId)
  const limpio = nombre.trim()
  if (!capa || !limpio || limpio === capa.name) return null
  const sessionId = useSessionStore.getState().sessionId
  try {
    if (capa.datasetId && sessionId) await workspaceApi.editarDataset(sessionId, capa.datasetId, { name: limpio })
  } catch (err) {
    return `No se pudo renombrar: ${err instanceof Error ? err.message : String(err)}`
  }
  useOperaciones.getState().ejecutar({ op: 'rename_layer', layer_id: capaId, args: { nombre: limpio, antes: capa.name } }, 'user')
  return null
}
