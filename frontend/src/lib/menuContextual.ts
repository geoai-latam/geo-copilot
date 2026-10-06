/** FH.8 — estado del menú contextual (qué se señaló y dónde abrirlo). */
import { create } from 'zustand'

import type { Objetivo } from '@/components/MenuContextual.helpers'

interface MenuState {
  abierto: (Objetivo & { x: number; y: number }) | null
  abrir: (o: Objetivo, x: number, y: number) => void
  cerrar: () => void
}

export const useMenuContextual = create<MenuState>((set) => ({
  abierto: null,
  abrir: (o, x, y) => set({ abierto: { ...o, x, y } }),
  cerrar: () => set({ abierto: null }),
}))
