/**
 * Artefactos de una respuesta → mapa (F4, S4.2/S4.3).
 *
 * Una capa del contrato se añade al store con el tipo de renderer que le toca
 * (inline, teselas MVT, raster XYZ, ArcGIS, WMS); `replaces` la sustituye EN SU
 * SITIO (re-estilo: misma capa, mismo nombre, mismo lugar en el orden); una
 * orden `set_style` re-estila la capa que ya está. Lo demás (tablas, gráficos,
 * estadísticas, informes) va al panel de resultados, no aquí.
 */
import type { Artifact, LayerArtifact, LayerRef, MapCommand, QueryResponse, StyleSpec } from '@/contracts'
import { useOperaciones } from '@/lib/operaciones'
import { pedidoDe, usePedidoMapa } from '@/lib/pedidoMapa'
import { useComparacion } from '@/lib/comparacion'
import { resolverCapa } from '@/lib/referencias'
import { useTiempo } from '@/lib/tiempo'
import { guardarVista } from '@/lib/vistas'
import { pickRestyleTarget } from '@/lib/restyleTarget'
import { EMPTY_FC, esVectorial, useMapStore, type LayerTiles, type OrigenCapa, type RasterLegend } from '@/stores/mapStore'
import type { GeoJSONFeatureCollection, LayerSymbology } from '@/types'

const RESTYLE = new Set(['apply_symbology', 'symbology'])
/** Referencia del agente a la capa en foco (ver `capabilities_mapa.py`). */
const ACTIVA = 'activa'

/** El estilo del contrato es el dialecto de simbología del mapa (mismos campos). */
export function simbologiaDe(style: StyleSpec | null | undefined): LayerSymbology | undefined {
  if (!style) return undefined
  return style as unknown as LayerSymbology
}

function extensionDe(ref: LayerRef) {
  const b = ref.bbox as [number, number, number, number] | null | undefined
  return b ? { xmin: b[0], ymin: b[1], xmax: b[2], ymax: b[3] } : null
}

/** S4.4: la procedencia que el agente verá de vuelta en el map_context (sin sql/código). */
function origenDe(ref: LayerRef): OrigenCapa | null {
  const p = ref.provenance
  return p ? { capability: p.capability, arguments: p.arguments } : null
}

function anadirRaster(ref: LayerRef): string | null {
  const s = ref.storage
  const map = useMapStore.getState()
  const comun = { name: ref.name, extent: extensionDe(ref), origen: origenDe(ref),
                  fecha: ref.time?.start ? String(ref.time.start).slice(0, 10) : null }
  switch (s.kind) {
    case 'raster-tiles':
      return map.addRasterLayer({ ...comun, url: s.url_template,
                                  legend: (s.legend ?? null) as RasterLegend | null, kind: 'raster-xyz' })
    case 'arcgis-image':
      return map.addRasterLayer({ ...comun, url: s.service_url, kind: 'arcgis-image' })
    case 'wms':
      return map.addRasterLayer({ ...comun, url: s.url, kind: 'wms', wmsLayers: s.layers })
    default:
      return null
  }
}

function anadirVector(a: LayerArtifact, reestilo: boolean): string | null { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const ref = a.layer
  const tiles: LayerTiles | undefined = a.tiles
    ? {
        url: a.tiles.url_template,
        sourceLayer: a.tiles.source_layer,
        geometryType: ref.geometry_type ?? null,
        bbox: (ref.bbox as [number, number, number, number] | null | undefined) ?? null,
        featureCount: ref.feature_count ?? 0,
        fields: a.tiles.fields,
      }
    : undefined
  const data = (a.inline as unknown as GeoJSONFeatureCollection | null) ?? null
  if (!data && !tiles) return null // sin forma de dibujarla ahora (referencia pura)
  const simbologia = simbologiaDe(ref.style)
  const datasetId = ref.id.startsWith('ds_') ? ref.id : undefined
  const store = useMapStore.getState()
  // La capa que se re-estila: la que dice el backend (FRT-04, por nombre) o, en un
  // re-estilo sin objetivo explícito, la activa.
  // Solo capas vectoriales se re-estilan (la última del mapa puede ser un raster).
  const objetivo = a.replaces || reestilo ? pickRestyleTarget(store.layers.filter(esVectorial), a.replaces) : undefined
  if (!objetivo) {
    const nueva = store.addLayer(data ?? EMPTY_FC, ref.name, simbologia, datasetId, tiles)
    useOperaciones.getState().ejecutar({ op: 'add_layer', layer_id: nueva }, 'agent')
    return nueva
  }
  // Sustituir EN SU SITIO: mismo nombre (H3 de F0) y mismo lugar en el orden de dibujo.
  const indice = store.layers.findIndex((l) => l.id === objetivo.id)
  store.removeLayer(objetivo.id)
  // Misma capa con datos nuevos (el MISMO dataset, p. ej. con un campo más): conserva lo
  // que el usuario le puso — estilo si no llega otro, filtro, etiquetas, opacidad,
  // visibilidad y selección (V5 en Chrome: «el área de cada lote» perdía la rampa y el filtro).
  const mismaCapa = !!datasetId && objetivo.datasetId === datasetId
  const nueva = useMapStore.getState().addLayer(data ?? EMPTY_FC, objetivo.name, simbologia ?? (mismaCapa ? objetivo.symbology : undefined),
                                               datasetId, tiles)
  // V5 EH.7: la sustituta conserva el ID de la que sustituye (es la misma capa en su sitio).
  // Con un id nuevo, todo enlace de las respuestas a esa capa —hasta el de la respuesta de
  // este mismo turno— quedaba roto («Esa capa ya no está en el mapa»).
  const id = objetivo.id
  useMapStore.setState((s) => ({ layers: s.layers.map((l) => (l.id !== nueva ? l : {
    ...l, id, name: objetivo.name,
    ...(mismaCapa ? {
      filtro: objetivo.filtro ?? null, filtroCount: objetivo.filtroCount ?? null, labelField: objetivo.labelField ?? null,
      opacity: objetivo.opacity, visible: objetivo.visible, seleccion: objetivo.seleccion ?? null, origen: objetivo.origen ?? null,
      ...(simbologia ? {} : { color: objetivo.color }),
    } : {}),
  })) }))
  useMapStore.getState().moveLayer(id, indice)
  // FH.1: queda en el registro; Ctrl+Z devuelve la capa de antes a su sitio.
  useOperaciones.getState().ejecutar({ op: 'replace_layer', layer_id: id, previa: objetivo }, 'agent')
  return id
}

/** FH.10: órdenes de la vista (marcador, cortina, tiempo). `true` si la orden era de estas. */
function aplicarVista(cmd: MapCommand, anadidas: string[]): boolean {
  const a = (cmd.args ?? {}) as Record<string, unknown>
  switch (cmd.op) {
    case 'save_view':
      guardarVista(String(a.nombre ?? ''))
      return true
    case 'compare': {
      const capas = useMapStore.getState().layers
      const izq = resolverCapa({ capa: String(a.left) }, capas, anadidas)
      const der = resolverCapa({ capa: String(a.right) }, capas, anadidas)
      if (izq && der && izq.id !== der.id) useComparacion.getState().abrir(izq.id, der.id)
      return true
    }
    case 'end_compare':
      useComparacion.getState().cerrar()
      return true
    case 'set_time':
      if (a.time) useTiempo.getState().ir(String(a.time).slice(0, 10))
      useTiempo.getState().reproducir(!!a.play)
      return true
    default:
      return false
  }
}

/** Aplica al mapa las capas y órdenes de la respuesta. Devuelve los ids de capa añadidos. */
/** `consulta`: la del usuario en este turno (si el agente pide algo en el mapa, vuelve con la respuesta). */
export function aplicarArtefactos(resp: QueryResponse, consulta = ''): string[] { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const reestilo = RESTYLE.has(resp.intent ?? '')
  const anadidas: string[] = []
  for (const a of resp.artifacts as Artifact[]) {
    if (a.kind === 'layer') {
      const esRaster = a.layer.kind === 'raster'
      const id = esRaster ? anadirRaster(a.layer) : anadirVector(a, reestilo)
      if (id && esRaster) useOperaciones.getState().ejecutar({ op: 'add_layer', layer_id: id }, 'agent')
      if (id) anadidas.push(id)
    } else if (a.kind === 'map_command') {
      // FH.1: toda orden del agente pasa por el mismo reducer que los gestos del
      // usuario: queda en el registro y se deshace igual.
      const cmd = a.command
      // FH.9: un pedido en el mapa no es una operación: pone la UI a esperar la respuesta
      const pedido = pedidoDe(cmd, consulta)
      if (pedido) {
        usePedidoMapa.getState().pedir(pedido)
        continue
      }
      // FH.10: vistas, comparación y tiempo son estado de la vista (no cambian capas)
      if (aplicarVista(cmd, anadidas)) continue
      const store = useMapStore.getState()
      let layerId = cmd.layer_id ?? null
      if (cmd.op === 'set_style') {
        // (el re-estilo decide la capa con la misma regla que antes: FRT-04)
        layerId = pickRestyleTarget(store.layers.filter(esVectorial), cmd.layer_id)?.id ?? null
      } else if (layerId === ACTIVA) {
        // `activa` = la capa que trajo esta respuesta o, si no trajo ninguna, la activa del mapa.
        layerId = anadidas[anadidas.length - 1] ?? store.layers[store.layers.length - 1]?.id ?? null
      }
      if (cmd.op !== 'zoom_to' && !layerId) continue
      useOperaciones.getState().ejecutar({ ...cmd, layer_id: layerId } as MapCommand, 'agent')
    }
  }
  return anadidas
}
