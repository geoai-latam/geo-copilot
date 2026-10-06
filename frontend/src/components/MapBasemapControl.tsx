import { Layers } from 'lucide-react'
import { BASE_MAPS, useBaseMapId, useMapStore, type BaseMapId } from '@/stores/mapStore'

/**
 * MapBasemapControl — selector de mapa base (los 11 basemaps del store).
 *
 * Repone el picker de basemap que vivía en el MapControls del motor de mapa
 * anterior (retirado en la migración a MapLibre). El motor MapLibre ya reacciona
 * al cambio de `baseMapId` intercambiando los tiles del source in-place (ver
 * MapLibreMap), así que aquí solo hace falta un disparador de UI.
 */
export function MapBasemapControl() {
  const baseMapId = useBaseMapId()
  const setBaseMap = useMapStore((s) => s.setBaseMap)
  return (
    <div className="basemap-control" title="Mapa base">
      <Layers size={13} aria-hidden />
      <select
        aria-label="Mapa base"
        value={baseMapId}
        onChange={(e) => setBaseMap(e.target.value as BaseMapId)}
      >
        {BASE_MAPS.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </select>
    </div>
  )
}
