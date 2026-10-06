/**
 * «Nueva conversación» = una SESIÓN nueva: historial y workspace vacíos.
 *
 * V5 de FH (otras temáticas): solo se reiniciaba el historial de la sesión del backend; sus
 * datasets (círculos, buffers, uniones de turnos anteriores) seguían en la lista que ve el
 * agente, y el punto marcado seguía en el mapa. El agente reutilizó un «Círculo de 5000 m»
 * viejo y respondió 50 sedes donde había 15. Los proyectos guardados no se tocan.
 */
import { useComparacion } from '@/lib/comparacion'
import { useOperaciones } from '@/lib/operaciones'
import { usePedidoMapa } from '@/lib/pedidoMapa'
import { useTiempo } from '@/lib/tiempo'
import { recordarSesion } from '@/lib/workspaceRestore'
import { sessionApi } from '@/services/api'
import { useChatStore, useMapStore, useSessionStore } from '@/stores'
import { logger } from '@/utils/logger'

export async function nuevaConversacion(): Promise<string | null> {
  useChatStore.getState().clearMessages()
  const mapa = useMapStore.getState()
  mapa.clearAllLayers()
  mapa.setPuntoMarcado(null)
  mapa.setSelectedFeature(null)
  useOperaciones.getState().limpiar()
  useComparacion.getState().cerrar()
  useTiempo.getState().ir(null)
  useTiempo.getState().reproducir(false)
  usePedidoMapa.getState().limpiar()
  try {
    const { session_id } = await sessionApi.create()
    recordarSesion(session_id)
    useSessionStore.getState().setSessionId(session_id) // el socket se reconecta al cambiar
    return session_id
  } catch (e) {
    // Sin sesión nueva, al menos el historial de la actual se vacía (lo de antes).
    logger.error('[nuevaConversacion] no se pudo crear la sesión nueva', e)
    const actual = useSessionStore.getState().sessionId
    if (actual) await sessionApi.reset(actual).catch(() => undefined)
    return null
  }
}
