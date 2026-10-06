/**
 * FH.11 — proyectos: guardar el mapa (capas con estilo, filtro, selección y fecha; vistas;
 * cámara), la conversación y el registro, y reabrirlos. Abrir uno escribe su estado como el de
 * la pestaña (su sesión) y recarga: el arranque normal lo restaura; el backend ya reconstruyó
 * la conversación del agente con ese id (recuerda).
 */
import { useOperaciones } from '@/lib/operaciones'
import { useVistas } from '@/lib/vistas'
import { capasGuardables, escribirEstadoDeSesion, mensajesGuardables } from '@/lib/workspaceRestore'
import { proyectosApi } from '@/services/api'
import { useChatStore } from '@/stores/chatStore'
import { useMapStore } from '@/stores/mapStore'
import { useSessionStore } from '@/stores/sessionStore'

/** El estado del proyecto: lo que hay AHORA en la pestaña. */
export function estadoActual() {
  const m = useMapStore.getState()
  return {
    capas: capasGuardables(m.layers),
    vistas: useVistas.getState().vistas,
    chat: mensajesGuardables(useChatStore.getState().messages),
    camara: { center: m.mapCenter, zoom: m.mapZoom },
    basemap: m.baseMapId,
    // el registro de operaciones, para leerlo (Ctrl+Z no cruza entre aperturas)
    registro: useOperaciones.getState().registro.slice(-100).map((e) => ({
      op: e.op, layer_id: e.layer_id, layer_name: e.layer_name, author: e.author, at: e.at, deshecha: e.deshecha,
    })),
  }
}

export async function guardarProyecto(nombre: string): Promise<{ id: string; nombre: string }> {
  const sid = useSessionStore.getState().sessionId
  if (!sid) throw new Error('No hay sesión que guardar')
  return proyectosApi.guardar(sid, nombre.trim(), estadoActual())
}

export async function abrirProyecto(id: string, recargar: () => void = () => window.location.reload()) {
  const p = await proyectosApi.abrir(id)
  const e = p.estado as Parameters<typeof escribirEstadoDeSesion>[1]
  escribirEstadoDeSesion(p.session_id, e)
  recargar()
}
