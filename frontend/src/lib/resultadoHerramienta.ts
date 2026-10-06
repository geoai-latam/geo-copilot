/**
 * El resultado de ejecutar una capacidad desde la interfaz (panel de herramientas, menú
 * contextual) → al mapa, igual que la respuesta de /query: capa del workspace (inline o
 * teselas) o raster del servicio. Las capas quedan en el registro como del usuario.
 */
import type { McpRunResult } from '@/services/api'
import { EMPTY_FC, useMapStore } from '@/stores/mapStore'
import { capaDelUsuario, useOperaciones } from '@/lib/operaciones'

/** Añade al mapa lo que trajo `res`. `rasterPrevio`: raster que sustituye (el del panel). */
export function aplicarResultado(res: McpRunResult, nombrePorDefecto: string, rasterPrevio?: string | null): { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  capa: string | null; raster: string | null
} {
  const map = useMapStore.getState()
  const r = res.results
  const nombre = r.layer_name ?? r.layer_ref?.name ?? nombrePorDefecto
  let capa: string | null = null
  if (r.tiles) {
    capa = capaDelUsuario(map.addLayer(EMPTY_FC, nombre, undefined, r.layer_ref?.id, {
      url: r.tiles.url, sourceLayer: r.tiles.source_layer,
      geometryType: r.tiles.geometry_type ?? null, bbox: r.tiles.bbox ?? null,
      featureCount: r.tiles.feature_count ?? 0, fields: r.tiles.fields ?? [],
    })) ?? null
  } else if (r.geojson) {
    capa = capaDelUsuario(map.addLayer(r.geojson, nombre, undefined, r.layer_ref?.id)) ?? null
  }
  let raster: string | null = null
  if (r.external_imagery?.service_url) {
    if (rasterPrevio) {
      useOperaciones.getState().ejecutar({ op: 'remove_layer', layer_id: rasterPrevio, args: {}, reason: null }, 'user')
    }
    raster = capaDelUsuario(map.addRasterLayer({
      url: r.external_imagery.service_url, name: r.external_imagery.name ?? nombre,
      extent: r.external_imagery.extent ?? null, legend: r.external_imagery.legend ?? null,
      origen: r.external_imagery.provenance
        ? { capability: r.external_imagery.provenance.capability, arguments: r.external_imagery.provenance.arguments }
        : null,
    })) ?? null
  }
  return { capa, raster }
}
