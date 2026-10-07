/**
 * Globo o plano: la proyección del mapa. El explorador Sentinel-2 la pide en globo para ver la
 * cuadrícula del mundo entero sin la deformación de Mercator cerca de los polos.
 */
import { useEffect, type RefObject } from 'react'
import type * as maplibregl from 'maplibre-gl'

import { useMapStore } from '@/stores/mapStore'

export type Proyeccion = 'mercator' | 'globe'

export function useProyeccion(mapRef: RefObject<maplibregl.Map | null>): void {
  const proyeccion = useMapStore((s) => s.proyeccion)
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const aplicar = () => map.setProjection({ type: proyeccion })
    if (map.isStyleLoaded()) aplicar()
    else map.once('idle', aplicar)
  }, [mapRef, proyeccion])
}
