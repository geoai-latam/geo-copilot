/* eslint-disable max-lines -- deuda congelada (F1 del plan de calidad): partir por responsabilidad, no crecer */
import { useProyeccion } from '@/lib/proyeccion'
import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import '@/lib/maplibreWorker'
import 'maplibre-gl/dist/maplibre-gl.css'
import { BASE_MAPS, useBaseMapId, useLayers, useFlyToLayerId, useMapCenter, useMapZoom, useMapStore, type MapLayer } from '@/stores/mapStore'
import { setMapTestState, type LayerProbe } from '@/lib/mapTestState'
import { basemapRasterSpec } from '@/lib/maplibreBasemap'
import { transformRequestApi } from '@/lib/maplibreImagery'
import { cabecerasAuth } from '@/lib/auth'
import { identificar } from '@/lib/identificar'
import { visibleEfectiva } from '@/lib/visibilidad'
import { useComparacion } from '@/lib/comparacion'
import { useTiempo } from '@/lib/tiempo'
import { registrarMapaActivo } from '@/lib/mapaActivo'
import { firmaDeCapa, rendererDe, type RenderCtx } from '@/lib/renderers'
import { idsDeResaltado } from '@/lib/renderers/vector'
import { esDibujo, registrarMapa } from '@/lib/dibujo'
import { useOperaciones } from '@/lib/operaciones'
import { filtroResaltado, tocaLazo, type Origen } from '@/lib/seleccion'
import { useMenuContextual } from '@/lib/menuContextual'
import { usePedidoMapa } from '@/lib/pedidoMapa'
import { responder } from '@/lib/responderPedido'
import { useUIStore } from '@/stores/uiStore'

type Caja = [number, number, number, number]
/** Último encuadre pedido: se repite cuando el panel de resultados cambia el hueco libre. */
let ultimoEncuadre: Caja | null = null

/**
 * Encuadra `b` en la parte del mapa que NO tapa el panel de resultados (S4.3:
 * capa, tabla y gráfico a la vista a la vez; sin esto la capa quedaba detrás).
 */
function encuadrar(map: maplibregl.Map, b: Caja, duration = 1200) {
  ultimoEncuadre = b
  // CI de TH.18: con el panel de la tabla abierto en una ventana estrecha, el relleno (48 + panel)
  // superaba el ancho del mapa y MapLibre NO movía el mapa («Encuadrar» no hacía nada). El relleno
  // nunca deja menos de 80 px libres; en ese caso se encuadra en lo que queda.
  const { clientWidth: ancho, clientHeight: alto } = map.getContainer()
  const derecha = Math.max(0, Math.min(48 + useUIStore.getState().rellenoDerecho, ancho - 48 - 80))
  const vertical = Math.max(0, Math.min(48, (alto - 80) / 2))
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], {
    padding: { top: vertical, bottom: vertical, left: Math.min(48, ancho / 4), right: derecha }, duration,
    maxZoom: 18, // una manzana se ve; un punto no llega al máximo del mapa base
  })
}

/**
 * MapLibreMap — el mapa (MapLibre GL).
 *
 * F4 (S4.2): UN solo `sync` para todas las capas del store, sean del tipo que
 * sean. Cada tipo sabe construir sus specs (`lib/renderers`); aquí solo se
 * aplican, se mantiene el orden de dibujo del store (índice 0 = abajo, también
 * entre raster y vector) y la opacidad y visibilidad comunes. Publica el
 * oráculo de estado para los E2E (`window.__mapTestState`).
 */

const BASEMAP_SOURCE = 'basemap'
// Proxy de teselas del backend para servicios ArcGIS externos (CORS). Mismo
// origen que el resto del API (/api/v1); nginx inyecta la key server-side.
const ARCGIS_PROXY_BASE = '/api/v1/proxy/imagery'

function specFor(baseMapId: string) {
  const cfg = BASE_MAPS.find((b) => b.id === baseMapId) ?? BASE_MAPS[0]
  return basemapRasterSpec(cfg)
}

function contexto(): RenderCtx {
  return { origin: typeof window !== 'undefined' ? window.location.origin : '', arcgisProxyBase: ARCGIS_PROXY_BASE }
}

/** Ids de las capas de estilo de un grupo (en su orden interno). */
function capasDeEstilo(map: maplibregl.Map, sourceId: string): string[] {
  return (map.getStyle().layers ?? [])
    .filter((l) => (l as { source?: string }).source === sourceId)
    .map((l) => l.id)
}

function quitarGrupo(map: maplibregl.Map, sourceId: string) {
  for (const id of capasDeEstilo(map, sourceId)) map.removeLayer(id)
  if (map.getSource(sourceId)) map.removeSource(sourceId)
}

/** Id de la primera capa que NO es el basemap — para reinsertar el basemap DEBAJO de todo. */
function firstNonBasemapLayerId(map: maplibregl.Map): string | undefined {
  for (const l of map.getStyle().layers ?? []) {
    if ((l as { source?: string }).source !== BASEMAP_SOURCE) return l.id
  }
  return undefined
}

function aplicarVisibilidadYOpacidad(map: maplibregl.Map, layer: MapLayer) {
  // FH.10: encima de la del usuario, la de la comparación y la del control de tiempo
  const vis = visibleEfectiva(layer, useMapStore.getState().layers) ? 'visible' : 'none'
  const opac = rendererDe(layer.kind).opacityPaint(layer)
  for (const l of map.getStyle().layers ?? []) {
    if ((l as { source?: string }).source !== layer.id) continue
    map.setLayoutProperty(l.id, 'visibility', vis)
    if (l.id.includes('-sel-')) continue // el resaltado conserva su color (FH.2)
    for (const [tipo, prop, valor] of opac) {
      if (l.type === tipo) map.setPaintProperty(l.id, prop, valor)
    }
  }
}

/** FH.2: una selección del USUARIO pasa por el mismo reducer que las del agente. */
function seleccionar(layerId: string, ids: number[], mode: 'replace' | 'add' | 'toggle', origin: Origen) {
  useOperaciones.getState().ejecutar(
    { op: 'select', layer_id: layerId, args: { ids, mode, origin, where: null, count: null }, reason: null }, 'user')
}

/** El anillo (lon, lat) de la figura trazada: la caja por sus esquinas o el lazo tal cual, cerrado. */
function poligonoDelTrazo(trazo: maplibregl.LngLat[], modo: 'box' | 'lasso' | null): number[][] {
  if (trazo.length < 2) return []
  if (modo === 'box') {
    const a = trazo[0]
    const b = trazo[trazo.length - 1]
    return [[a.lng, a.lat], [b.lng, a.lat], [b.lng, b.lat], [a.lng, b.lat], [a.lng, a.lat]]
  }
  const anillo = trazo.map((p) => [p.lng, p.lat])
  return [...anillo, anillo[0]]
}

function bboxDeAnillo(map: maplibregl.Map, anillo: number[][]): [maplibregl.PointLike, maplibregl.PointLike] {
  const ps = anillo.map((c) => map.project(c as [number, number]))
  return [[Math.min(...ps.map((p) => p.x)), Math.min(...ps.map((p) => p.y))],
          [Math.max(...ps.map((p) => p.x)), Math.max(...ps.map((p) => p.y))]]
}

/** ¿Se pueden añadir fuentes y capas? (el estilo cargado; las fuentes pueden seguir cargando). */
function estiloListo(map: maplibregl.Map): boolean {
  if (map.isStyleLoaded()) return true
  return (map as unknown as { style?: { _loaded?: boolean } }).style?._loaded === true
}

/** FH.2: el filtro del resaltado = la selección de la capa (sin reconstruirla). */
function aplicarSeleccion(map: maplibregl.Map, layer: MapLayer) {
  const filtro = filtroResaltado(layer)
  for (const id of idsDeResaltado(layer.id)) {
    if (map.getLayer(id)) map.setFilter(id, filtro as maplibregl.FilterSpecification)
  }
}

/** El orden de dibujo del mapa = el del store. `moveLayer` sin beforeId lleva al
 * tope: recorriendo de abajo arriba, cada grupo queda encima del anterior. El
 * basemap no se toca (sigue al fondo). */
function aplicarOrden(map: maplibregl.Map, layers: MapLayer[]) {
  for (const layer of layers) {
    for (const id of capasDeEstilo(map, layer.id)) map.moveLayer(id)
  }
  // FH.3: lo que se está dibujando o editando (terra-draw, `td-*`) siempre encima:
  // si no, los vértices de un dibujo quedaban tapados por una capa de resultado.
  for (const l of map.getStyle().layers ?? []) {
    if (l.id.startsWith('td-')) map.moveLayer(l.id)
  }
}

/** Qué hay dibujado de cada capa, para saber si hay que reconstruirla. */
type Gestionadas = Map<string, unknown[]>

function iguales(a: unknown[], b: unknown[]) {
  return a.length === b.length && a.every((v, i) => Object.is(v, b[i]))
}

function syncCapas(map: maplibregl.Map, layers: MapLayer[], gestionadas: Gestionadas) { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const ctx = contexto()
  const actuales = new Set(layers.map((l) => l.id))
  for (const id of Array.from(gestionadas.keys())) {
    if (!actuales.has(id)) {
      quitarGrupo(map, id)
      gestionadas.delete(id)
    }
  }
  let nueva: MapLayer | null = null
  const sondas: LayerProbe[] = []
  let featureCount = 0
  let rendererKind: string | null = null
  for (const layer of layers) {
    const renderer = rendererDe(layer.kind)
    const n = renderer.featureCount(layer)
    featureCount += n
    rendererKind = layer.symbology?.symbology_type ?? rendererKind
    sondas.push({
      id: layer.id, name: layer.name, kind: layer.kind, featureCount: n, color: layer.color,
      rendererKind: layer.symbology?.symbology_type ?? null, opacity: layer.opacity ?? 1, visible: layer.visible,
      seleccionados: layer.seleccion?.count ?? 0,
    })
    // Aislar cada capa: un fallo al pintar una (expresión inválida, estado de
    // WebGL) no aborta el sync de las demás ni el oráculo.
    try {
      const firma = firmaDeCapa(layer)
      const previa = gestionadas.get(layer.id)
      if (previa && !iguales(previa, firma)) {
        quitarGrupo(map, layer.id)
        gestionadas.delete(layer.id)
      }
      if (!gestionadas.has(layer.id)) {
        const { source, layers: specs } = renderer.buildSpecs(layer, ctx)
        map.addSource(layer.id, source as never)
        for (const spec of specs) map.addLayer(spec as unknown as maplibregl.LayerSpecification)
        if (!previa) nueva = layer
        gestionadas.set(layer.id, firma)
      }
      aplicarVisibilidadYOpacidad(map, layer)
      aplicarSeleccion(map, layer)
    } catch (err) {
      console.error('[MapLibreMap] fallo al renderizar la capa', layer.id, err)
    }
  }
  try {
    aplicarOrden(map, layers)
  } catch (err) {
    console.error('[MapLibreMap] fallo al ordenar las capas', err)
  }
  // Vuela a la capa recién añadida (si el store no tiene ya un flyTo pendiente para ella).
  // Un dibujo NO: el usuario lo acaba de trazar donde está mirando (V5 FH.3: el
  // mapa saltaba a zoom 18 sobre el polígono recién dibujado).
  if (nueva && !esDibujo(nueva) && useMapStore.getState().flyToLayerId !== nueva.id) {
    const b = rendererDe(nueva.kind).bounds(nueva)
    if (b) encuadrar(map, b as Caja)
  }
  setMapTestState({ engine: 'maplibre', featureCount, rendererKind, layers: sondas })
}

export function MapLibreMap() {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const managedRef = useRef<Gestionadas>(new Map())
  const attribCtrlRef = useRef<maplibregl.AttributionControl | null>(null)
  const baseMapId = useBaseMapId()
  const layers = useLayers()
  const flyToLayerId = useFlyToLayerId()
  const clearFlyToLayer = useMapStore((s) => s.clearFlyToLayer)
  const mapCenter = useMapCenter()
  const mapZoom = useMapZoom()
  const viewInitRef = useRef(false)
  useProyeccion(mapRef)

  // Init una sola vez.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const spec = specFor(baseMapId)
    // Cámara inicial desde el store (única fuente de verdad): así map y store
    // coinciden en el montaje y el efecto de re-centrar no dispara un flyTo
    // espurio (incluido el doble-montaje de StrictMode en dev).
    const { mapCenter: initCenter, mapZoom: initZoom } = useMapStore.getState()
    const map = new maplibregl.Map({
      container: containerRef.current,
      center: initCenter,
      zoom: initZoom,
      attributionControl: false,
      // FH.2: Shift es de la selección (Shift+clic suma; la caja es la nuestra).
      boxZoom: false,
      // F6: el token del usuario en las peticiones a la API (teselas del workspace y del
      // proxy de imágenes); nunca a los basemaps de otros orígenes.
      transformRequest: (url) => transformRequestApi(url, cabecerasAuth()),
      style: {
        version: 8,
        sources: {
          [BASEMAP_SOURCE]: {
            type: 'raster',
            tiles: spec.tiles,
            tileSize: 256,
            attribution: spec.attribution,
            maxzoom: spec.maxzoom,
          },
        },
        layers: [{ id: 'basemap-layer', type: 'raster', source: BASEMAP_SOURCE }],
      },
    })
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    attribCtrlRef.current = new maplibregl.AttributionControl({ compact: true })
    map.addControl(attribCtrlRef.current, 'bottom-right')
    mapRef.current = map
    // Puente de viewport (solo lectura) para map_context — SIEMPRE disponible,
    // es la ruta de datos de prod ("qué hay en esta zona"), ver lib/mapViewport.
    ;(window as unknown as { __mapViewport?: { getBounds: () => maplibregl.LngLatBounds } }).__mapViewport = {
      getBounds: () => map.getBounds(),
    }
    // La instancia completa del mapa (mutable) solo en DEV/E2E: arnés de paridad
    // (FND-E2E-HARNESS) + debug. En prod no se filtra el handle global.
    if (import.meta.env.DEV || import.meta.env.VITE_E2E === 'true') {
      (window as unknown as { __mlmap?: maplibregl.Map }).__mlmap = map
    }
    setMapTestState({ engine: 'maplibre', featureCount: 0, rendererKind: null })

    // Mantiene el store en sync con la cámara real (map_context.viewport.zoom
    // deja de quedar estático). Guard de near-igualdad evita pelear con flyTo.
    map.on('moveend', () => {
      const c = map.getCenter()
      useMapStore.getState().setMapView([c.lng, c.lat], map.getZoom())
    })

    // MAP-PICKING-IDENTIFY (vector): click sobre un feature GeoJSON → popup.
    // queryRenderedFeatures devuelve lo pintado en el punto; nos quedamos con
    // el primero cuyo source es una capa GeoJSON gestionada.
    // Solo las capas vectoriales se identifican así (los raster no tienen features).
    const esPicable = (source: unknown) => {
      if (typeof source !== 'string') return false
      const capa = useMapStore.getState().layers.find((l) => l.id === source)
      return !!capa && rendererDe(capa.kind).pickable
    }
    registrarMapa(map) // FH.3: terra-draw dibuja sobre este mapa
    registrarMapaActivo(map) // FH.10: la cortina de comparación sigue su cámara
    map.on('click', (e) => {
      // el trazo de caja/lazo no es un clic; mientras se dibuja, el clic es de terra-draw
      if (useMapStore.getState().modoSeleccion || useMapStore.getState().modoDibujo) return
      const store = useMapStore.getState()
      // FH.9: si el agente pidió un punto, este clic es SOLO la respuesta. V5 (otra temática):
      // sobre una capa de puntos el clic también seleccionaba el punto de debajo, y el agente
      // tomó esa selección (1 elemento) como el alcance de «a menos de 5 km de aquí».
      if (usePedidoMapa.getState().pedido?.modo === 'pick_point') {
        store.setPuntoMarcado({ lon: e.lngLat.lng, lat: e.lngLat.lat })
        void responder()
        return
      }
      const hit = map.queryRenderedFeatures(e.point).find((f) => esPicable(f.source))
      // FH.2: el clic sobre un elemento lo selecciona (Shift: lo añade o lo quita).
      if (hit && typeof hit.source === 'string' && typeof hit.id === 'number') {
        seleccionar(hit.source, [hit.id], e.originalEvent.shiftKey ? 'toggle' : 'replace', 'click')
      }
      // S4.4 (E4.5): el click es el «aquí» del usuario; viaja en el map_context.
      store.setPuntoMarcado({ lon: e.lngLat.lng, lat: e.lngLat.lat })
      // FH.10: identificar TODAS las capas en el punto (vector de encima por capa + rasters).
      // Con Shift el clic es «sumar a la selección»: sin popup (V5 FH.16: el popup de cada clic
      // tapaba los lotes siguientes y la mitad de los Shift+clic caían sobre él).
      if (e.originalEvent.shiftKey) store.setSelectedFeature(null)
      else void identificar(map, e, map.queryRenderedFeatures(e.point).filter((f) => esPicable(f.source)))
    })
    // FH.8: clic derecho sobre un elemento → queda seleccionado (si no lo estaba) y se abre el
    // menú con las acciones que admite su tipo de geometría.
    map.on('contextmenu', (e) => {
      if (useMapStore.getState().modoSeleccion || useMapStore.getState().modoDibujo) return
      const hit = map.queryRenderedFeatures(e.point).find((f) => esPicable(f.source))
      if (!hit || typeof hit.source !== 'string' || typeof hit.id !== 'number') return
      e.originalEvent.preventDefault()
      const capa = useMapStore.getState().layers.find((l) => l.id === hit.source)
      if (!capa?.seleccion?.ids?.includes(hit.id)) seleccionar(hit.source, [hit.id], 'replace', 'click')
      useMenuContextual.getState().abrir(
        { capaId: hit.source, alcance: 'seleccion', geometria: hit.geometry?.type ?? null },
        e.originalEvent.clientX, e.originalEvent.clientY)
    })
    // FH.2: caja y lazo. Se traza arrastrando; al soltar, se selecciona por capa
    // lo que toca la figura (Shift: se suma a lo que había). Un solo uso.
    let trazo: maplibregl.LngLat[] = []
    let inicio: maplibregl.Point | null = null
    const dibujarTrazo = () => {
      const src = map.getSource('seleccion-trazo') as maplibregl.GeoJSONSource | undefined
      const anillo = poligonoDelTrazo(trazo, useMapStore.getState().modoSeleccion)
      const datos = { type: 'FeatureCollection', features: anillo.length > 3
        ? [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [anillo] } }] : [] }
      if (src) src.setData(datos as never)
      else {
        map.addSource('seleccion-trazo', { type: 'geojson', data: datos as never })
        map.addLayer({ id: 'seleccion-trazo-fill', type: 'fill', source: 'seleccion-trazo',
                       paint: { 'fill-color': '#ffb000', 'fill-opacity': 0.12 } })
        map.addLayer({ id: 'seleccion-trazo-line', type: 'line', source: 'seleccion-trazo',
                       paint: { 'line-color': '#ffb000', 'line-width': 2, 'line-dasharray': [2, 1] } })
      }
    }
    map.on('mousedown', (e) => {
      if (!useMapStore.getState().modoSeleccion) return
      e.preventDefault()
      map.dragPan.disable()
      inicio = e.point
      trazo = [e.lngLat]
    })
    map.on('mousemove', (e) => {
      if (!inicio) return
      trazo.push(e.lngLat)
      dibujarTrazo()
    })
    map.on('mouseup', (e) => {
      if (!inicio) return
      const modo = useMapStore.getState().modoSeleccion
      const anillo = poligonoDelTrazo(trazo, modo)
      const caja: [maplibregl.PointLike, maplibregl.PointLike] = [
        [Math.min(inicio.x, e.point.x), Math.min(inicio.y, e.point.y)],
        [Math.max(inicio.x, e.point.x), Math.max(inicio.y, e.point.y)],
      ]
      inicio = null
      trazo = []
      map.dragPan.enable()
      useMapStore.getState().setModoSeleccion(null)
      dibujarTrazo()
      if (anillo.length < 4) return
      const porCapa = new Map<string, Set<number>>()
      const lazoPantalla = modo === 'lasso'
      for (const f of map.queryRenderedFeatures(lazoPantalla ? bboxDeAnillo(map, anillo) : caja)) {
        if (!esPicable(f.source) || typeof f.id !== 'number' || typeof f.source !== 'string') continue
        if (!tocaLazo(f.geometry as never, anillo)) continue
        if (!porCapa.has(f.source)) porCapa.set(f.source, new Set())
        porCapa.get(f.source)!.add(f.id)
      }
      for (const [capa, ids] of porCapa) {
        seleccionar(capa, [...ids], e.originalEvent.shiftKey ? 'add' : 'replace', modo ?? 'box')
      }
    })

    // Cursor de puntero sobre features (UX, paridad con el motor anterior).
    map.on('mousemove', (e) => {
      if (useMapStore.getState().modoDibujo) return // el cursor lo pone terra-draw
      const over = map.queryRenderedFeatures(e.point).some((f) => esPicable(f.source))
      map.getCanvas().style.cursor = over ? 'pointer' : ''
    })

    // Sincroniza cualquier capa ya presente cuando el estilo esté listo.
    // El Set se crea UNA vez (`useRef(new Set())`, arriba) y nunca se
    // reasigna, así que capturarlo aquí es idéntico en comportamiento y
    // además satisface la regla del hook: la limpieza no debe leer `.current`,
    // que para entonces podría apuntar a otra cosa.
    const capasGestionadas = managedRef.current
    map.once('load', () => syncCapas(map, useMapStore.getState().layers, capasGestionadas))
    return () => {
      registrarMapa(null)
      map.remove()
      mapRef.current = null
      capasGestionadas.clear()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Cambio de basemap: re-crea el source raster. No basta `setTiles` (solo
  // cambia los tiles, no la atribución → el AttributionControl seguiría
  // mostrando la fuente anterior). Se re-inserta el layer DEBAJO de todo.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const spec = specFor(baseMapId)
    const apply = () => {
      if (map.getLayer('basemap-layer')) map.removeLayer('basemap-layer')
      if (map.getSource(BASEMAP_SOURCE)) map.removeSource(BASEMAP_SOURCE)
      map.addSource(BASEMAP_SOURCE, {
        type: 'raster',
        tiles: spec.tiles,
        tileSize: 256,
        attribution: spec.attribution,
        maxzoom: spec.maxzoom,
      } as maplibregl.RasterSourceSpecification)
      // beforeId = primera capa no-basemap → el basemap queda al fondo.
      map.addLayer(
        { id: 'basemap-layer', type: 'raster', source: BASEMAP_SOURCE } as maplibregl.LayerSpecification,
        firstNonBasemapLayerId(map),
      )
      // El AttributionControl cachea y no refresca su texto al re-crear el
      // source; se re-crea para que muestre la atribución de la fuente nueva.
      if (attribCtrlRef.current) {
        try {
          map.removeControl(attribCtrlRef.current)
        } catch {
          /* ya removido */
        }
      }
      attribCtrlRef.current = new maplibregl.AttributionControl({ compact: true })
      map.addControl(attribCtrlRef.current, 'bottom-right')
    }
    if (map.isStyleLoaded()) apply()
    else map.once('idle', apply)
  }, [baseMapId])

  // Un solo sync para todas las capas (cualquier tipo): altas, bajas, estilo,
  // visibilidad, opacidad y orden de dibujo.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    // Las capas de AHORA, no las del render que programó el sync: un sync diferido
    // hasta `idle` con la lista vieja (vacía, al arrancar) borraba la capa que
    // entró mientras tanto (V2 FH.3: el dibujo desaparecía una de cada tres veces).
    const run = () => syncCapas(map, useMapStore.getState().layers, managedRef.current)
    // Basta con que el ESTILO esté cargado (addSource/addLayer lo exigen); isStyleLoaded()
    // además espera a que terminen de cargar TODAS las fuentes.
    if (estiloListo(map)) run()
    else map.once('idle', run)
  }, [layers])

  // fitBounds al área de la capa cuando el store lo pide.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !flyToLayerId) return
    const layer = layers.find((l) => l.id === flyToLayerId)
    const b = layer ? rendererDe(layer.kind).bounds(layer) : null
    if (b) encuadrar(map, b as Caja)
    clearFlyToLayer()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flyToLayerId])

  // FH.1: `zoom_to` con extensión (sin capa): el agente encuadra una zona.
  // FH.10: la comparación y el control de tiempo cambian lo que se dibuja (no el store)
  useEffect(() => {
    const reaplicar = () => {
      const map = mapRef.current
      if (!map) return
      for (const l of useMapStore.getState().layers) {
        try { aplicarVisibilidadYOpacidad(map, l) } catch { /* capa aún no montada */ }
      }
    }
    const a = useComparacion.subscribe(reaplicar)
    const b = useTiempo.subscribe(reaplicar)
    return () => { a(); b() }
  }, [])

  const encuadrePedido = useMapStore((s) => s.encuadrePedido)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !encuadrePedido) return
    encuadrar(map, encuadrePedido as Caja)
    useMapStore.getState().pedirEncuadre(null)
  }, [encuadrePedido])

  // El «aquí» se ve: un marcador en el último punto donde el usuario hizo click.
  const puntoMarcado = useMapStore((s) => s.puntoMarcado)
  const marcadorRef = useRef<maplibregl.Marker | null>(null)
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!puntoMarcado) {
      marcadorRef.current?.remove()
      marcadorRef.current = null
      return
    }
    if (!marcadorRef.current) {
      const el = document.createElement('div')
      el.className = 'punto-marcado'
      el.title = 'Punto marcado: el «aquí» de tus preguntas'
      el.setAttribute('data-testid', 'punto-marcado')
      marcadorRef.current = new maplibregl.Marker({ element: el })
    }
    marcadorRef.current.setLngLat([puntoMarcado.lon, puntoMarcado.lat]).addTo(map)
  }, [puntoMarcado])

  // El panel de resultados se abre/cierra: re-encuadrar lo último en el hueco libre.
  const rellenoDerecho = useUIStore((s) => s.rellenoDerecho)
  useEffect(() => {
    const map = mapRef.current
    if (map && ultimoEncuadre) encuadrar(map, ultimoEncuadre, 400)
  }, [rellenoDerecho])

  // Re-centrar la cámara cuando el store cambia mapCenter/mapZoom (botón "vista
  // inicial" del panel). Se salta el primer render para no pisar el encuadre de
  // arranque (Colombia), y el guard de near-igualdad rompe el lazo con moveend.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!viewInitRef.current) {
      viewInitRef.current = true
      return
    }
    const c = map.getCenter()
    const near =
      Math.abs(c.lng - mapCenter[0]) < 1e-4 &&
      Math.abs(c.lat - mapCenter[1]) < 1e-4 &&
      Math.abs(map.getZoom() - mapZoom) < 0.01
    if (near) return
    map.flyTo({ center: mapCenter, zoom: mapZoom, duration: 1000 })

  }, [mapCenter, mapZoom])

  return <div ref={containerRef} data-testid="maplibre-map" style={{ position: 'absolute', inset: 0 }} />
}
