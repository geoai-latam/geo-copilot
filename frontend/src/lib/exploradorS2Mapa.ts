/**
 * Explorador Sentinel-2 — lo que toca el mapa y el servicio (el panel y la tarjeta de la escena
 * vista lo comparten). Las tools son las de imagery-mcp, las mismas que usa el agente.
 */
import { mcpApi, type McpRunResult } from '@/services/api'
import { EMPTY_FC, useMapStore, useSessionStore } from '@/stores'
import { capaDelUsuario, useOperaciones } from '@/lib/operaciones'
import { buildMapContext } from '@/utils/mapContext'
import { type Metrica, SERVIDOR_IMAGERY, simbologiaCuadricula } from '@/lib/exploradorS2'
import type { LayerSymbology } from '@/types'

export async function correrImagery(tool: string, args: Record<string, unknown>): Promise<McpRunResult> {
  const sessionId = useSessionStore.getState().sessionId
  if (!sessionId) throw new Error('No hay sesión activa.')
  const res = await mcpApi.run(SERVIDOR_IMAGERY, tool, {
    session_id: sessionId, arguments: args, map_context: buildMapContext(),
  })
  if (!res.success) throw new Error(res.message ?? 'La herramienta no devolvió resultado.')
  return res
}

export const existeCapa = (id: string | null): id is string =>
  !!id && useMapStore.getState().layers.some((l) => l.id === id)

/** Quita una capa si sigue en el mapa (el usuario pudo borrarla a mano). */
export function quitarSiExiste(id: string | null) {
  if (existeCapa(id)) {
    useOperaciones.getState().ejecutar({ op: 'remove_layer', layer_id: id, args: {}, reason: null }, 'user')
  }
}

/** La cuadrícula de un resultado al mapa: inline (la vista) o en teselas del workspace (el mundo
 * entero son ~29.000 polígonos: el núcleo los sirve como MVT). */
export function capaDeCuadricula(res: McpRunResult, nombre: string, simbologia: LayerSymbology): string | null {
  const map = useMapStore.getState()
  const r = res.results
  if (r.tiles) {
    return capaDelUsuario(map.addLayer(EMPTY_FC, nombre, simbologia, r.layer_ref?.id, {
      url: r.tiles.url, sourceLayer: r.tiles.source_layer,
      geometryType: r.tiles.geometry_type ?? null, bbox: r.tiles.bbox ?? null,
      featureCount: r.tiles.feature_count ?? 0, fields: r.tiles.fields ?? [],
    })) ?? null
  }
  if (r.geojson?.features?.length) {
    return capaDelUsuario(map.addLayer(r.geojson, nombre, simbologia, r.layer_ref?.id)) ?? null
  }
  return null
}

/** Relleno de la cuadrícula: se apaga mientras se ve una escena para no teñirla. */
export function rellenoCuadricula(capa: string | null, metrica: Metrica, relleno: boolean) {
  if (existeCapa(capa)) {
    useOperaciones.getState().ejecutar({
      op: 'set_style', layer_id: capa, args: { style: simbologiaCuadricula(metrica, relleno) as never },
      reason: 'explorador Sentinel-2',
    }, 'user')
  }
}

/** Resalta en la cuadrícula la tesela abierta. null la apaga y quita también la selección del
 * clic con que se abrió: su relleno teñiría la escena que se va a ver. */
export function resaltarTesela(capa: string | null, tile: string | null) {
  if (!existeCapa(capa)) return
  useMapStore.getState().setResaltado(capa, tile ? { where: { field: 'tile', op: '=', value: tile }, count: 1, origin: 'query' } : null)
  if (!tile && useMapStore.getState().layers.find((l) => l.id === capa)?.seleccion) {
    useOperaciones.getState().ejecutar({ op: 'clear_selection', layer_id: capa, args: {}, reason: null } as never, 'user')
  }
}
