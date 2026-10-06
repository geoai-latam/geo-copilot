/**
 * F6 (S6.1) — inicio de sesión con OIDC (código de autorización + PKCE).
 *
 * La configuración la da el backend (`GET /api/v1/auth/config`): el mismo emisor con el que
 * valida los tokens. Sin OIDC (desarrollo) no hay login y las peticiones van sin token.
 *
 * El token viaja como `Authorization: Bearer` en `fetch` (api.ts), en las teselas de MapLibre y
 * al pedir el ticket del WebSocket (el socket se abre con el ticket, nunca con el token).
 *
 * Los tokens se guardan en localStorage (no sessionStorage) para que una pestaña nueva de la app
 * no obligue a entrar otra vez; el de acceso vive 5 minutos y se renueva con el de refresco.
 */
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'

export type ModoAuth = 'oidc' | 'api_key' | 'ninguno'

export interface ConfigAuth {
  modo: ModoAuth
  issuer?: string
  client_id?: string
}

export interface Usuario {
  sub: string
  org_id: string
  rol: string | null
  nombre: string
  via: string
}

type Oyente = () => void

let modo: ModoAuth = 'ninguno'
let manager: UserManager | null = null
let usuarioOidc: User | null = null
const oyentes = new Set<Oyente>()

function avisar(): void {
  oyentes.forEach((f) => f())
}

/** Suscribirse a cambios de sesión (entró, salió, caducó). Devuelve la baja. */
export function alCambiarSesion(f: Oyente): () => void {
  oyentes.add(f)
  return () => oyentes.delete(f)
}

export function modoAuth(): ModoAuth {
  return modo
}

/** ¿Hace falta iniciar sesión antes de usar la app? */
export function requiereLogin(): boolean {
  return modo === 'oidc' && (!usuarioOidc || usuarioOidc.expired === true)
}

/** Cabeceras de autenticación para una petición al backend (vacías sin OIDC). */
export function cabecerasAuth(): Record<string, string> {
  const token = usuarioOidc && !usuarioOidc.expired ? usuarioOidc.access_token : null
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function esCallback(url: URL): boolean {
  return url.searchParams.has('code') && url.searchParams.has('state')
}

/** Esperas (ms) entre intentos de leer la configuración si el backend no responde (≈15 s en total). */
export const ESPERAS_CONFIG_MS = [500, 1000, 2000, 4000, 8000]

/**
 * La configuración de identidad, o null si el backend NO RESPONDE (red caída o 5xx tras los reintentos).
 *
 * Rama arcgis-busqueda (V5): con el backend reiniciándose, un 502 dejaba `modo: 'ninguno'` y la app
 * se mostraba como si no hubiera login — «Desconectado», sin usuario y sin forma de entrar hasta
 * recargar (el backend seguía rechazando todo con 401). Un 4xx sí es una respuesta: backend sin OIDC.
 */
async function leerConfig(fetchImpl: typeof fetch, esperas: number[]): Promise<ConfigAuth | null> {
  for (let intento = 0; ; intento++) {
    try {
      const r = await fetchImpl('/api/v1/auth/config')
      if (r.ok) return (await r.json()) as ConfigAuth
      if (r.status < 500) return { modo: 'ninguno' }
    } catch {
      // sin red / sin backend: se reintenta
    }
    if (intento >= esperas.length) return null
    await new Promise((ok) => setTimeout(ok, esperas[intento]))
  }
}

/**
 * Arranque: lee la configuración, completa el regreso del proveedor si lo es, y recupera la
 * sesión guardada (renovándola si el token de acceso caducó). Nunca lanza. Sin OIDC, la app sigue
 * como antes; si el backend no responde, `'sin_servidor'` (no se adivina que no hay login).
 */
export async function iniciarAuth(
  fetchImpl: typeof fetch = fetch, esperas: number[] = ESPERAS_CONFIG_MS,
): Promise<ModoAuth | 'sin_servidor'> {
  const leida = await leerConfig(fetchImpl, esperas)
  if (leida === null) return 'sin_servidor'
  const cfg: ConfigAuth = leida
  modo = cfg.modo
  if (cfg.modo !== 'oidc' || !cfg.issuer || !cfg.client_id) return modo

  const origen = window.location.origin
  manager = new UserManager({
    authority: cfg.issuer,
    client_id: cfg.client_id,
    redirect_uri: `${origen}/`,
    post_logout_redirect_uri: `${origen}/`,
    response_type: 'code',
    scope: 'openid profile email',
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.localStorage }),
  })
  manager.events.addUserLoaded((u) => { usuarioOidc = u; avisar() })
  manager.events.addUserUnloaded(() => { usuarioOidc = null; avisar() })
  manager.events.addSilentRenewError(() => { usuarioOidc = null; avisar() })

  const url = new URL(window.location.href)
  if (esCallback(url)) {
    try {
      usuarioOidc = await manager.signinRedirectCallback()
    } catch {
      usuarioOidc = null
    }
    // quitar code/state de la barra (y del historial): no deben poder reutilizarse
    const destino = (usuarioOidc?.state as string | undefined) || '/'
    window.history.replaceState({}, document.title, destino.startsWith('/') ? destino : '/')
    return modo
  }
  usuarioOidc = await manager.getUser()
  if (usuarioOidc?.expired) {
    try {
      usuarioOidc = await manager.signinSilent()
    } catch {
      usuarioOidc = null
    }
  }
  return modo
}

/** Ir al proveedor a iniciar sesión; al volver, a la misma ruta. */
export async function iniciarSesion(): Promise<void> {
  if (!manager) return
  const actual = window.location.pathname + window.location.search
  await manager.signinRedirect({ state: actual })
}

/** Cerrar sesión en la app y en el proveedor. */
export async function cerrarSesion(): Promise<void> {
  if (!manager) return
  const idToken = usuarioOidc?.id_token
  await manager.removeUser()
  usuarioOidc = null
  await manager.signoutRedirect({ id_token_hint: idToken })
}

/**
 * El backend dijo 401: el token ya no vale. Se intenta renovar una vez; si no, la sesión se
 * da por terminada y la app vuelve a la pantalla de entrada. Devuelve si se renovó.
 */
export async function sesionRechazada(): Promise<boolean> {
  if (modo !== 'oidc' || !manager) return false
  try {
    usuarioOidc = await manager.signinSilent()
    avisar()
    return !!usuarioOidc
  } catch {
    usuarioOidc = null
    await manager.removeUser().catch(() => undefined)
    avisar()
    return false
  }
}

/** Solo tests. */
export function __fijarParaTests(m: ModoAuth, token: string | null): void {
  modo = m
  usuarioOidc = token ? ({ access_token: token, expired: false } as unknown as User) : null
}

// ---------------------------------------------------------------------------
// El usuario de la app (GET /api/v1/auth/yo): nombre, organización y rol
// ---------------------------------------------------------------------------

let usuarioApp: Usuario | null = null

export function usuarioActual(): Usuario | null {
  return usuarioApp
}

export function fijarUsuario(u: Usuario | null): void {
  usuarioApp = u
  avisar()
}
