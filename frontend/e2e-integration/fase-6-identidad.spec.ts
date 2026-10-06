import { test, expect, type Browser, type Page } from '@playwright/test'
import { cabecerasDe, entrar, envDev, oidcActivo, tokenDe } from './identidad'

/**
 * FASE 6 — identidad y organizaciones contra el stack REAL (V3): Keycloak de desarrollo con
 * usuarios reales (ana y beto y carla en «acme» con roles analyst, viewer y admin; diego en «beta»),
 * la API con OIDC, PostGIS con las tablas de `plataforma` y un MCP tabular (almacen-demo).
 *
 *   E6.1 sin sesión → pantalla de entrada → Keycloak → dentro, con nombre, organización y rol.
 *   E6.2 el proyecto y la sesión de Ana NO EXISTEN para Diego (otra organización) ni para Beto.
 *   E6.3 Carla da de alta una conexión con credencial desde el panel → la usa Ana, no Diego; la
 *        credencial no aparece en la página ni en las respuestas.
 *   E6.4 Beto (visor) pide un buffer → denegado con el motivo (rol).
 *   E6.5 la auditoría muestra quién ejecutó y quién aprobó qué; la cadena está íntegra.
 *
 * Correr (stack con OIDC arriba + `npm run dev` en 5173):
 *   E2E_REAL_URL=http://localhost:5173 npx playwright test --config playwright.integration.config.ts fase-6-identidad
 */

const API = process.env.E2E_API_URL ?? 'http://localhost:8000'

async function api(usuario: 'ana' | 'beto' | 'carla' | 'diego', ruta: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${API}/api/v1${ruta}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(await cabecerasDe(usuario)), ...(init.headers ?? {}) },
  })
}

/** Una persona en su propio navegador (sin la sesión de Ana de los demás specs). */
async function navegadorDe(browser: Browser, usuario: 'ana' | 'beto' | 'carla' | 'diego'): Promise<Page> {
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } })
  const page = await ctx.newPage()
  await entrar(page, usuario)
  return page
}

async function turno(page: Page, texto: string): Promise<string> {
  const previos = await page.locator('.msg-assist').count()
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(texto)
  await input.press('Enter')
  const hitl = page.getByRole('button', { name: /Aprobar y ejecutar/ })
  const limite = Date.now() + 240_000
  while (Date.now() < limite) {
    if (await hitl.isVisible().catch(() => false)) await hitl.click()
    const msgs = page.locator('.msg-assist')
    const t = (await msgs.count()) > previos ? ((await msgs.last().textContent()) ?? '') : ''
    if (t.trim() && !(await input.isDisabled()) && !/Procesando/.test(t)) return t
    await page.waitForTimeout(1_000)
  }
  throw new Error(`sin respuesta a «${texto}»`)
}

test.describe('Integración REAL — F6 identidad y organizaciones', () => {
  test.beforeAll(async () => {
    test.skip(!(await oidcActivo()), 'el backend no usa OIDC (OIDC_ISSUER vacío)')
  })

  test('E6.1: sin sesión → pantalla de entrada → Keycloak → dentro con nombre, organización y rol', async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } })
    const page = await ctx.newPage()
    await page.goto('/')
    await expect(page.getByRole('button', { name: /Iniciar sesión/ })).toBeVisible({ timeout: 60_000 })
    await expect(page.locator('.topbar')).toHaveCount(0)
    await entrar(page, 'ana')
    const menu = page.locator('.menu-usuario')
    await expect(menu).toContainText('Ana Analista')
    await expect(menu).toContainText('acme')
    await expect(menu).toContainText('Analista')
    await ctx.close()
  })

  test('E6.2: el proyecto y la sesión de Ana no existen para Diego ni para Beto', async () => {
    const sid = (await (await api('ana', '/session/', { method: 'POST', body: '{}' })).json()).session_id as string
    const guardado = await api('ana', '/proyectos', {
      method: 'POST', body: JSON.stringify({ session_id: sid, nombre: 'Proyecto de Ana (E6.2)', estado: { capas: [] } }),
    })
    expect(guardado.status).toBe(201)
    const pid = (await guardado.json()).id as string
    expect((await (await api('ana', '/proyectos')).json()).proyectos.map((p: { id: string }) => p.id)).toContain(pid)
    for (const otro of ['diego', 'beto'] as const) {
      const lista = (await (await api(otro, '/proyectos')).json()).proyectos.map((p: { id: string }) => p.id)
      expect(lista, `${otro} no ve el proyecto`).not.toContain(pid)
      expect((await api(otro, `/proyectos/${pid}/abrir`, { method: 'POST' })).status, `${otro} abre`).toBe(404)
      expect((await api(otro, `/session/${sid}`)).status, `${otro} lee la sesión`).toBe(404)
      expect((await api(otro, `/session/${sid}/ws-ticket`, { method: 'POST' })).status, `${otro} pide el WS`).toBe(404)
      expect((await api(otro, `/workspace/${sid}/datasets`)).status, `${otro} ve el workspace`).toBe(404)
    }
    expect((await api('ana', `/proyectos/${pid}/abrir`, { method: 'POST' })).status).toBe(200)
    // sin token, nada
    expect((await fetch(`${API}/api/v1/proyectos`)).status).toBe(401)
  })

  test('E6.3: Carla da de alta una conexión con credencial; la usa Ana, no Diego; la credencial no se ve', async ({ browser }) => {
    const credencial = envDev('ALMACEN_MCP_APP_KEY')
    const id = 'almacen_e2e'
    await api('carla', `/connections/${id}`, { method: 'DELETE' })  // de una corrida anterior
    const page = await navegadorDe(browser, 'carla')
    const respuestas: string[] = []
    page.on('response', async (r) => { if (r.url().includes('/api/v1/connections')) respuestas.push(await r.text().catch(() => '')) })
    await page.locator('[title="Conexiones"]').click()
    await page.getByRole('button', { name: 'Añadir conexión' }).click()
    const form = page.getByRole('form', { name: 'Nueva conexión' })
    await form.getByLabel('Identificador').fill(id)
    await form.getByLabel('URL del servidor MCP').fill('http://almacen-demo:9500/mcp')
    await form.getByLabel('Qué contiene (lo lee el agente)').fill('Sedes educativas de Cundinamarca (almacén de acme)')
    await form.getByLabel('Credencial (Bearer)').fill(credencial)
    await form.getByLabel(/Devuelve filas/).check()
    await form.getByRole('button', { name: 'Conectar' }).click()
    const conexion = page.locator(`[data-testid="conexion"][data-server="${id}"]`)
    await expect(conexion).toContainText('de tu organización', { timeout: 60_000 })
    await expect(conexion).toContainText('credencial')
    await expect(conexion.getByTestId('estado-servidor')).toHaveText('Disponible')
    expect(await page.content()).not.toContain(credencial)
    expect(respuestas.join('\n')).not.toContain(credencial)

    const deAna = (await (await api('ana', '/connections/tools')).json()).tools.map((t: { herramienta: string }) => t.herramienta)
    expect(deAna).toContain(`${id}__run_query`)
    const deDiego = (await (await api('diego', '/connections/tools')).json()).tools.map((t: { herramienta: string }) => t.herramienta)
    expect(deDiego).not.toContain(`${id}__run_query`)
    // Ana la usa (la misma capacidad que el agente)
    const sid = (await (await api('ana', '/session/', { method: 'POST', body: '{}' })).json()).session_id
    const run = await api('ana', `/connections/${id}/tools/list_tables/run`, {
      method: 'POST', body: JSON.stringify({ session_id: sid, arguments: {} }),
    })
    expect(run.status).toBe(200)
    expect((await run.json()).success).toBe(true)

    expect((await api('carla', `/connections/${id}`, { method: 'DELETE' })).status).toBe(200)
    await page.context().close()
  })

  test('E6.4 + E6.5: el visor no calcula (lo dice); la auditoría muestra quién hizo y aprobó qué', async ({ browser }) => {
    const beto = await navegadorDe(browser, 'beto')
    const r1 = await turno(beto, 'trae los lotes de la manzana 004503009')
    expect(r1).toMatch(/4\s+lotes/)  // leer, sí
    const r2 = await turno(beto, 'haz un buffer de 500 m a esos lotes')
    console.log(`[E6.4] ${r2}`)
    expect(r2).toMatch(/rol|permis/i)  // calcular, no — y lo dice
    await beto.context().close()

    const tokCarla = await tokenDe('carla')
    const aud = await (await fetch(`${API}/api/v1/auditoria?limite=100`, { headers: { Authorization: `Bearer ${tokCarla}` } })).json()
    type E = { actor_nombre: string; accion: string; resultado: string }
    const deBeto = (aud.entradas as E[]).filter((e) => e.actor_nombre === 'Beto Visor')
    expect(deBeto.some((e) => e.accion === 'consulta')).toBe(true)
    expect(deBeto.some((e) => e.accion === 'hitl.aprobar')).toBe(true)          // aprobó su SQL
    expect(deBeto.some((e) => e.resultado === 'denegado')).toBe(true)           // el buffer
    expect((await (await fetch(`${API}/api/v1/auditoria/verificar`, { headers: { Authorization: `Bearer ${tokCarla}` } })).json()).integra).toBe(true)
    // …y un analista no la ve
    expect((await api('ana', '/auditoria')).status).toBe(403)

    // En la UI de Carla
    const carla = await navegadorDe(browser, 'carla')
    await carla.locator('[title="Auditoría"]').click()
    const panel = carla.locator('.auditoria-panel')
    await expect(panel).toContainText('Registro íntegro')
    await expect(panel.locator('.auditoria-entrada.res-denegado').first()).toContainText('Beto Visor')
    await carla.context().close()
  })
})
