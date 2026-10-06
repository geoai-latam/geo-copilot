/**
 * FH.10 — vistas guardadas (marcadores): una extensión del mapa con nombre. Viven en la
 * pestaña (sessionStorage, por sesión) y en el proyecto (TH.11); el agente las ve en el
 * map_context y va a una con `zoom_to` (bbox), o guarda la actual con `save_view`.
 */
import { create } from 'zustand'

import { getViewBounds } from '@/lib/mapViewport'
import { useMapStore } from '@/stores/mapStore'

export interface Vista {
  id: string
  nombre: string
  bbox: [number, number, number, number]
}

const clave = (sid: string | null) => `geo.vistas.${sid ?? 'sin-sesion'}`

function leer(sid: string | null): Vista[] {
  try {
    const v = JSON.parse(window.sessionStorage.getItem(clave(sid)) ?? '[]')
    return Array.isArray(v) ? v : []
  } catch {
    return []
  }
}

interface VistasState {
  sesion: string | null
  vistas: Vista[]
  cargar: (sid: string | null) => void
  poner: (vistas: Vista[]) => void
}

export const useVistas = create<VistasState>((set, get) => ({
  sesion: null,
  vistas: [],
  cargar: (sid) => set({ sesion: sid, vistas: leer(sid) }),
  poner: (vistas) => {
    try {
      window.sessionStorage.setItem(clave(get().sesion), JSON.stringify(vistas))
    } catch {
      // sin almacenamiento: las vistas duran lo que la pestaña
    }
    set({ vistas })
  },
}))

/** Guarda la vista actual con ese nombre (si ya existe el nombre, la reemplaza). */
export function guardarVista(nombre: string): Vista | null {
  const bbox = getViewBounds()
  const limpio = nombre.trim().slice(0, 80)
  if (!bbox || !limpio) return null
  const vista: Vista = { id: `vista-${Date.now()}`, nombre: limpio, bbox }
  const { vistas, poner } = useVistas.getState()
  poner([...vistas.filter((v) => v.nombre.toLowerCase() !== limpio.toLowerCase()), vista])
  return vista
}

export function irAVista(id: string) {
  const v = useVistas.getState().vistas.find((x) => x.id === id)
  if (v) useMapStore.getState().pedirEncuadre(v.bbox)
}

export function borrarVista(id: string) {
  const { vistas, poner } = useVistas.getState()
  poner(vistas.filter((v) => v.id !== id))
}
