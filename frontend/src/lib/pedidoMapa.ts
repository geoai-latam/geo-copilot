/**
 * FH.9 — el agente pide algo EN el mapa (`request_input`): un punto, un área, una capa o
 * elementos. El turno terminó con la pregunta; cuando el usuario responde en el mapa, la
 * consulta ORIGINAL vuelve a enviarse con `map_context.respuesta_mapa` (qué respondió) y el
 * agente sigue. Cancelar también se le dice (para que no vuelva a pedirlo).
 */
import { create } from 'zustand'

import type { MapCommand } from '@/contracts'

export type ModoPedido = 'pick_point' | 'draw_area' | 'pick_layer' | 'pick_features'

export interface PedidoMapa {
  modo: ModoPedido
  pregunta: string
  /** pick_features: la capa donde elegir (si el agente la acotó). */
  capaId: string | null
  /** La consulta del usuario que quedó esperando esta respuesta. */
  consulta: string
}

export interface RespuestaMapa {
  modo: ModoPedido
  pedido: string
  layer_id?: string | null
  cancelado?: boolean
}

interface PedidoState {
  pedido: PedidoMapa | null
  pedir: (p: PedidoMapa) => void
  limpiar: () => void
}

export const usePedidoMapa = create<PedidoState>((set) => ({
  pedido: null,
  pedir: (pedido) => set({ pedido }),
  limpiar: () => set({ pedido: null }),
}))

/** El pedido de una respuesta del agente (su orden `request_input`), si trae. */
export function pedidoDe(cmd: MapCommand, consulta: string): PedidoMapa | null {
  if (cmd.op !== 'request_input') return null
  const a = cmd.args as { mode: ModoPedido; prompt: string }
  return { modo: a.mode, pregunta: a.prompt, capaId: cmd.layer_id ?? null, consulta }
}
