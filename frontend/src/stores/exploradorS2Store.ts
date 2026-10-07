/**
 * Estado del explorador Sentinel-2. Vive fuera del componente: el cajón se desmonta al cambiar
 * de cajón y, con el estado local, volver dejaba el panel en blanco y la búsqueda siguiente ya no
 * sabía qué cuadrícula ni qué escena sustituir (se apilaban en el mapa).
 *
 * Se guarda en `sessionStorage` (por pestaña, como la sesión y sus capas, que al recargar vuelven
 * con el MISMO id): sin eso, cada recarga olvidaba sus capas y la búsqueda siguiente sumaba otra
 * cuadrícula. Sin almacenamiento (modo privado), funciona igual hasta recargar.
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { DescargaS2, EscenaS2, Metrica, TeselaS2 } from '@/lib/exploradorS2'
import { ventanaPorDefecto } from '@/lib/exploradorS2'

export type OrdenEscenas = 'menos_nubes' | 'mas_cobertura' | 'reciente'

/** La escena que está en el mapa y cómo se ve (para ajustar su contraste o leer un píxel). */
export interface EscenaVista {
  id: string
  fecha: string
  producto: string
  bbox: [number, number, number, number] | null
  descargas: DescargaS2[]
  /** Contraste aplicado: tres rangos (color) o uno (índice o banda); null = el del servicio. */
  rangos: [number, number][] | null
}

export interface ExploradorS2Estado {
  /** Sesión a la que pertenece este estado: sus capas viven en ella (otra sesión, otro estado). */
  sesion: string | null
  desde: string
  hasta: string
  maxNubes: number
  minCobertura: number
  metrica: Metrica
  minEscenas: number
  /** Mundo (agregados por mes, todas las teselas) o la vista (fechas exactas, mejor escena). */
  modo: 'mundo' | 'vista'
  /** Producto con que se ven las escenas (`product` de imagery_scene_view). */
  producto: string
  escenaVista: EscenaVista | null
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
    sesion: null as string | null, desde, hasta, maxNubes: 100, minCobertura: 10, metrica: 'nubes_min' as Metrica, orden: 'menos_nubes' as OrdenEscenas, minEscenas: 0,
    modo: 'mundo' as 'mundo' | 'vista', producto: 'true_color', escenaVista: null as EscenaVista | null,
    teselas: null, capaGrid: null, tesela: null, escenas: null, rasterPrevio: null,
  }
}

export const useExploradorS2 = create<ExploradorS2Estado>()(persist((set) => ({
  ...inicial(),
  fijar: (cambios) => set(cambios),
  reiniciar: () => set(inicial()),
}), {
  name: 'geo.exploradorS2',
  storage: createJSONStorage(() => window.sessionStorage),
}))
