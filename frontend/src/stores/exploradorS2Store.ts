/**
 * Estado del explorador Sentinel-2. Vive fuera del componente: el cajón se desmonta al cambiar
 * de cajón y, con el estado local, volver dejaba el panel en blanco y la búsqueda siguiente ya no
 * sabía qué cuadrícula ni qué escena sustituir (se apilaban en el mapa).
 */
import { create } from 'zustand'

import type { EscenaS2, Metrica, TeselaS2 } from '@/lib/exploradorS2'
import { ventanaPorDefecto } from '@/lib/exploradorS2'

export type OrdenEscenas = 'menos_nubes' | 'mas_cobertura' | 'reciente'

export interface ExploradorS2Estado {
  desde: string
  hasta: string
  maxNubes: number
  minCobertura: number
  metrica: Metrica
  /** Orden de las escenas de la tesela (el de `imagery_catalog_scenes`). */
  orden: OrdenEscenas
  teselas: TeselaS2[] | null
  /** Capa de la cuadrícula en el mapa (la búsqueda nueva la sustituye). */
  capaGrid: string | null
  tesela: TeselaS2 | null
  escenas: EscenaS2[] | null
  /** Raster de la última escena vista (la siguiente lo sustituye). */
  rasterPrevio: string | null
  fijar: (cambios: Partial<Omit<ExploradorS2Estado, 'fijar' | 'reiniciar'>>) => void
  reiniciar: () => void
}

function inicial() {
  const { desde, hasta } = ventanaPorDefecto(new Date())
  return {
    desde, hasta, maxNubes: 100, minCobertura: 10, metrica: 'nubes_min' as Metrica, orden: 'menos_nubes' as OrdenEscenas,
    teselas: null, capaGrid: null, tesela: null, escenas: null, rasterPrevio: null,
  }
}

export const useExploradorS2 = create<ExploradorS2Estado>((set) => ({
  ...inicial(),
  fijar: (cambios) => set(cambios),
  reiniciar: () => set(inicial()),
}))
