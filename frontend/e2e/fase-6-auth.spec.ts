import { test, expect, type Page } from '@playwright/test'
import { json, mockAuth, mockInit, waitForMap } from './helpers'

/**
 * F6 (V2, determinista) — identidad en la UI: pantalla de entrada (E6.1), quién está conectado,
 * la auditoría solo para administración (E6.5) y el alta de una conexión con credencial que
 * no vuelve a aparecer (E6.3). El backend va mockeado; con usuarios reales de Keycloak está
 * e2e-integration/fase-6-identidad.spec.ts.
 */

const ISSUER = 'http://idp.e2e/realms/geo'
const CLIENTE = 'geo-copilot-web'

/** Una sesión OIDC ya guardada (como la deja oidc-client-ts al volver del proveedor). */
async function conSesionGuardada(page: Page): Promise<void> {
  await page.addInitScript(([iss, cli]) => {
    const ahora = Math.floor(Date.now() / 1000)
    localStorage.setItem(`oidc.user:${iss}:${cli}`, JSON.stringify({
      access_token: 'token-e2e', token_type: 'Bearer', scope: 'openid', expires_at: ahora + 3600,
      profile: { sub: 'u-carla', iss, aud: cli, exp: ahora + 3600, iat: ahora },
    }))
  }, [ISSUER, CLIENTE])
}

const CARLA = { sub: 'u-carla', org_id: 'acme', rol: 'admin', nombre: 'Carla Admin', via: 'oidc' }
const ANA = { ...CARLA, sub: 'u-ana', rol: 'analyst', nombre: 'Ana Analista' }

test.describe('F6 · identidad en la interfaz', () => {
  test('E6.1: con OIDC y sin sesión, la pantalla de entrada; la app no se monta', async ({ page }) => {
    await mockAuth(page, CARLA, { modo: 'oidc', issuer: ISSUER, client_id: CLIENTE })
    const pedidasApp: string[] = []
    await page.route('**/api/v1/session/', (r) => { pedidasApp.push('session'); return json(r, { session_id: 'x' }) })
    await page.goto('/')
    await expect(page.getByRole('button', { name: /Iniciar sesión/ })).toBeVisible()
    await expect(page.getByText('Inicia sesión con la cuenta de tu organización')).toBeVisible()
    await expect(page.locator('.topbar')).toHaveCount(0)
    expect(pedidasApp).toEqual([])  // ni sesión ni WS antes de entrar
  })

  test('con sesión: nombre, organización y rol arriba; el token va en cada petición', async ({ page }) => {
    await conSesionGuardada(page)
    await mockInit(page)
    await mockAuth(page, CARLA, { modo: 'oidc', issuer: ISSUER, client_id: CLIENTE })
    const autorizaciones: (string | undefined)[] = []
    await page.route('**/api/v1/metadata/entities', (r) => {
      autorizaciones.push(r.request().headers()['authorization'])
      return json(r, { entities: [], total: 0 })
    })
    await page.goto('/')
    await waitForMap(page)
    const menu = page.locator('.menu-usuario')
    await expect(menu).toContainText('Carla Admin')
    await expect(menu).toContainText('acme')
    await expect(menu).toContainText('Administración')
    await expect.poll(() => autorizaciones.length).toBeGreaterThan(0)
    expect(autorizaciones.every((a) => a === 'Bearer token-e2e')).toBe(true)
  })

  test('E6.5: la auditoría es solo para administración y dice quién hizo qué', async ({ page }) => {
    await mockInit(page)
    await mockAuth(page, CARLA)
    await page.route('**/api/v1/auditoria/verificar', (r) => json(r, { integra: true, encadenada: true }))
    await page.route('**/api/v1/auditoria?**', (r) => json(r, {
      organizacion: 'acme', siguiente: null, entradas: [
        { id: 3, ts: '2026-09-28T03:00:00Z', actor_sub: 'u-beto', actor_nombre: 'Beto Visor', actor_via: 'oidc',
          accion: 'capacidad.ejecutar', recurso: 'core.ws_buffer', session_id: 's-beto', resultado: 'denegado',
          detalle: { argumentos: { meters: 500 }, rol: 'viewer', rol_requerido: 'analyst' } },
        { id: 2, ts: '2026-09-28T02:59:00Z', actor_sub: 'u-ana', actor_nombre: 'Ana Analista', actor_via: 'oidc',
          accion: 'hitl.aprobar', recurso: 'sql_execution:1', session_id: 's-ana', resultado: 'approved',
          detalle: { titulo: 'Ejecutar Consulta SQL', vista_previa: 'SELECT * FROM lotes' } },
      ],
    }))
    await page.goto('/')
    await waitForMap(page)
    await page.locator('[title="Auditoría"]').click()
    const panel = page.locator('.auditoria-panel')
    await expect(panel).toContainText('Registro íntegro')
    const entradas = panel.locator('.auditoria-entrada')
    await expect(entradas).toHaveCount(2)
    await expect(entradas.nth(0)).toContainText('Beto Visor intentó (denegado)')
    await expect(entradas.nth(0)).toContainText('denegado')
    await expect(entradas.nth(1)).toContainText('Ana Analista aprobó')
    await expect(entradas.nth(1)).toContainText('SELECT * FROM lotes')
  })

  test('un analista no ve la auditoría', async ({ page }) => {
    await mockInit(page)
    await mockAuth(page, ANA)
    await page.goto('/')
    await waitForMap(page)
    await expect(page.locator('[title="Historial"]')).toBeVisible()
    await expect(page.locator('[title="Auditoría"]')).toHaveCount(0)
  })

  test('E6.3: alta de una conexión con credencial; la credencial no se vuelve a ver', async ({ page }) => {
    const SECRETO = 'sk-super-secreto-e2e'
    await mockInit(page)
    await mockAuth(page, CARLA)
    let creada = false
    const cuerpos: Record<string, unknown>[] = []
    await page.route('**/api/v1/connections', async (r) => {
      if (r.request().method() === 'POST') {
        cuerpos.push(r.request().postDataJSON())
        creada = true
        return json(r, { id: 'almacen', url: 'https://almacen.example.org/mcp', tiene_credencial: true,
                         description: null, adapter: 'tabular_geo', trust: 'untrusted', creada_por: 'u-carla',
                         creada: '2026-09-28T03:00:00Z', estado: null }, 201)
      }
      return json(r, {
        puede_administrar: true,
        servers: creada ? [{ id: 'almacen', url: 'https://almacen.example.org/mcp', conformance: 'G0',
                             estado: 'disponible', ultimo_error: null, servidor: null, de: 'organizacion',
                             tools: [{ nombre: 'run_query', herramienta: 'almacen__run_query', habilitada: true,
                                       motivo: null, riesgo: 'read', geo: false }] }] : [],
        de_la_organizacion: creada ? [{ id: 'almacen', url: 'https://almacen.example.org/mcp', tiene_credencial: true,
                                        description: null, adapter: 'tabular_geo', trust: 'untrusted',
                                        creada_por: 'u-carla', creada: '2026-09-28T03:00:00Z' }] : [],
      })
    })
    await page.goto('/')
    await waitForMap(page)
    await page.locator('[title="Conexiones"]').click()
    await page.getByRole('button', { name: 'Añadir conexión' }).click()
    const form = page.getByRole('form', { name: 'Nueva conexión' })
    await form.getByLabel('Identificador').fill('almacen')
    await form.getByLabel('URL del servidor MCP').fill('https://almacen.example.org/mcp')
    await form.getByLabel('Credencial (Bearer)').fill(SECRETO)
    await expect(form.getByLabel('Credencial (Bearer)')).toHaveAttribute('type', 'password')
    await form.getByLabel(/Devuelve filas/).check()
    await form.getByRole('button', { name: 'Conectar' }).click()

    const conexion = page.locator('[data-testid="conexion"][data-server="almacen"]')
    await expect(conexion).toContainText('de tu organización')
    await expect(conexion).toContainText('credencial')
    expect(cuerpos).toEqual([{ id: 'almacen', url: 'https://almacen.example.org/mcp', credencial: SECRETO,
                               adapter: 'tabular_geo' }])
    // el formulario se cerró y la credencial no quedó en ningún lugar de la página
    await expect(page.getByRole('form', { name: 'Nueva conexión' })).toHaveCount(0)
    expect(await page.content()).not.toContain(SECRETO)
  })
})
