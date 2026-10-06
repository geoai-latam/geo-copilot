import { test, expect, type Page } from '@playwright/test'
import { cabecerasDe } from './identidad'

/**
 * FASE 3 — MCP Hub contra el stack REAL (V3).
 *
 * Los servidores del YAML (imagery-mcp y el de ejemplo hello-geo) aparecen en el
 * panel genérico con el formulario de su `input_schema`, y lo que devuelven
 * llega al mapa: la capa vectorial de hello entra al workspace de la sesión.
 * NDVI real por el camino genérico está en real-imagery.spec.ts.
 *
 * Referencia independiente: círculo de 250 m → π·250² = 196 350 m² = 19,63 ha.
 *
 * Correr (stack arriba con `--profile examples`, config/mcp_servers.local.yaml
 * con imagery + hello, y `npm run dev` en 5173):
 *   E2E_REAL_URL=http://localhost:5173 npx playwright test --config playwright.integration.config.ts fase-3-mcp
 */

type Win = any

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

test.describe('Integración REAL — F3 MCP Hub', () => {
  test.setTimeout(120_000)

  test('los servidores del YAML están conectados y sus tools habilitadas', async ({ request }) => {
    const r = await request.get('/api/v1/connections', { headers: await cabecerasDe('ana') })
    expect(r.ok()).toBe(true)
    const servers = (await r.json()).servers as { id: string; estado: string; tools: { nombre: string; habilitada: boolean }[] }[]
    const porId = Object.fromEntries(servers.map((s) => [s.id, s]))
    expect(porId.imagery?.estado).toBe('disponible')
    expect(porId.imagery.tools.map((t) => t.nombre)).toEqual(expect.arrayContaining(
      ['imagery_ndvi', 'imagery_change', 'imagery_zonal_stats', 'imagery_composite', 'imagery_search_scenes']))
    expect(porId.hello?.tools.every((t) => t.habilitada)).toBe(true)
  })

  test('E3.4/E3.2: hello_circle desde el panel genérico entra al mapa con el área correcta', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    await page.locator('[title="Herramientas"]').click()
    const panel = page.locator('[data-testid="mcp-tools-panel"]')
    await expect(panel).toBeVisible({ timeout: 20_000 })

    // El formulario sale del esquema del servidor: lon/lat/meters numéricos, sin selector de capa.
    await panel.getByLabel('Herramienta').selectOption('hello/hello_circle')
    const form = panel.locator('[data-testid="mcp-tool-form"]')
    await expect(form.getByLabel('Lon')).toHaveAttribute('type', 'number')
    await form.getByLabel('Lon').fill('-74.08')
    await form.getByLabel('Lat').fill('4.6')
    await form.getByLabel('Meters').fill('250')
    await panel.getByRole('button', { name: 'Ejecutar' }).click()

    const res = page.locator('[data-testid="mcp-tool-result"]')
    await expect(res).toContainText('Círculo de 250 m', { timeout: 60_000 })
    await res.getByText('Detalles del servicio').click()
    const area = await res.locator('dt', { hasText: /^area_ha$/ }).locator('xpath=following-sibling::dd').textContent()
    expect(Math.abs(parseFloat(area ?? '0') - 19.63)).toBeLessThan(0.05)

    // La capa quedó en el mapa (y en el workspace: lleva su dataset).
    await page.waitForFunction(() => {
      const st = (window as Win).__mlmap.getStyle()
      return Object.keys(st.sources).some((k) => k.startsWith('layer-'))
    }, undefined, { timeout: 20_000 })
  })
})
