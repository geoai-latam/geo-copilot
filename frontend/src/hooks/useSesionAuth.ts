import { useSyncExternalStore } from 'react'
import { alCambiarSesion, modoAuth, requiereLogin, usuarioActual, type Usuario } from '@/lib/auth'

export interface EstadoSesion {
  modo: ReturnType<typeof modoAuth>
  requiereLogin: boolean
  usuario: Usuario | null
}

let cache: EstadoSesion = { modo: 'ninguno', requiereLogin: false, usuario: null }

function leer(): EstadoSesion {
  const nuevo = { modo: modoAuth(), requiereLogin: requiereLogin(), usuario: usuarioActual() }
  if (nuevo.modo !== cache.modo || nuevo.requiereLogin !== cache.requiereLogin || nuevo.usuario !== cache.usuario) {
    cache = nuevo
  }
  return cache
}

/** F6: la sesión del usuario (se re-renderiza al entrar, salir o caducar). */
export function useSesionAuth(): EstadoSesion {
  return useSyncExternalStore(alCambiarSesion, leer, leer)
}
