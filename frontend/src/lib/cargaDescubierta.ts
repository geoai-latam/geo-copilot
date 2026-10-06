/**
 * Cargar al mapa un servicio descubierto: lo comparten el panel «Descubrir» y las tarjetas de
 * servicios del chat (antes cada uno repetía la carga, y los dos pedían 2000 elementos).
 *
 * - Servicio con varias capas: no se adivina la 0, se devuelven sus capas para que el usuario elija.
 * - La capa llega COMPLETA (paginada en el servidor MCP). Si es grande, el backend la deja en el
 *   workspace y responde teselas: el navegador pide solo lo visible, como desde el chat.
 */
import { discoveryApi, type WorkspaceCapa } from '@/services/api'
import { EMPTY_FC, useMapStore, type LayerTiles } from '@/stores/mapStore'
import { capaDelUsuario } from '@/lib/operaciones'
import { necesitaElegirCapa } from '@/components/discoveryPanel.helpers'
import type { DiscoveryLayer, DiscoveryLoadResponse, HubItem } from '@/types/discovery'

export type Carga =
  | { estado: 'elegir_capa'; capas: DiscoveryLayer[] }
  | { estado: 'cargada'; mensaje: string; layerId: string | null }

export function tilesDeRespuesta(t: WorkspaceCapa['tiles'] | undefined): LayerTiles | undefined {
  if (!t) return undefined
  return {
    url: t.url,
    sourceLayer: t.source_layer,
    geometryType: t.geometry_type,
    bbox: t.bbox,
    featureCount: t.feature_count ?? 0,
    fields: t.fields ?? [],
  }
}

const n = (x: number) => x.toLocaleString('es-CO')

/** Lo que se le dice al usuario: cuántos elementos y, si no vino todo, que es una muestra. */
export function mensajeDeCarga(r: DiscoveryLoadResponse): string {
  if (r.type === 'imagery') return `Cargué «${r.name}» al mapa (servicio de imágenes).`
  const cargados = r.tiles?.feature_count ?? r.feature_count ?? r.geojson?.features?.length ?? 0
  const unidad = cargados === 1 ? 'elemento' : 'elementos'
  const muestra = r.total_available && r.total_available > cargados
    ? ` El servicio tiene ${n(r.total_available)}: es una muestra, no el total.`
    : ''
  return `Cargué «${r.name}» al mapa (${n(cargados)} ${unidad}).${muestra}`
}

export async function cargarDescubierto( // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  item: HubItem,
  opciones: { sessionId?: string; capa?: DiscoveryLayer } = {},
): Promise<Carga> {
  let capa = opciones.capa
  if (!capa && necesitaElegirCapa(item)) {
    const capas = await discoveryApi.layers(item.service_url)
    if (capas.length > 1) return { estado: 'elegir_capa', capas }
    if (capas.length === 1) capa = capas[0]
  }
  const r = await discoveryApi.load(item, {
    sessionId: opciones.sessionId,
    ...(capa ? { layerId: capa.id, layerName: capa.nombre } : {}),
  })
  const mapa = useMapStore.getState()
  let layerId: string | null = null
  const tiles = tilesDeRespuesta(r.tiles)
  if (r.type === 'geojson' && (tiles || r.geojson)) {
    layerId = mapa.addLayer(r.geojson ?? EMPTY_FC, r.name, r.symbology ?? undefined, r.dataset_id ?? undefined, tiles)
  } else if (r.type === 'imagery' && r.imagery) {
    const ext = r.extent && typeof r.extent === 'object' && !Array.isArray(r.extent)
      ? (r.extent as { xmin: number; ymin: number; xmax: number; ymax: number })
      : null
    layerId = mapa.addRasterLayer({ url: r.imagery.service_url, name: r.name, extent: ext })
  }
  capaDelUsuario(layerId)
  return { estado: 'cargada', mensaje: mensajeDeCarga(r), layerId }
}
