import { test, expect } from '@playwright/test'
import { json, mockInit, waitForMap } from './helpers'

/**
 * F4 (V2) — E4.4: panel de Conexiones. Estado, nivel y herramientas de cada
 * servidor MCP; una tool deshabilitada por el pinning se revisa y se re-aprueba;
 * «Probar» abre su formulario.
 */

const servidores = (aprobada: boolean) => [
  {
    id: 'imagery', url: 'http://imagery-mcp:9100/mcp', conformance: 'G2', estado: 'disponible', ultimo_error: null,
    servidor: { name: 'geo-imagery', version: '1.30.0' },
    tools: [
      { nombre: 'imagery_ndvi', herramienta: 'imagery__imagery_ndvi', habilitada: true, motivo: null, riesgo: 'compute', geo: true },
      { nombre: 'imagery_composite', herramienta: 'imagery__imagery_composite', habilitada: aprobada,
        motivo: aprobada ? null : 'su descripción cambió desde la última aprobación', riesgo: 'read', geo: true },
    ],
  },
  {
    id: 'hello', url: 'http://hello-geo:9200/mcp', conformance: 'G1', estado: 'no_disponible',
    ultimo_error: 'ConnectError: conexión rechazada', servidor: null,
    tools: [{ nombre: 'hello_circle', herramienta: 'hello__hello_circle', habilitada: true, motivo: null, riesgo: 'read', geo: false }],
  },
]

const TOOLS = [
  { server: 'imagery', tool: 'imagery_ndvi', herramienta: 'imagery__imagery_ndvi', description: 'NDVI de una zona.',
    riesgo: 'compute', estado: 'disponible', geo: { inputs: {} },
    input_schema: { type: 'object', properties: { date_from: { type: 'string', title: 'Date From' } } } },
  { server: 'hello', tool: 'hello_circle', herramienta: 'hello__hello_circle', description: 'Círculo.',
    riesgo: 'read', estado: 'no_disponible', geo: { inputs: {} },
    input_schema: { type: 'object', properties: { meters: { type: 'number', title: 'Meters' } } } },
]

test.describe('F4 · panel de Conexiones (E4.4)', () => {
  test('estado, nivel y tools; re-aprobar una tool cambiada; probar una desde su formulario', async ({ page }) => {
    await mockInit(page)
    let aprobada = false
    const aprobaciones: string[] = []
    // F6: re-aprobar es de administración (el usuario del E2E lo es)
    await page.route('**/api/v1/connections', (r) => json(r, { servers: servidores(aprobada), de_la_organizacion: [], puede_administrar: true }))
    await page.route('**/api/v1/connections/tools', (r) => json(r, { tools: TOOLS }))
    await page.route('**/api/v1/connections/*/tools/*/approve', (r) => {
      aprobaciones.push(new URL(r.request().url()).pathname)
      aprobada = true
      return json(r, { ok: true })
    })

    await page.goto('/')
    await waitForMap(page)
    await page.locator('[title="Conexiones"]').click()

    const imagery = page.locator('[data-testid="conexion"][data-server="imagery"]')
    const hello = page.locator('[data-testid="conexion"][data-server="hello"]')
    await expect(imagery.getByTestId('estado-servidor')).toHaveText('Disponible')
    await expect(imagery.getByTestId('nivel-servidor')).toHaveText('G2')
    await expect(imagery).toContainText('teselas')                 // qué significa G2
    await expect(imagery).toContainText('2 herramientas · 1 deshabilitada')
    await expect(hello.getByTestId('estado-servidor')).toHaveText('No disponible')
    await expect(hello).toContainText('conexión rechazada')        // el último error, a la vista

    // La tool cambiada: se ve por qué está deshabilitada y se re-aprueba.
    const composite = imagery.locator('[data-testid="conexion-tool"][data-tool="imagery_composite"]')
    await expect(composite.getByTestId('motivo-deshabilitada')).toContainText('su descripción cambió')
    await composite.getByRole('button', { name: 'Aprobar imagery_composite' }).click()
    await expect.poll(() => aprobaciones).toEqual(['/api/v1/connections/imagery/tools/imagery_composite/approve'])
    await expect(composite.getByRole('button', { name: 'Probar imagery_composite' })).toBeVisible()
    await expect(imagery).toContainText('2 herramientas')
    await expect(imagery).not.toContainText('deshabilitada')

    // Probar abre el formulario de ESA tool (no la primera de la lista).
    await hello.getByRole('button', { name: 'Probar hello_circle' }).click()
    const panel = page.locator('[data-testid="mcp-tools-panel"]')
    await expect(panel).toBeVisible()
    await expect(panel.getByLabel('Herramienta')).toHaveValue('hello/hello_circle')
    await expect(panel.getByLabel('Meters')).toBeVisible()
  })
})
