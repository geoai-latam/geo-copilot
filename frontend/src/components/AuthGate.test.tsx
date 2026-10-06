/**
 * F6 (E6.1) — la puerta de entrada: con OIDC y sin sesión, pantalla de login (la app no se
 * monta); sin OIDC, la app directamente; con un usuario sin rol, «Sin acceso» con el motivo.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AuthGate } from './AuthGate'
import { __fijarParaTests, cabecerasAuth, fijarUsuario } from '@/lib/auth'
import { pedirTicketWs } from '@/services/websocket'

function respuestas(mapa: Record<string, { status?: number; body: unknown }>) {
  return vi.fn(async (url: string) => {
    const r = mapa[url] ?? { status: 404, body: {} }
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  __fijarParaTests('ninguno', null)
  fijarUsuario(null)
  localStorage.clear()
})

describe('AuthGate', () => {
  it('con OIDC y sin sesión muestra la pantalla de entrada y NO monta la app', async () => {
    vi.stubGlobal('fetch', respuestas({
      '/api/v1/auth/config': { body: { modo: 'oidc', issuer: 'http://idp.test/realms/geo', client_id: 'web' } },
    }))
    render(<AuthGate><div>la app</div></AuthGate>)
    expect(await screen.findByRole('button', { name: /Iniciar sesión/ })).toBeTruthy()
    expect(screen.queryByText('la app')).toBeNull()
  })

  it('sin OIDC (desarrollo) la app entra directamente', async () => {
    vi.stubGlobal('fetch', respuestas({
      '/api/v1/auth/config': { body: { modo: 'ninguno' } },
      '/api/v1/auth/yo': { body: { sub: 'dev', org_id: 'dev', rol: 'admin', nombre: 'Desarrollo', via: 'dev' } },
    }))
    render(<AuthGate><div>la app</div></AuthGate>)
    expect(await screen.findByText('la app')).toBeTruthy()
  })

  it('un usuario rechazado por el backend ve el motivo y puede salir', async () => {
    vi.stubGlobal('fetch', respuestas({
      '/api/v1/auth/config': { body: { modo: 'ninguno' } },
      '/api/v1/auth/yo': { status: 403, body: { detail: 'Tu usuario no tiene un rol en GEO Copilot.' } },
    }))
    render(<AuthGate><div>la app</div></AuthGate>)
    expect((await screen.findByRole('alert')).textContent).toContain('no tiene un rol')
    expect(screen.getByRole('button', { name: /Salir/ })).toBeTruthy()
    expect(screen.queryByText('la app')).toBeNull()
  })
})

describe('credenciales en las peticiones', () => {
  it('sin sesión no hay cabecera; con sesión, Bearer', () => {
    expect(cabecerasAuth()).toEqual({})
    __fijarParaTests('oidc', 'tok-123')
    expect(cabecerasAuth()).toEqual({ Authorization: 'Bearer tok-123' })
  })

  it('el ticket del WS se pide con el token y para esa sesión', async () => {
    __fijarParaTests('oidc', 'tok-123')
    const f = vi.fn(async () => new Response(JSON.stringify({ ticket: 'tk', expira_en_s: 30 }), { status: 200 }))
    expect(await pedirTicketWs('ses 1', f as unknown as typeof fetch)).toBe('tk')
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/v1/session/ses%201/ws-ticket')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')
  })

  it('si no se consigue ticket (sesión ajena, backend caído) no se inventa uno', async () => {
    const f = vi.fn(async () => new Response('{}', { status: 404 }))
    expect(await pedirTicketWs('s', f as unknown as typeof fetch)).toBeNull()
    const caido = vi.fn(async () => { throw new Error('red') })
    expect(await pedirTicketWs('s', caido as unknown as typeof fetch)).toBeNull()
  })
})

describe('V5: el backend no responde al arrancar', () => {
  it('no muestra la app «sin login»: lo dice y ofrece reintentar', async () => {
    const { iniciarAuth } = await import('@/lib/auth')
    const caido = vi.fn(async () => new Response('Bad Gateway', { status: 502 }))
    expect(await iniciarAuth(caido as unknown as typeof fetch, [0, 0])).toBe('sin_servidor')
    expect(caido).toHaveBeenCalledTimes(3)  // 1 + 2 reintentos
  })

  it('si vuelve durante los reintentos, sigue con normalidad', async () => {
    const { iniciarAuth } = await import('@/lib/auth')
    let n = 0
    const vuelve = vi.fn(async () => (++n < 3
      ? new Response('Bad Gateway', { status: 502 })
      : new Response(JSON.stringify({ modo: 'ninguno' }), { status: 200 })))
    expect(await iniciarAuth(vuelve as unknown as typeof fetch, [0, 0, 0])).toBe('ninguno')
  })

  it('un 404 sí es una respuesta: backend sin la ruta de identidad, sin login', async () => {
    const { iniciarAuth } = await import('@/lib/auth')
    const viejo = vi.fn(async () => new Response('{}', { status: 404 }))
    expect(await iniciarAuth(viejo as unknown as typeof fetch, [0])).toBe('ninguno')
    expect(viejo).toHaveBeenCalledTimes(1)
  })

  it('la pantalla «sin conexión» reintenta al pulsar el botón', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fallos = vi.fn(async () => new Response('Bad Gateway', { status: 502 }))
    vi.stubGlobal('fetch', fallos)
    render(<AuthGate><div>la app</div></AuthGate>)
    await vi.advanceTimersByTimeAsync(20_000)
    expect((await screen.findByRole('alert')).textContent).toContain('No se pudo contactar con el servidor')
    expect(screen.queryByText('la app')).toBeNull()
    vi.stubGlobal('fetch', respuestas({
      '/api/v1/auth/config': { body: { modo: 'ninguno' } },
      '/api/v1/auth/yo': { body: { sub: 'dev', org_id: 'dev', rol: 'admin', nombre: 'Desarrollo', via: 'dev' } },
    }))
    screen.getByRole('button', { name: /Reintentar/ }).click()
    expect(await screen.findByText('la app')).toBeTruthy()
    vi.useRealTimers()
  })
})
