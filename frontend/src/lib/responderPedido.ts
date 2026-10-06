/** FH.9 — responder EN el mapa a lo que pidió el agente: reenvía la consulta original con la respuesta. */
import { runQuery } from '@/lib/runQuery'
import { usePedidoMapa, type ModoPedido, type RespuestaMapa } from '@/lib/pedidoMapa'

const ETIQUETA: Record<ModoPedido, (capa?: string) => string> = {
  pick_point: () => '📍 Punto marcado en el mapa',
  draw_area: (c) => `✏️ Área dibujada en el mapa${c ? ` (${c})` : ''}`,
  pick_layer: (c) => `🗂️ Capa elegida: ${c ?? ''}`,
  pick_features: (c) => `☑️ Elementos seleccionados${c ? ` en ${c}` : ''}`,
}

/** Responde el pedido en curso: reenvía la consulta original con la respuesta. */
export async function responder(parcial: { layerId?: string | null; nombre?: string; cancelado?: boolean } = {}) {
  const p = usePedidoMapa.getState().pedido
  if (!p) return
  usePedidoMapa.getState().limpiar()
  const respuestaMapa: RespuestaMapa = {
    modo: p.modo, pedido: p.pregunta,
    ...(parcial.layerId ? { layer_id: parcial.layerId } : {}),
    ...(parcial.cancelado ? { cancelado: true } : {}),
  }
  await runQuery(p.consulta, {
    respuestaMapa,
    etiqueta: parcial.cancelado ? '✖ No quise señalarlo en el mapa' : ETIQUETA[p.modo](parcial.nombre),
  })
}
